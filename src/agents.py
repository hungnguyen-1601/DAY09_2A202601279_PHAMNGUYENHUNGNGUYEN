"""Cac agent trong he thong.

- OrderSellerAgent / PaymentAgent / DeliveryAgent (LLM): moi agent chi nhin
  mot domain du lieu, doc fact sheet tu tool va tra ve finding JSON.
- PolicyAgent (LLM): nhan handoff findings tu 3 specialist, ap EC_POLICY_V1
  va de xuat ket luan.
- VerifierAgent (deterministic): tinh lai toan bo tu CSV bang policy_engine,
  doi chieu voi de xuat cua PolicyAgent, sua moi sai lech truoc khi ghi file.
"""
from . import llm_client, policy_engine

ORDER_SELLER_SYSTEM = (
    "You are the Order & Seller Agent of an e-commerce dispute team. "
    "Input: order status, items with seller handoff check (carrier_after_limit "
    "means the seller handed the parcel to the carrier AFTER its shipping "
    "limit). Reply ONLY JSON: "
    '{"order_status": str, "sellers_past_limit": [seller_id], "finding": short str}'
)

PAYMENT_SYSTEM = (
    "You are the Payment Agent. Input: payment rows and totals of one order. "
    "payment_matches_order_value means |payment_total - (item_total + "
    "freight_total)| <= 0.10 BRL. Reply ONLY JSON: "
    '{"n_payments": int, "payment_total": number, "split_payment": bool, '
    '"matches_order_value": bool, "finding": short str}'
)

DELIVERY_SYSTEM = (
    "You are the Delivery Agent. Input: delivery timestamps of one order. "
    "Reply ONLY JSON: "
    '{"delivered": bool, "delivered_after_estimate": bool, "finding": short str}'
)

POLICY_SYSTEM = (
    "You are the Policy Agent applying EC_POLICY_V1. Given findings from the "
    "Order&Seller, Payment and Delivery agents, pick the FIRST matching rule:\n"
    "1. status canceled & payment_total>0 -> canceled_order_paid\n"
    "2. status unavailable & payment_total>0 -> unavailable_order_paid\n"
    "3. delivered_after_estimate & sellers_past_limit not empty -> late_delivery_seller\n"
    "4. delivered_after_estimate & sellers_past_limit empty -> late_delivery_logistics\n"
    "5. n_payments>=2 & matches_order_value -> valid_split_payment\n"
    "6. not delivered_after_estimate & matches_order_value -> unsupported_late_claim\n"
    "Refund: rule 1-2 payment_total; rule 3-4 freight_total; rule 5-6 zero. "
    "Reply ONLY JSON: "
    '{"primary_issue": str, "recommended_refund_brl": number, "reason": short str}'
)


def order_seller_agent(tracer, case_id, facts):
    payload = {
        "order_id": facts["order_id"],
        "order_status": facts["order_status"],
        "items": [
            {
                "order_item_id": it["order_item_id"],
                "seller_id": it["seller_id"],
                "shipping_limit_date": it["shipping_limit_date"],
                "carrier_after_limit": it["carrier_after_limit"],
            }
            for it in facts["items"]
        ],
        "carrier_pickup_ts": facts["carrier_ts"],
    }
    finding = llm_client.chat_json(
        tracer, case_id, "order_seller_agent", ORDER_SELLER_SYSTEM, payload
    )
    tracer.log(case_id, "order_seller_agent", "finding", finding=finding)
    return finding


def payment_agent(tracer, case_id, facts):
    payload = {
        "order_id": facts["order_id"],
        "payments": facts["payments"],
        "item_total": facts["item_total"],
        "freight_total": facts["freight_total"],
        "payment_total": facts["payment_total"],
        "payment_matches_order_value": facts["payment_matches_order_value"],
    }
    finding = llm_client.chat_json(
        tracer, case_id, "payment_agent", PAYMENT_SYSTEM, payload
    )
    tracer.log(case_id, "payment_agent", "finding", finding=finding)
    return finding


def delivery_agent(tracer, case_id, facts):
    payload = {
        "order_id": facts["order_id"],
        "delivered_ts": facts["delivered_ts"],
        "estimated_ts": facts["estimated_ts"],
        "delivered_after_estimate": facts["delivered_after_estimate"],
    }
    finding = llm_client.chat_json(
        tracer, case_id, "delivery_agent", DELIVERY_SYSTEM, payload
    )
    tracer.log(case_id, "delivery_agent", "finding", finding=finding)
    return finding


def policy_agent(tracer, case_id, facts, findings):
    payload = {
        "order_status": facts["order_status"],
        "payment_total": facts["payment_total"],
        "freight_total": facts["freight_total"],
        "n_payments": facts["n_payments"],
        "delivered_after_estimate": facts["delivered_after_estimate"],
        "sellers_past_limit": facts["sellers_past_limit"],
        "matches_order_value": facts["payment_matches_order_value"],
        "specialist_findings": findings,
    }
    proposal = llm_client.chat_json(
        tracer, case_id, "policy_agent", POLICY_SYSTEM, payload, max_tokens=200
    )
    tracer.log(case_id, "policy_agent", "proposal", proposal=proposal)
    return proposal


def verifier_agent(tracer, case_id, facts, proposal):
    """Kiem chung de xuat cua PolicyAgent bang policy engine deterministic.

    Output cuoi cung luon la ket qua tinh truc tiep tu CSV; moi sai lech cua
    LLM duoc ghi vao trace duoi dang correction.
    """
    decision = policy_engine.decide(facts)
    corrections = []
    if proposal is None:
        corrections.append("policy_agent_no_valid_json")
    else:
        if proposal.get("primary_issue") != decision["primary_issue"]:
            corrections.append(
                f"primary_issue: {proposal.get('primary_issue')} -> {decision['primary_issue']}"
            )
        try:
            llm_refund = round(float(proposal.get("recommended_refund_brl")), 2)
        except (TypeError, ValueError):
            llm_refund = None
        if llm_refund != decision["recommended_refund_brl"]:
            corrections.append(
                f"refund: {llm_refund} -> {decision['recommended_refund_brl']}"
            )
    tracer.log(
        case_id,
        "verifier_agent",
        "verification",
        agrees=not corrections,
        corrections=corrections,
        final_primary_issue=decision["primary_issue"],
        final_refund_brl=decision["recommended_refund_brl"],
    )
    return decision
