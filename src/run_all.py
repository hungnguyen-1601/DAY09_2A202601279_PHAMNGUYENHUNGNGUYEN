"""Coordinator Agent + entry point: chay 50 case K3 end-to-end.

Luong moi case:
  input -> grounded fact sheet -> 3 specialist handoff -> Policy handoff
  -> Verifier handoff -> full-output verification -> staging.

Chi khi ca 50 case thanh cong, output va trace moi duoc publish. Vi vay mot
batch bi ngat khong ghi de bo artifact hoan chinh truoc do.

Chay: python -m src.run_all
"""
import json
import platform
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from . import agents, config, data_access, handoffs
from .tracer import Tracer

EXPECTED_CASE_NAMES = [f"EC_{index:03d}.json" for index in range(1, 51)]


def _dedupe(values):
    return list(dict.fromkeys(values))


def _source_ids(facts):
    order_id = facts["order_id"]
    return _dedupe(
        [f"order:{order_id}"]
        + [
            f"item:{order_id}:{item['order_item_id']}"
            for item in facts["items"]
        ]
        + [
            f"payment:{order_id}:{payment['payment_sequential']}"
            for payment in facts["payments"]
        ]
        + [f"seller:{item['seller_id']}" for item in facts["items"]]
    )


def _finding_gaps(findings, facts):
    """Mo ta ro specialist output bi thieu/mau thuan truoc Policy handoff."""
    gaps = []
    order = findings.get("order_seller")
    payment = findings.get("payment")
    delivery = findings.get("delivery")

    if not isinstance(order, dict):
        gaps.append("Order & Seller Agent did not return a JSON object.")
    else:
        if order.get("order_status") != facts["order_status"]:
            gaps.append("Order & Seller finding conflicts with source order_status.")
        if order.get("sellers_past_limit") != facts["sellers_past_limit"]:
            gaps.append("Order & Seller finding conflicts with sourced seller handoff facts.")

    if not isinstance(payment, dict):
        gaps.append("Payment Agent did not return a JSON object.")
    else:
        if payment.get("n_payments") != facts["n_payments"]:
            gaps.append("Payment finding conflicts with sourced payment row count.")
        if payment.get("matches_order_value") != facts["payment_matches_order_value"]:
            gaps.append("Payment finding conflicts with sourced reconciliation result.")

    if not isinstance(delivery, dict):
        gaps.append("Delivery Agent did not return a JSON object.")
    elif delivery.get("delivered_after_estimate") != facts["delivered_after_estimate"]:
        gaps.append("Delivery finding conflicts with sourced delivery timestamps.")
    return gaps


