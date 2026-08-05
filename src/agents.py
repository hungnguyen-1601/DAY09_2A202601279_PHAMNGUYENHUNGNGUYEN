"""Cac agent trong he thong.

- OrderSellerAgent / PaymentAgent / DeliveryAgent (LLM): moi agent chi nhin
  mot domain du lieu, doc fact sheet tu tool va tra ve finding JSON.
- PolicyAgent (LLM): nhan handoff findings tu 3 specialist, ap EC_POLICY_V1
  va de xuat ket luan.
- VerifierAgent (deterministic): tinh lai toan bo tu CSV, dung output cuoi,
  kiem schema/entity/evidence/so tien/policy va chi tra output khi tat ca hop le.
"""
import math

from . import handoffs, llm_client, output_validator, policy_engine

ORDER_SELLER_SYSTEM = (
    "You are the Order & Seller Agent of an e-commerce dispute team. "
    "The coordinator validated the handoff contract; use only the assigned "
    "domain facts below. carrier_after_limit "
    "means the seller handed the parcel to the carrier AFTER its shipping "
    "limit). Reply ONLY JSON: "
    '{"order_status": str, "sellers_past_limit": [seller_id], "finding": short str}'
)

PAYMENT_SYSTEM = (
    "You are the Payment Agent. The coordinator validated the handoff contract; "
    "use only the payment rows and totals below. "
    "payment_matches_order_value means |payment_total - (item_total + "
    "freight_total)| <= 0.10 BRL. Reply ONLY JSON: "
    '{"n_payments": int, "payment_total": number, "split_payment": bool, '
    '"matches_order_value": bool, "finding": short str}'
)

DELIVERY_SYSTEM = (
    "You are the Delivery Agent. The coordinator validated the handoff contract; "
    "use only the delivery timestamps below. "
    "Reply ONLY JSON: "
    '{"delivered": bool, "delivered_after_estimate": bool|null, "finding": short str}'
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


def _validate_received_handoff(handoff, recipient):
    errors = handoffs.validate_handoff(handoff, expected_recipient=recipient)
    if errors:
        raise ValueError("Agent received invalid handoff: " + "; ".join(errors))


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _order_response_error(value):
    if not isinstance(value, dict):
        return "Order & Seller response must be an object"
    if not isinstance(value.get("order_status"), str):
        return "order_status must be a string"
    sellers = value.get("sellers_past_limit")
    if not isinstance(sellers, list) or any(not isinstance(x, str) for x in sellers):
        return "sellers_past_limit must be a string list"
    if value.get("finding") is not None and not isinstance(value.get("finding"), str):
        return "finding must be a string or null"
    return None


def _payment_response_error(value):
    if not isinstance(value, dict):
        return "Payment response must be an object"
    if not isinstance(value.get("n_payments"), int) or isinstance(
        value.get("n_payments"), bool
    ):
        return "n_payments must be an integer"
    if not _is_number(value.get("payment_total")):
        return "payment_total must be a finite number"
    if not isinstance(value.get("split_payment"), bool):
        return "split_payment must be boolean"
    if not isinstance(value.get("matches_order_value"), bool):
        return "matches_order_value must be boolean"
    if value.get("finding") is not None and not isinstance(value.get("finding"), str):
        return "finding must be a string or null"
    return None


def _delivery_response_error(value):
    if not isinstance(value, dict):
        return "Delivery response must be an object"
    if not isinstance(value.get("delivered"), bool):
        return "delivered must be boolean"
    if value.get("delivered_after_estimate") is not None and not isinstance(
        value.get("delivered_after_estimate"), bool
    ):
        return "delivered_after_estimate must be boolean or null"
    if value.get("finding") is not None and not isinstance(value.get("finding"), str):
        return "finding must be a string or null"
    return None


def _policy_response_error(value):
    if not isinstance(value, dict):
        return "Policy response must be an object"
    if not isinstance(value.get("primary_issue"), str):
        return "primary_issue must be a string"
    if not _is_number(value.get("recommended_refund_brl")):
        return "recommended_refund_brl must be a finite number"
    if not isinstance(value.get("reason"), str):
        return "reason must be a string"
    return None


def _require_response(response, agent_name):
    if response is None:
        raise RuntimeError(f"{agent_name} failed to return valid JSON after retries")
    if response.get("finding") is None:
        response["finding"] = ""
    return response


def order_seller_agent(tracer, case_id, facts, handoff):
    _validate_received_handoff(handoff, "order_seller_agent")
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
        tracer,
        case_id,
        "order_seller_agent",
        ORDER_SELLER_SYSTEM,
        payload,
        response_validator=_order_response_error,
    )
    finding = _require_response(finding, "order_seller_agent")
    tracer.log(case_id, "order_seller_agent", "finding", finding=finding)
    return finding


