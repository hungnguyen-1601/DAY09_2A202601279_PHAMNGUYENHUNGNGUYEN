"""Hop dong handoff co cau truc giua cac agent.

Moi handoff bat buoc mang du context de agent nhan viec va nguoi cham co the
truy vet: ticket/cau hoi, nhiem vu, fact kem source ID, phan con thieu hoac
mau thuan, va buoc tiep theo.
"""

REQUIRED_FIELDS = {
    "ticket_id",
    "recipient",
    "question",
    "assigned_task",
    "sourced_facts",
    "missing_or_conflicting_facts",
    "next_action",
}


def sourced_fact(name: str, value, source_ids: list[str]) -> dict:
    """Tao mot fact kem danh sach ID nguon co the truy vet."""
    return {"name": name, "value": value, "source_ids": list(source_ids)}


def validate_handoff(handoff: dict, expected_recipient: str | None = None) -> list[str]:
    """Tra ve danh sach loi contract; danh sach rong nghia la hop le."""
    errors = []
    if not isinstance(handoff, dict):
        return ["handoff must be an object"]

    missing = REQUIRED_FIELDS - set(handoff)
    extra = set(handoff) - REQUIRED_FIELDS
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected fields: {sorted(extra)}")

    for field in ("ticket_id", "recipient", "question", "assigned_task", "next_action"):
        if not isinstance(handoff.get(field), str) or not handoff.get(field, "").strip():
            errors.append(f"{field} must be a non-empty string")

    if expected_recipient and handoff.get("recipient") != expected_recipient:
        errors.append(
            f"recipient {handoff.get('recipient')!r} != {expected_recipient!r}"
        )

    facts = handoff.get("sourced_facts")
    if not isinstance(facts, list) or not facts:
        errors.append("sourced_facts must be a non-empty list")
    else:
        seen_names = set()
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict) or set(fact) != {"name", "value", "source_ids"}:
                errors.append(f"sourced_facts[{index}] has invalid shape")
                continue
            name = fact.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"sourced_facts[{index}].name must be non-empty")
            elif name in seen_names:
                errors.append(f"duplicate sourced fact name: {name}")
            else:
                seen_names.add(name)
            source_ids = fact.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                errors.append(f"sourced_facts[{index}].source_ids must be non-empty")
            elif any(not isinstance(x, str) or not x.strip() for x in source_ids):
                errors.append(f"sourced_facts[{index}].source_ids contains invalid ID")
            elif len(source_ids) != len(set(source_ids)):
                errors.append(f"sourced_facts[{index}].source_ids contains duplicates")

    gaps = handoff.get("missing_or_conflicting_facts")
    if not isinstance(gaps, list):
        errors.append("missing_or_conflicting_facts must be a list")
    elif any(not isinstance(x, str) or not x.strip() for x in gaps):
        errors.append("missing_or_conflicting_facts contains an invalid description")

    return errors


def create_handoff(
    ticket_id: str,
    recipient: str,
    question: str,
    assigned_task: str,
    sourced_facts: list[dict],
    missing_or_conflicting_facts: list[str],
    next_action: str,
) -> dict:
    """Tao handoff va fail-fast neu contract khong day du."""
    handoff = {
        "ticket_id": ticket_id,
        "recipient": recipient,
        "question": question,
        "assigned_task": assigned_task,
        "sourced_facts": sourced_facts,
        "missing_or_conflicting_facts": missing_or_conflicting_facts,
        "next_action": next_action,
    }
    errors = validate_handoff(handoff, expected_recipient=recipient)
    if errors:
        raise ValueError("Invalid handoff: " + "; ".join(errors))
    return handoff