def process_case(tracer, case_path):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case["case_id"]
    order_id = case["customer_request"]["claimed_order_id"]
    customer_question = case["customer_request"].get("message", "").strip()

    if case_id != case_path.stem:
        raise ValueError(f"{case_path.name}: case_id {case_id!r} does not match filename")

    tracer.log(
        case_id,
        "coordinator",
        "case_received",
        claimed_order_id=order_id,
        policy_version=case.get("policy_version"),
        message=customer_question,
    )

    if case.get("policy_version") != config.POLICY_VERSION:
        tracer.log(
            case_id,
            "coordinator",
            "case_rejected",
            reason="wrong_policy_version",
            expected_policy_version=config.POLICY_VERSION,
        )
        raise ValueError(
            f"{case_id}: policy_version must be {config.POLICY_VERSION}"
        )
    if not data_access.order_exists(order_id):
        tracer.log(case_id, "coordinator", "case_rejected", reason="order_not_found")
        raise ValueError(f"{case_id}: claimed order {order_id} does not exist")

    facts = data_access.get_order_facts(order_id)
    all_sources = _source_ids(facts)
    order_source = [f"order:{order_id}"]
    item_sources = [
        f"item:{order_id}:{item['order_item_id']}" for item in facts["items"]
    ] or order_source
    payment_sources = [
        f"payment:{order_id}:{payment['payment_sequential']}"
        for payment in facts["payments"]
    ] or order_source
    seller_sources = _dedupe(
        [f"seller:{item['seller_id']}" for item in facts["items"]]
    ) or order_source

    tracer.log(
        case_id,
        "coordinator",
        "facts_compiled",
        order_status=facts["order_status"],
        n_items=facts["n_items"],
        n_payments=facts["n_payments"],
        payment_total=facts["payment_total"],
        source_ids=all_sources,
    )

    order_gaps = []
    if not facts["items"]:
        order_gaps.append("No order item row exists.")
    if facts["carrier_ts"] is None:
        order_gaps.append("order_delivered_carrier_date is missing.")
    if any(item["shipping_limit_date"] is None for item in facts["items"]):
        order_gaps.append("At least one item shipping_limit_date is missing.")
    order_handoff = handoffs.create_handoff(
        ticket_id=case_id,
        recipient="order_seller_agent",
        question=customer_question,
        assigned_task=(
            "Determine the order status and which sellers, if any, handed the "
            "parcel to the carrier after shipping_limit_date."
        ),
        sourced_facts=[
            handoffs.sourced_fact("order_status", facts["order_status"], order_source),
            handoffs.sourced_fact("carrier_pickup_ts", facts["carrier_ts"], order_source),
            handoffs.sourced_fact(
                "items", facts["items"], _dedupe(item_sources + seller_sources)
            ),
        ],
        missing_or_conflicting_facts=order_gaps,
        next_action="Return the grounded order and seller finding as JSON.",
    )
    tracer.handoff(case_id, "order_seller_agent", order_handoff)
    f_order = agents.order_seller_agent(tracer, case_id, facts, order_handoff)

    payment_gaps = []
    if not facts["payments"]:
        payment_gaps.append("No payment row exists.")
    if not facts["payment_matches_order_value"]:
        payment_gaps.append(
            "Payment total conflicts with item_total + freight_total beyond 0.10 BRL."
        )
    payment_handoff = handoffs.create_handoff(
        ticket_id=case_id,
        recipient="payment_agent",
        question=customer_question,
        assigned_task=(
            "Reconcile payment rows with item + freight totals and determine "
            "whether a split payment is valid."
        ),
        sourced_facts=[
            handoffs.sourced_fact("payments", facts["payments"], payment_sources),
            handoffs.sourced_fact("item_total_brl", facts["item_total"], item_sources),
            handoffs.sourced_fact(
                "freight_total_brl", facts["freight_total"], item_sources
            ),
            handoffs.sourced_fact(
                "payment_total_brl", facts["payment_total"], payment_sources
            ),
            handoffs.sourced_fact(
                "payment_matches_order_value",
                facts["payment_matches_order_value"],
                _dedupe(item_sources + payment_sources),
            ),
        ],
        missing_or_conflicting_facts=payment_gaps,
        next_action="Return the grounded payment reconciliation finding as JSON.",
    )
    tracer.handoff(case_id, "payment_agent", payment_handoff)
    f_payment = agents.payment_agent(tracer, case_id, facts, payment_handoff)

    delivery_gaps = []
    if facts["delivered_ts"] is None:
        delivery_gaps.append("order_delivered_customer_date is missing.")
    if facts["estimated_ts"] is None:
        delivery_gaps.append("order_estimated_delivery_date is missing.")
    delivery_handoff = handoffs.create_handoff(
        ticket_id=case_id,
        recipient="delivery_agent",
        question=customer_question,
        assigned_task="Determine whether delivery occurred after the estimated date.",
        sourced_facts=[
            handoffs.sourced_fact("delivered_ts", facts["delivered_ts"], order_source),
            handoffs.sourced_fact("estimated_ts", facts["estimated_ts"], order_source),
            handoffs.sourced_fact(
                "delivered_after_estimate",
                facts["delivered_after_estimate"],
                order_source,
            ),
        ],
        missing_or_conflicting_facts=delivery_gaps,
        next_action="Return the grounded delivery timing finding as JSON.",
    )
    tracer.handoff(case_id, "delivery_agent", delivery_handoff)
    f_delivery = agents.delivery_agent(tracer, case_id, facts, delivery_handoff)

    findings = {
        "order_seller": f_order,
        "payment": f_payment,
        "delivery": f_delivery,
    }
    policy_gaps = _dedupe(
        order_gaps
        + payment_gaps
        + delivery_gaps
        + _finding_gaps(findings, facts)
    )
    policy_handoff = handoffs.create_handoff(
        ticket_id=case_id,
        recipient="policy_agent",
        question=customer_question,
        assigned_task=(
            "Apply the first matching EC_POLICY_V1 rule and propose the primary "
            "issue, refund and policy reason."
        ),
        sourced_facts=[
            handoffs.sourced_fact(
                "policy_version", config.POLICY_VERSION, [f"policy:{config.POLICY_VERSION}"]
            ),
            handoffs.sourced_fact("order_status", facts["order_status"], order_source),
            handoffs.sourced_fact("payment_total_brl", facts["payment_total"], payment_sources),
            handoffs.sourced_fact("freight_total_brl", facts["freight_total"], item_sources),
            handoffs.sourced_fact("n_payments", facts["n_payments"], payment_sources),
            handoffs.sourced_fact(
                "delivered_after_estimate", facts["delivered_after_estimate"], order_source
            ),
            handoffs.sourced_fact(
                "sellers_past_limit", facts["sellers_past_limit"], seller_sources
            ),
            handoffs.sourced_fact(
                "payment_matches_order_value",
                facts["payment_matches_order_value"],
                _dedupe(item_sources + payment_sources),
            ),
            handoffs.sourced_fact("specialist_findings", findings, all_sources),
        ],
        missing_or_conflicting_facts=policy_gaps,
        next_action="Return one EC_POLICY_V1 proposal as JSON for independent verification.",
    )
    tracer.handoff(case_id, "policy_agent", policy_handoff)
    proposal = agents.policy_agent(tracer, case_id, facts, findings, policy_handoff)

    verifier_gaps = list(policy_gaps)
    if not isinstance(proposal, dict):
        verifier_gaps.append("Policy Agent did not return a JSON object.")
    verifier_handoff = handoffs.create_handoff(
        ticket_id=case_id,
        recipient="verifier_agent",
        question=customer_question,
        assigned_task=(
            "Independently recompute policy and validate the complete final JSON: "
            "schema, entities, evidence IDs, amounts, parties and actions."
        ),
        sourced_facts=[
            handoffs.sourced_fact("policy_proposal", proposal, all_sources),
            handoffs.sourced_fact(
                "verified_fact_summary",
                {
                    "order_status": facts["order_status"],
                    "item_total_brl": facts["item_total"],
                    "freight_total_brl": facts["freight_total"],
                    "payment_total_brl": facts["payment_total"],
                    "delivered_after_estimate": facts["delivered_after_estimate"],
                    "sellers_past_limit": facts["sellers_past_limit"],
                },
                all_sources,
            ),
            handoffs.sourced_fact(
                "policy_version", config.POLICY_VERSION, [f"policy:{config.POLICY_VERSION}"]
            ),
        ],
        missing_or_conflicting_facts=verifier_gaps,
        next_action=(
            "Correct any proposal mismatch, reject invalid output, and return only "
            "a fully verified final output."
        ),
    )
    tracer.handoff(case_id, "verifier_agent", verifier_handoff)
    output = agents.verifier_agent(tracer, case, facts, proposal, verifier_handoff)
    return case_id, output