def payment_agent(tracer, case_id, facts, handoff):
    _validate_received_handoff(handoff, "payment_agent")
    payload = {
        "order_id": facts["order_id"],
        "payments": facts["payments"],
        "item_total": facts["item_total"],
        "freight_total": facts["freight_total"],
        "payment_total": facts["payment_total"],
        "payment_matches_order_value": facts["payment_matches_order_value"],
    }
    finding = llm_client.chat_json(
        tracer,
        case_id,
        "payment_agent",
        PAYMENT_SYSTEM,
        payload,
        response_validator=_payment_response_error,
    )
    finding = _require_response(finding, "payment_agent")
    tracer.log(case_id, "payment_agent", "finding", finding=finding)
    return finding


def delivery_agent(tracer, case_id, facts, handoff):
    _validate_received_handoff(handoff, "delivery_agent")
    payload = {
        "order_id": facts["order_id"],
        "delivered_ts": facts["delivered_ts"],
        "estimated_ts": facts["estimated_ts"],
        "delivered_after_estimate": facts["delivered_after_estimate"],
    }
    finding = llm_client.chat_json(
        tracer,
        case_id,
        "delivery_agent",
        DELIVERY_SYSTEM,
        payload,
        response_validator=_delivery_response_error,
    )
    finding = _require_response(finding, "delivery_agent")
    tracer.log(case_id, "delivery_agent", "finding", finding=finding)
    return finding


def policy_agent(tracer, case_id, facts, findings, handoff):
    _validate_received_handoff(handoff, "policy_agent")
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
        tracer,
        case_id,
        "policy_agent",
        POLICY_SYSTEM,
        payload,
        max_tokens=200,
        response_validator=_policy_response_error,
    )
    if proposal is None:
        raise RuntimeError("policy_agent failed to return valid JSON after retries")
    tracer.log(case_id, "policy_agent", "proposal", proposal=proposal)
    return proposal


def verifier_agent(tracer, case, facts, proposal, handoff):
    """Dung va kiem tra full output; fail-closed neu bat ky check nao sai."""
    case_id = case["case_id"]
    _validate_received_handoff(handoff, "verifier_agent")
    try:
        decision = policy_engine.decide(facts)
    except (KeyError, TypeError, ValueError) as exc:
        tracer.log(
            case_id,
            "verifier_agent",
            "verification_failed",
            proposal=proposal,
            corrections=[],
            validation_errors=[str(exc)],
        )
        raise
    corrections = []
    if not isinstance(proposal, dict):
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
        if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
            corrections.append("reason: missing or empty")

    output = policy_engine.build_output(case_id, facts, decision)
    validation_errors = output_validator.validate_final_output(case, facts, output)
    if validation_errors:
        tracer.log(
            case_id,
            "verifier_agent",
            "verification_failed",
            proposal=proposal,
            corrections=corrections,
            validation_errors=validation_errors,
        )
        raise ValueError(
            f"{case_id} final output failed verification: "
            + "; ".join(validation_errors)
        )

    tracer.log(
        case_id,
        "verifier_agent",
        "verification",
        proposal_agrees=not corrections,
        corrections=corrections,
        output_valid=True,
        validation_errors=[],
        checks={
            "schema": True,
            "policy": True,
            "entities": True,
            "evidence": True,
            "financials": True,
            "actions": True,
        },
        policy_version=case["policy_version"],
        policy_evidence_id=f"policy:{decision['root_cause']}",
        proposal=proposal,
        final_decision=decision,
        final_output=output,
    )
    return output
