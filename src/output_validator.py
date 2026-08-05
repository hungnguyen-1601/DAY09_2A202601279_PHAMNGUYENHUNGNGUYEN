"""Kiem tra output cuoi day du truoc khi cho phep ghi file.

Validator nay nam trong Verifier Agent va fail-closed: schema, policy, entity,
evidence hoac so tien sai thi case khong duoc publish vao output/.
"""
import json
import math

from . import config, policy_engine

TOP_LEVEL_KEYS = {
    "case_id",
    "assessment",
    "affected_entities",
    "root_cause_analysis",
    "evidence_ids",
    "financial_resolution",
    "resolution_actions",
}
NESTED_KEYS = {
    "assessment": {"primary_issue", "case_status", "confidence"},
    "affected_entities": {"order_ids", "item_ids", "seller_ids", "payment_ids"},
    "root_cause_analysis": {"ranked_causes", "responsible_parties"},
    "financial_resolution": {
        "currency",
        "item_total_brl",
        "freight_total_brl",
        "payment_total_brl",
        "recommended_refund_brl",
    },
}


def _is_finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _unique(values) -> bool:
    if not isinstance(values, list):
        return False
    return all(value not in values[:index] for index, value in enumerate(values))


def _money_matches(actual, expected) -> bool:
    return _is_finite_number(actual) and abs(float(actual) - float(expected)) <= 0.005


