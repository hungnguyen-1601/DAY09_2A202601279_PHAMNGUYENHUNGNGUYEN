"""Kiem tra trace 50 ticket, handoff contract va output da ghi.

Chay: python -B scripts/verify_trace.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, data_access, handoffs  # noqa: E402

TRACE_PATH = ROOT / "logging" / "trace.jsonl"
EXPECTED_CASE_IDS = [f"EC_{index:03d}" for index in range(1, 51)]
EXPECTED_RECIPIENTS = [
    "order_seller_agent",
    "payment_agent",
    "delivery_agent",
    "policy_agent",
    "verifier_agent",
]


def _reject_non_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _loads(raw):
    return json.loads(raw, parse_constant=_reject_non_json_constant)


def _read_trace(errors):
    if not TRACE_PATH.is_file():
        errors.append("logging/trace.jsonl does not exist")
        return []
    records = []
    for line_number, line in enumerate(
        TRACE_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            record = _loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"trace line {line_number} is invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"trace line {line_number} is not an object")
            continue
        record["_line"] = line_number
        records.append(record)
    return records


def _valid_source_ids(case, output):
    order_id = case["customer_request"]["claimed_order_id"]
    facts = data_access.get_order_facts(order_id)
    cause = output["root_cause_analysis"]["ranked_causes"][0]["cause_code"]
    return {
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
        f"policy:{config.POLICY_VERSION}",
        f"policy:{cause}",
    }


def main():
    errors = []
    data_access.load_data()
    if TRACE_PATH.with_name(TRACE_PATH.name + ".tmp").exists():
        errors.append("stale logging/trace.jsonl.tmp exists")
    records = _read_trace(errors)

    actual_case_ids = sorted(
        {record.get("case_id") for record in records if record.get("case_id")}
    )
    if actual_case_ids != EXPECTED_CASE_IDS:
        missing = sorted(set(EXPECTED_CASE_IDS) - set(actual_case_ids))
        extra = sorted(set(actual_case_ids) - set(EXPECTED_CASE_IDS))
        errors.append(f"trace case IDs mismatch: missing={missing}, extra={extra}")

    total_handoffs = 0
    for case_id in EXPECTED_CASE_IDS:
        case_records = [record for record in records if record.get("case_id") == case_id]
        if not case_records:
            continue
        case_path = ROOT / "input" / f"{case_id}.json"
        output_path = ROOT / "output" / f"{case_id}.json"
        try:
            case = _loads(case_path.read_text(encoding="utf-8"))
            output = _loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{case_id}: cannot read input/output: {exc}")
            continue

        for event in ("case_received", "facts_compiled", "verification", "output_staged", "output_written"):
            count = sum(record.get("event") == event for record in case_records)
            if count != 1:
                errors.append(f"{case_id}: expected one {event}, found {count}")
        if any(record.get("event") == "verification_failed" for record in case_records):
            errors.append(f"{case_id}: contains verification_failed")

        required_event_agents = {
            "case_received": "coordinator",
            "facts_compiled": "coordinator",
            "verification": "verifier_agent",
            "output_staged": "coordinator",
            "output_written": "coordinator",
        }
        for event, expected_agent in required_event_agents.items():
            matches = [record for record in case_records if record.get("event") == event]
            if len(matches) == 1 and matches[0].get("agent") != expected_agent:
                errors.append(f"{case_id}: {event} emitted by wrong agent")

        successful_calls = [
            record for record in case_records if record.get("event") == "llm_call"
        ]
        call_agents = [record.get("agent") for record in successful_calls]
        expected_call_agents = EXPECTED_RECIPIENTS[:4]
        if call_agents != expected_call_agents:
            errors.append(f"{case_id}: successful LLM call order is {call_agents}")
        for call in successful_calls:
            if call.get("model") != config.MODEL_NAME:
                errors.append(f"{case_id}: llm_call uses unexpected model")
            try:
                parsed_raw = _loads(call.get("output_raw", ""))
                if not isinstance(parsed_raw, dict):
                    errors.append(f"{case_id}: llm_call output is not a JSON object")
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                errors.append(f"{case_id}: llm_call output_raw is invalid JSON: {exc}")

        expected_domain_events = {
            ("order_seller_agent", "finding"),
            ("payment_agent", "finding"),
            ("delivery_agent", "finding"),
            ("policy_agent", "proposal"),
        }
        for agent, event in expected_domain_events:
            count = sum(
                record.get("agent") == agent and record.get("event") == event
                for record in case_records
            )
            if count != 1:
                errors.append(f"{case_id}: expected one {agent}/{event}, found {count}")

        case_handoffs = [
            record for record in case_records if record.get("event") == "handoff"
        ]
        total_handoffs += len(case_handoffs)
        recipients = [record.get("to") for record in case_handoffs]
        if recipients != EXPECTED_RECIPIENTS:
            errors.append(f"{case_id}: handoff order is {recipients}")

        try:
            valid_sources = _valid_source_ids(case, output)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            errors.append(f"{case_id}: cannot derive valid source IDs: {exc}")
            continue
        for record in case_handoffs:
            if record.get("agent") != "coordinator":
                errors.append(f"{case_id}: handoff was not emitted by coordinator")
            contract = {
                key: record.get(key) for key in handoffs.REQUIRED_FIELDS
            }
            contract_errors = handoffs.validate_handoff(
                contract, expected_recipient=record.get("to")
            )
            if record.get("ticket_id") != case_id:
                contract_errors.append("ticket_id does not match case_id")
            if record.get("question") != case["customer_request"]["message"]:
                contract_errors.append("question does not match customer request")
            if contract_errors:
                errors.append(
                    f"{case_id} line {record['_line']}: invalid handoff: "
                    + "; ".join(contract_errors)
                )
            for fact in record.get("sourced_facts", []):
                if not isinstance(fact, dict):
                    continue
                for source_id in fact.get("source_ids", []):
                    if source_id not in valid_sources:
                        errors.append(
                            f"{case_id} line {record['_line']}: "
                            f"ungrounded handoff source {source_id}"
                        )

        event_positions = {}
        for index, record in enumerate(case_records):
            event_positions.setdefault((record.get("agent"), record.get("event")), index)
        for record in case_handoffs:
            handoff_position = case_records.index(record)
            recipient = record.get("to")
            receiving_event = "verification" if recipient == "verifier_agent" else "llm_call"
            receive_position = event_positions.get((recipient, receiving_event))
            if receive_position is None or handoff_position >= receive_position:
                errors.append(
                    f"{case_id}: handoff to {recipient} does not precede {receiving_event}"
                )

        ordered_phase_events = [
            ("coordinator", "case_received"),
            ("coordinator", "facts_compiled"),
            ("verifier_agent", "verification"),
            ("coordinator", "output_staged"),
            ("coordinator", "output_written"),
        ]
        phase_positions = [event_positions.get(key) for key in ordered_phase_events]
        if any(position is None for position in phase_positions) or phase_positions != sorted(
            phase_positions
        ):
            errors.append(f"{case_id}: core event phases are out of order")

        verifications = [
            record for record in case_records if record.get("event") == "verification"
        ]
        if len(verifications) == 1:
            verification = verifications[0]
            checks = verification.get("checks")
            if verification.get("output_valid") is not True:
                errors.append(f"{case_id}: verifier did not mark output_valid")
            if verification.get("validation_errors") != []:
                errors.append(f"{case_id}: verifier has validation errors")
            if not isinstance(checks, dict) or not checks or not all(
                value is True for value in checks.values()
            ):
                errors.append(f"{case_id}: verifier checks are incomplete")
            if verification.get("final_output") != output:
                errors.append(f"{case_id}: verified final_output differs from output file")

        written = [
            record for record in case_records if record.get("event") == "output_written"
        ]
        if len(written) == 1 and written[0].get("output") != output:
            errors.append(f"{case_id}: output_written payload differs from output file")

    if total_handoffs != 250:
        errors.append(f"trace has {total_handoffs} handoffs, expected 250")
    if records:
        last = records[-1]
        if last.get("case_id") != "EC_050" or last.get("event") != "output_written":
            errors.append("last trace event must be EC_050 output_written")

    print(
        f"Checked trace: {len(actual_case_ids)} cases, {total_handoffs} handoffs, "
        f"{len(errors)} errors"
    )
    for error in errors:
        print("ERROR:", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
