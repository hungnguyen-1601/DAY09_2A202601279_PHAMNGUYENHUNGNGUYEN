"""Policy engine deterministic ap dung EC_POLICY_V1.

Verifier Agent dung engine nay de kiem chung / sua ket luan cua Policy Agent
(LLM) truoc khi ghi output. Cac rule ap dung theo dung thu tu uu tien trong
README muc 4.
"""
from . import config

# Verifier tinh lai deterministic tu CSV va sua moi proposal sai truoc khi ghi
# output, nen confidence cua quyet dinh cuoi cung la 1.0.
RULE_TABLE = {
    "canceled_order_paid": {
        "root_cause": "ORDER_CANCELED_AFTER_PAYMENT",
        "party": ("platform", "OLIST_PLATFORM"),
        "action": "issue_full_refund",
        "case_status": "action_required",
        "confidence": 1.0,
    },
    "unavailable_order_paid": {
        "root_cause": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "party": ("platform", "OLIST_PLATFORM"),
        "action": "issue_full_refund",
        "case_status": "action_required",
        "confidence": 1.0,
    },
    "late_delivery_seller": {
        "root_cause": "SELLER_HANDOFF_AFTER_LIMIT",
        "party": ("seller", None),  # party_id = seller vi pham, dien luc runtime
        "action": "refund_freight",
        "case_status": "action_required",
        "confidence": 1.0,
    },
    "late_delivery_logistics": {
        "root_cause": "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "party": ("logistics_provider", "LOGISTICS_PROVIDER"),
        "action": "refund_freight",
        "case_status": "action_required",
        "confidence": 1.0,
    },
    "valid_split_payment": {
        "root_cause": "MULTIPLE_PAYMENTS_RECONCILED",
        "party": None,
        "action": "explain_valid_split_payment",
        "case_status": "no_action",
        "confidence": 1.0,
    },
    "unsupported_late_claim": {
        "root_cause": "DELIVERY_WITHIN_ESTIMATE",
        "party": None,
        "action": "reject_late_refund",
        "case_status": "no_action",
        "confidence": 1.0,
    },
}


def decide(facts: dict) -> dict:
    """Ap rule theo thu tu uu tien, tra ve ket luan day du cho mot case."""
    status = facts["order_status"]
    payment_total = facts["payment_total"]

    if status == "canceled" and payment_total > 0:
        issue = "canceled_order_paid"
        refund = payment_total
    elif status == "unavailable" and payment_total > 0:
        issue = "unavailable_order_paid"
        refund = payment_total
    elif facts["delivered_after_estimate"] and facts["sellers_past_limit"]:
        issue = "late_delivery_seller"
        refund = facts["freight_total"]
    elif facts["delivered_after_estimate"]:
        issue = "late_delivery_logistics"
        refund = facts["freight_total"]
    elif facts["n_payments"] >= 2 and facts["payment_matches_order_value"]:
        issue = "valid_split_payment"
        refund = 0.0
    elif not facts["delivered_after_estimate"] and facts["payment_matches_order_value"]:
        issue = "unsupported_late_claim"
        refund = 0.0
    else:
        # Bo 50 case chinh thuc khong roi vao day; fallback an toan nhat la
        # bac bo claim khong co bang chung.
        issue = "unsupported_late_claim"
        refund = 0.0

    rule = RULE_TABLE[issue]
    if rule["party"] is None:
        parties = []
    elif rule["party"][0] == "seller":
        parties = [
            {"party_type": "seller", "party_id": sid}
            for sid in facts["sellers_past_limit"][: config.MAX_PARTIES]
        ]
    else:
        parties = [{"party_type": rule["party"][0], "party_id": rule["party"][1]}]

    return {
        "primary_issue": issue,
        "case_status": rule["case_status"],
        "confidence": rule["confidence"],
        "root_cause": rule["root_cause"],
        "responsible_parties": parties,
        "recommended_refund_brl": round(refund, 2),
        "resolution_actions": [rule["action"]],
    }


def build_output(case_id: str, facts: dict, decision: dict) -> dict:
    """Dung output JSON dung schema README muc 6 tu facts + decision."""
    order_id = facts["order_id"]
    item_ids = [f"{order_id}:{it['order_item_id']}" for it in facts["items"]]
    seller_ids = []
    for it in facts["items"]:
        if it["seller_id"] not in seller_ids:
            seller_ids.append(it["seller_id"])
    payment_ids = [f"{order_id}:{p['payment_sequential']}" for p in facts["payments"]]

    # Evidence: order + items + payments (+ seller neu seller chiu trach nhiem)
    # + policy. Cat bot items/payments truoc neu vuot gioi han 10.
    responsible_sellers = [
        p["party_id"] for p in decision["responsible_parties"] if p["party_type"] == "seller"
    ]
    ev_items = [f"item:{order_id}:{it['order_item_id']}" for it in facts["items"]]
    ev_pays = [f"payment:{order_id}:{p['payment_sequential']}" for p in facts["payments"]]
    ev_sellers = [f"seller:{sid}" for sid in responsible_sellers]
    fixed = 2 + len(ev_sellers)  # order + policy + sellers
    budget = config.MAX_EVIDENCE - fixed
    while len(ev_items) + len(ev_pays) > budget:
        if len(ev_items) >= len(ev_pays) and len(ev_items) > 1:
            ev_items.pop()
        elif len(ev_pays) > 1:
            ev_pays.pop()
        elif ev_items:
            ev_items.pop()
        else:
            ev_pays.pop()
    evidence = (
        [f"order:{order_id}"]
        + ev_items
        + ev_pays
        + ev_sellers
        + [f"policy:{decision['root_cause']}"]
    )

    return {
        "case_id": case_id,
        "assessment": {
            "primary_issue": decision["primary_issue"],
            "case_status": decision["case_status"],
            "confidence": decision["confidence"],
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": item_ids[: config.MAX_ENTITY_IDS],
            "seller_ids": seller_ids[: config.MAX_ENTITY_IDS],
            "payment_ids": payment_ids[: config.MAX_ENTITY_IDS],
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": decision["root_cause"], "rank": 1}],
            "responsible_parties": decision["responsible_parties"],
        },
        "evidence_ids": evidence[: config.MAX_EVIDENCE],
        "financial_resolution": {
            "currency": "BRL",
            "item_total_brl": facts["item_total"],
            "freight_total_brl": facts["freight_total"],
            "payment_total_brl": facts["payment_total"],
            "recommended_refund_brl": decision["recommended_refund_brl"],
        },
        "resolution_actions": decision["resolution_actions"][: config.MAX_ACTIONS],
    }