def validate_final_output(case: dict, facts: dict, output: dict) -> list[str]:
    """Tra ve tat ca loi tim duoc; [] nghia la output duoc phep publish."""
    errors = []
    case_id = case.get("case_id")

    if case.get("policy_version") != config.POLICY_VERSION:
        errors.append(
            f"policy_version {case.get('policy_version')!r} != {config.POLICY_VERSION!r}"
        )
    claimed_order_id = case.get("customer_request", {}).get("claimed_order_id")
    if claimed_order_id != facts.get("order_id"):
        errors.append("claimed_order_id does not match fact sheet")

    if not isinstance(output, dict):
        return errors + ["output must be an object"]
    if set(output) != TOP_LEVEL_KEYS:
        errors.append(
            f"top-level keys mismatch: missing={sorted(TOP_LEVEL_KEYS - set(output))}, "
            f"extra={sorted(set(output) - TOP_LEVEL_KEYS)}"
        )
    if output.get("case_id") != case_id:
        errors.append("case_id does not match input")

    for field, expected_keys in NESTED_KEYS.items():
        value = output.get(field)
        if not isinstance(value, dict):
            errors.append(f"{field} must be an object")
        elif set(value) != expected_keys:
            errors.append(
                f"{field} keys mismatch: missing={sorted(expected_keys - set(value))}, "
                f"extra={sorted(set(value) - expected_keys)}"
            )

    assessment = output.get("assessment", {})
    if not isinstance(assessment, dict):
        assessment = {}
    confidence = assessment.get("confidence")
    if not _is_finite_number(confidence) or not 0 <= float(confidence) <= 1:
        errors.append("assessment.confidence must be a finite number in [0, 1]")

    expected_decision = policy_engine.decide(facts)
    if assessment.get("primary_issue") != expected_decision["primary_issue"]:
        errors.append("assessment.primary_issue does not match EC_POLICY_V1")
    if assessment.get("case_status") != expected_decision["case_status"]:
        errors.append("assessment.case_status does not match EC_POLICY_V1")
    if confidence != expected_decision["confidence"]:
        errors.append("assessment.confidence does not match verified decision")

    order_id = facts["order_id"]
    expected_items = [
        f"{order_id}:{item['order_item_id']}" for item in facts["items"]
    ][: config.MAX_ENTITY_IDS]
    expected_sellers = []
    for item in facts["items"]:
        if item["seller_id"] not in expected_sellers:
            expected_sellers.append(item["seller_id"])
    expected_sellers = expected_sellers[: config.MAX_ENTITY_IDS]
    expected_payments = [
        f"{order_id}:{payment['payment_sequential']}"
        for payment in facts["payments"]
    ][: config.MAX_ENTITY_IDS]
    expected_entities = {
        "order_ids": [order_id],
        "item_ids": expected_items,
        "seller_ids": expected_sellers,
        "payment_ids": expected_payments,
    }
    entities = output.get("affected_entities", {})
    if not isinstance(entities, dict):
        entities = {}
    for field, expected in expected_entities.items():
        actual = entities.get(field)
        if not isinstance(actual, list):
            errors.append(f"affected_entities.{field} must be a list")
        else:
            if len(actual) > config.MAX_ENTITY_IDS:
                errors.append(f"affected_entities.{field} exceeds limit")
            if not _unique(actual):
                errors.append(f"affected_entities.{field} contains duplicates")
            if actual != expected:
                errors.append(f"affected_entities.{field} does not match source data")

    root = output.get("root_cause_analysis", {})
    if not isinstance(root, dict):
        root = {}
    expected_causes = [
        {"cause_code": expected_decision["root_cause"], "rank": 1}
    ]
    causes = root.get("ranked_causes")
    if causes != expected_causes:
        errors.append("root_cause_analysis.ranked_causes does not match policy")
    if isinstance(causes, list) and len(causes) > config.MAX_ROOT_CAUSES:
        errors.append("root_cause_analysis.ranked_causes exceeds limit")
    parties = root.get("responsible_parties")
    if parties != expected_decision["responsible_parties"]:
        errors.append("root_cause_analysis.responsible_parties does not match policy")
    if not isinstance(parties, list):
        errors.append("root_cause_analysis.responsible_parties must be a list")
    elif len(parties) > config.MAX_PARTIES:
        errors.append("root_cause_analysis.responsible_parties exceeds limit")

    evidence = output.get("evidence_ids")
    expected_evidence = policy_engine.build_evidence_ids(facts, expected_decision)
    if not isinstance(evidence, list):
        errors.append("evidence_ids must be a list")
    else:
        if len(evidence) > config.MAX_EVIDENCE:
            errors.append("evidence_ids exceeds limit")
        if not _unique(evidence):
            errors.append("evidence_ids contains duplicates")
        if evidence != expected_evidence:
            errors.append("evidence_ids is incomplete or does not match source data")

        valid_ids = {
            f"order:{order_id}",
            *{
                f"item:{order_id}:{item['order_item_id']}"
                for item in facts["items"]
            },
            *{
                f"payment:{order_id}:{payment['payment_sequential']}"
                for payment in facts["payments"]
            },
            *{f"seller:{item['seller_id']}" for item in facts["items"]},
            f"policy:{expected_decision['root_cause']}",
        }
        for evidence_id in evidence:
            if not isinstance(evidence_id, str) or evidence_id not in valid_ids:
                errors.append(f"invalid or ungrounded evidence ID: {evidence_id!r}")

    financial = output.get("financial_resolution", {})
    if not isinstance(financial, dict):
        financial = {}
    if financial.get("currency") != "BRL":
        errors.append("financial_resolution.currency must be BRL")
    expected_money = {
        "item_total_brl": facts["item_total"],
        "freight_total_brl": facts["freight_total"],
        "payment_total_brl": facts["payment_total"],
        "recommended_refund_brl": expected_decision["recommended_refund_brl"],
    }
    for field, expected in expected_money.items():
        actual = financial.get(field)
        if not _money_matches(actual, expected):
            errors.append(f"financial_resolution.{field} does not match source data")
        elif float(actual) < 0 or abs(float(actual) - round(float(actual), 2)) > 1e-9:
            errors.append(f"financial_resolution.{field} must be non-negative with 2 decimals")

    actions = output.get("resolution_actions")
    if not isinstance(actions, list):
        errors.append("resolution_actions must be a list")
    else:
        if len(actions) > config.MAX_ACTIONS:
            errors.append("resolution_actions exceeds limit")
        if actions != expected_decision["resolution_actions"]:
            errors.append("resolution_actions does not match EC_POLICY_V1")

    try:
        json.dumps(output, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"output is not strict JSON serializable: {exc}")

    return errors
