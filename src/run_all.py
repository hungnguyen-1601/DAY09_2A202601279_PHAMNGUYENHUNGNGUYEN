"""Coordinator Agent + entry point: chay 50 case end-to-end.

Luong xu ly moi case:
  input JSON -> Coordinator -> tool data_access (fact sheet)
  -> handoff lan luot 3 specialist (Order&Seller, Payment, Delivery - LLM)
  -> handoff findings cho Policy Agent (LLM) de xuat ket luan
  -> Verifier Agent (deterministic) doi chieu voi CSV, sua sai lech
  -> ghi output/<case_id>.json

Chay: python -m src.run_all
"""
import json
import platform
import sys
import time
from collections import Counter

from . import agents, config, data_access, policy_engine
from .tracer import Tracer


def process_case(tracer, case_path):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case["case_id"]
    order_id = case["customer_request"]["claimed_order_id"]

    tracer.log(
        case_id,
        "coordinator",
        "case_received",
        claimed_order_id=order_id,
        policy_version=case.get("policy_version"),
        message=case["customer_request"].get("message", "")[:120],
    )

    if not data_access.order_exists(order_id):
        # Khong xay ra voi bo case chinh thuc; van ghi output an toan.
        tracer.log(case_id, "coordinator", "order_not_found", order_id=order_id)
        facts = {
            "order_id": order_id, "order_status": "unknown", "items": [],
            "payments": [], "n_items": 0, "n_payments": 0, "item_total": 0.0,
            "freight_total": 0.0, "payment_total": 0.0,
            "delivered_after_estimate": None,
            "payment_matches_order_value": True, "sellers_past_limit": [],
        }
        decision = policy_engine.decide(facts)
        return case_id, policy_engine.build_output(case_id, facts, decision)

    facts = data_access.get_order_facts(order_id)
    tracer.log(
        case_id,
        "coordinator",
        "facts_compiled",
        order_status=facts["order_status"],
        n_items=facts["n_items"],
        n_payments=facts["n_payments"],
        payment_total=facts["payment_total"],
    )

    tracer.log(case_id, "coordinator", "handoff", to="order_seller_agent")
    f_order = agents.order_seller_agent(tracer, case_id, facts)
    tracer.log(case_id, "coordinator", "handoff", to="payment_agent")
    f_payment = agents.payment_agent(tracer, case_id, facts)
    tracer.log(case_id, "coordinator", "handoff", to="delivery_agent")
    f_delivery = agents.delivery_agent(tracer, case_id, facts)

    findings = {
        "order_seller": f_order,
        "payment": f_payment,
        "delivery": f_delivery,
    }
    tracer.log(case_id, "coordinator", "handoff", to="policy_agent")
    proposal = agents.policy_agent(tracer, case_id, facts, findings)

    tracer.log(case_id, "coordinator", "handoff", to="verifier_agent")
    decision = agents.verifier_agent(tracer, case_id, facts, proposal)

    output = policy_engine.build_output(case_id, facts, decision)
    tracer.log(
        case_id,
        "coordinator",
        "output_written",
        primary_issue=output["assessment"]["primary_issue"],
        refund_brl=output["financial_resolution"]["recommended_refund_brl"],
    )
    return case_id, output


def main():
    t_start = time.time()
    data_access.load_data()
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    config.LOG_DIR.mkdir(exist_ok=True)
    tracer = Tracer(config.TRACE_PATH)

    case_paths = sorted(config.INPUT_DIR.glob("EC_*.json"))
    print(f"Processing {len(case_paths)} cases with model {config.MODEL_NAME}...")
    dist = Counter()
    for i, case_path in enumerate(case_paths, 1):
        t0 = time.time()
        case_id, output = process_case(tracer, case_path)
        out_path = config.OUTPUT_DIR / f"{case_id}.json"
        out_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        dist[output["assessment"]["primary_issue"]] += 1
        print(
            f"[{i:2}/{len(case_paths)}] {case_id}: "
            f"{output['assessment']['primary_issue']} "
            f"(refund {output['financial_resolution']['recommended_refund_brl']} BRL, "
            f"{time.time() - t0:.1f}s)"
        )
    tracer.close()

    wall = time.time() - t_start
    metadata = {
        "model": config.MODEL_NAME,
        "model_full_name": "Meta Llama 3.2 3B Instruct",
        "parameter_size": config.MODEL_PARAMETER_SIZE,
        "provider": config.MODEL_PROVIDER,
        "framework": (
            "Custom Python multi-agent pipeline: Coordinator + 3 specialist LLM "
            "agents (Order&Seller, Payment, Delivery) + Policy Agent (LLM) + "
            "deterministic Verifier Agent (pandas over Olist CSVs)"
        ),
        "runtime": {
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor(),
            "inference": "Ollama local, CPU",
            "cases": len(case_paths),
            "wall_time_seconds": round(wall, 1),
        },
        "policy_version": config.POLICY_VERSION,
    }
    config.METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone in {wall:.0f}s. Distribution: {dict(dist)}")
    print(f"Outputs: {config.OUTPUT_DIR}")
    print(f"Trace:   {config.TRACE_PATH}")
    print(f"Meta:    {config.METADATA_PATH}")


if __name__ == "__main__":
    sys.exit(main())