def _build_metadata(case_count, wall_seconds):
    return {
        "cohort": "K3",
        "repo_starter": "K3 Day 09 - Multi-Agent E-commerce Dispute Resolution",
        "repository_url": (
            "https://github.com/hungnguyen-1601/"
            "DAY09_2A202601279_PHAMNGUYENHUNGNGUYEN"
        ),
        "model": config.MODEL_NAME,
        "model_full_name": "Meta Llama 3.2 3B Instruct",
        "parameter_size": config.MODEL_PARAMETER_SIZE,
        "provider": config.MODEL_PROVIDER,
        "framework": (
            "Custom Python multi-agent pipeline: Coordinator + 3 specialist LLM "
            "agents (Order&Seller, Payment, Delivery) + Policy Agent (LLM) + "
            "deterministic full-output Verifier Agent over Olist CSV facts"
        ),
        "tools": ["Ollama local API", "pandas"],
        "runtime": {
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor(),
            "inference": "Ollama local, CPU",
            "cases": case_count,
            "verified_outputs": case_count,
            "wall_time_seconds": round(wall_seconds, 1),
        },
        "policy_version": config.POLICY_VERSION,
        "trace_schema_version": 2,
    }


def main():
    t_start = time.time()
    data_access.load_data()
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    config.LOG_DIR.mkdir(exist_ok=True)

    case_paths = sorted(config.INPUT_DIR.glob("EC_*.json"))
    actual_case_names = [path.name for path in case_paths]
    if actual_case_names != EXPECTED_CASE_NAMES:
        raise ValueError("input/ must contain exactly EC_001.json through EC_050.json")

    unexpected_output_entries = sorted(
        path.name
        for path in config.OUTPUT_DIR.iterdir()
        if path.name not in EXPECTED_CASE_NAMES
    )
    if unexpected_output_entries:
        raise ValueError(
            f"output/ contains unexpected entries: {unexpected_output_entries}"
        )

    output_backup = config.LOG_DIR / "output_publish_backup"
    metadata_backup = config.LOG_DIR / "metadata_publish_backup.json"
    if output_backup.exists() or metadata_backup.exists():
        raise RuntimeError(
            "A publish backup already exists in logging/; inspect/recover it "
            "before starting another batch."
        )

    tracer = Tracer(config.TRACE_PATH)
    metadata_temp = config.METADATA_PATH.with_name(config.METADATA_PATH.name + ".tmp")
    outputs = {}
    dist = Counter()
    publish_started = False
    trace_committed = False
    metadata_original_existed = config.METADATA_PATH.exists()
    metadata_published = False
    try:
        with tempfile.TemporaryDirectory(
            prefix="output_staging_", dir=config.LOG_DIR
        ) as staging_name:
            staging_dir = Path(staging_name)
            print(
                f"Processing {len(case_paths)} cases with model {config.MODEL_NAME}...",
                flush=True,
            )
            for index, case_path in enumerate(case_paths, 1):
                case_start = time.time()
                case_id, output = process_case(tracer, case_path)
                staged_path = staging_dir / f"{case_id}.json"
                staged_path.write_text(
                    json.dumps(
                        output,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    ),
                    encoding="utf-8",
                )
                tracer.log(
                    case_id,
                    "coordinator",
                    "output_staged",
                    path=f"output/{case_id}.json",
                    evidence_ids=output["evidence_ids"],
                )
                outputs[case_id] = output
                dist[output["assessment"]["primary_issue"]] += 1
                print(
                    f"[{index:2}/{len(case_paths)}] {case_id}: "
                    f"{output['assessment']['primary_issue']} "
                    f"(refund {output['financial_resolution']['recommended_refund_brl']} "
                    f"BRL, {time.time() - case_start:.1f}s)",
                    flush=True,
                )

            staged_names = sorted(path.name for path in staging_dir.iterdir())
            if staged_names != EXPECTED_CASE_NAMES:
                raise RuntimeError("staging did not produce exactly 50 expected outputs")

            wall = time.time() - t_start
            metadata = _build_metadata(len(case_paths), wall)
            metadata_temp.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )

            # Rollback-capable publish: keep the previous complete artifacts
            # until output, metadata and trace have all been replaced.
            config.OUTPUT_DIR.replace(output_backup)
            publish_started = True
            staging_dir.replace(config.OUTPUT_DIR)
            if metadata_original_existed:
                config.METADATA_PATH.replace(metadata_backup)
            metadata_temp.replace(config.METADATA_PATH)
            metadata_published = True

            for case_name in EXPECTED_CASE_NAMES:
                case_id = Path(case_name).stem
                tracer.log(
                    case_id,
                    "coordinator",
                    "output_written",
                    path=f"output/{case_name}",
                    output=outputs[case_id],
                )
            tracer.commit()
            trace_committed = True

            shutil.rmtree(output_backup, ignore_errors=True)
            metadata_backup.unlink(missing_ok=True)

        print(f"\nDone in {wall:.0f}s. Distribution: {dict(dist)}", flush=True)
        print(f"Outputs: {config.OUTPUT_DIR}", flush=True)
        print(f"Trace:   {config.TRACE_PATH}", flush=True)
        print(f"Meta:    {config.METADATA_PATH}", flush=True)
        return 0
    except BaseException:
        if not trace_committed:
            tracer.abort()
        metadata_temp.unlink(missing_ok=True)
        if publish_started and not trace_committed:
            if config.OUTPUT_DIR.exists():
                shutil.rmtree(config.OUTPUT_DIR)
            if output_backup.exists():
                output_backup.replace(config.OUTPUT_DIR)
            if metadata_published and config.METADATA_PATH.exists():
                config.METADATA_PATH.unlink()
            if metadata_backup.exists():
                metadata_backup.replace(config.METADATA_PATH)
        raise


if __name__ == "__main__":
    sys.exit(main())
