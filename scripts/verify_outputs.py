"""Kiem tra doc lap 50 file output truoc khi nop.

Doc truc tiep CSV (khong dung lai policy_engine cua pipeline) va kiem tra:
schema, gioi han so luong, dinh dang + su ton tai cua evidence ID, so tien,
va dieu kien rule theo README muc 4.

Chay: python scripts/verify_outputs.py
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TOL = 0.10

VALID_ISSUES = {
    "canceled_order_paid": ("ORDER_CANCELED_AFTER_PAYMENT", "issue_full_refund", "action_required", "platform"),
    "unavailable_order_paid": ("ORDER_UNAVAILABLE_AFTER_PAYMENT", "issue_full_refund", "action_required", "platform"),
    "late_delivery_seller": ("SELLER_HANDOFF_AFTER_LIMIT", "refund_freight", "action_required", "seller"),
    "late_delivery_logistics": ("CARRIER_DELIVERED_AFTER_ESTIMATE", "refund_freight", "action_required", "logistics_provider"),
    "valid_split_payment": ("MULTIPLE_PAYMENTS_RECONCILED", "explain_valid_split_payment", "no_action", None),
    "unsupported_late_claim": ("DELIVERY_WITHIN_ESTIMATE", "reject_late_refund", "no_action", None),
}

EXPECTED_NAMES = [f"EC_{index:03d}.json" for index in range(1, 51)]
TOP_LEVEL_KEYS = {
    "case_id", "assessment", "affected_entities", "root_cause_analysis",
    "evidence_ids", "financial_resolution", "resolution_actions",
}
NESTED_KEYS = {
    "assessment": {"primary_issue", "case_status", "confidence"},
    "affected_entities": {"order_ids", "item_ids", "seller_ids", "payment_ids"},
    "root_cause_analysis": {"ranked_causes", "responsible_parties"},
    "financial_resolution": {
        "currency", "item_total_brl", "freight_total_brl",
        "payment_total_brl", "recommended_refund_brl",
    },
}


def _reject_non_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def main():
    orders = pd.read_csv(
        ROOT / "data/olist_orders_dataset.csv",
        parse_dates=[
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    ).set_index("order_id", drop=False)
    items = pd.read_csv(
        ROOT / "data/olist_order_items_dataset.csv", parse_dates=["shipping_limit_date"]
    )
    pays = pd.read_csv(ROOT / "data/olist_order_payments_dataset.csv")

    errors = []
    warns = []
    output_entries = sorted((ROOT / "output").iterdir(), key=lambda path: path.name)
    actual_names = [path.name for path in output_entries]
    if actual_names != EXPECTED_NAMES or any(not path.is_file() for path in output_entries):
        missing = sorted(set(EXPECTED_NAMES) - set(actual_names))
        extra = sorted(set(actual_names) - set(EXPECTED_NAMES))
        errors.append(
            f"output/ phai chi co EC_001.json..EC_050.json; "
            f"thieu={missing}, du={extra}"
        )
    out_files = [ROOT / "output" / name for name in EXPECTED_NAMES if (ROOT / "output" / name).is_file()]

    for path in out_files:
        cid = path.stem
        e = lambda msg: errors.append(f"{cid}: {msg}")

        try:
            o = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=_reject_non_json_constant,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            e(f"JSON khong hop le: {exc}")
            continue
        if not isinstance(o, dict) or set(o) != TOP_LEVEL_KEYS:
            e("top-level schema keys khong chinh xac")
            continue
        bad_nested = False
        for key, required_keys in NESTED_KEYS.items():
            if not isinstance(o.get(key), dict) or set(o[key]) != required_keys:
                e(f"schema cua {key} khong chinh xac")
                bad_nested = True
        list_fields = [o.get("evidence_ids"), o.get("resolution_actions")]
        ae_candidate = o.get("affected_entities", {})
        rca_candidate = o.get("root_cause_analysis", {})
        list_fields += [ae_candidate.get(key) for key in ("order_ids", "item_ids", "seller_ids", "payment_ids")]
        list_fields += [rca_candidate.get("ranked_causes"), rca_candidate.get("responsible_parties")]
        if any(not isinstance(value, list) for value in list_fields):
            e("schema yeu cau cac entity/evidence/cause/party/action la list")
            bad_nested = True
        string_lists = [o.get("evidence_ids"), o.get("resolution_actions")]
        string_lists += [
            ae_candidate.get(key)
            for key in ("order_ids", "item_ids", "seller_ids", "payment_ids")
        ]
        if any(
            isinstance(values, list)
            and any(not isinstance(value, str) for value in values)
            for values in string_lists
        ):
            e("entity/evidence/action chi duoc chua chuoi")
            bad_nested = True
        causes_candidate = rca_candidate.get("ranked_causes")
        if (
            isinstance(causes_candidate, list)
            and (
                not causes_candidate
                or any(
                    not isinstance(value, dict)
                    or set(value) != {"cause_code", "rank"}
                    or not isinstance(value.get("cause_code"), str)
                    or not isinstance(value.get("rank"), int)
                    or isinstance(value.get("rank"), bool)
                    for value in causes_candidate
                )
            )
        ):
            e("ranked_causes co phan tu khong hop le")
            bad_nested = True
        parties_candidate = rca_candidate.get("responsible_parties")
        if isinstance(parties_candidate, list) and any(
            not isinstance(value, dict)
            or set(value) != {"party_type", "party_id"}
            or not isinstance(value.get("party_type"), str)
            or not isinstance(value.get("party_id"), str)
            for value in parties_candidate
        ):
            e("responsible_parties co phan tu khong hop le")
            bad_nested = True
        if bad_nested:
            continue

        if o.get("case_id") != cid:
            e("case_id khong khop ten file")

        inp = json.loads((ROOT / "input" / f"{cid}.json").read_text(encoding="utf-8"))
        oid = inp["customer_request"]["claimed_order_id"]

        issue = o["assessment"]["primary_issue"]
        conf = o["assessment"]["confidence"]
        status = o["assessment"]["case_status"]
        if issue not in VALID_ISSUES:
            e(f"primary_issue la {issue}")
            continue
        cause, action, want_status, want_party = VALID_ISSUES[issue]
        if not _is_finite_number(conf) or not (0 <= conf <= 1):
            e(f"confidence {conf} ngoai [0,1]")
        if status != want_status:
            e(f"case_status {status}, mong doi {want_status}")

        # gioi han so luong
        ae = o["affected_entities"]
        for k in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
            if len(ae[k]) > 5:
                e(f"{k} vuot 5")
        if len(o["evidence_ids"]) > 10:
            e("evidence vuot 10")
        rca = o["root_cause_analysis"]
        if len(rca["ranked_causes"]) > 3 or len(rca["responsible_parties"]) > 3:
            e("ranked_causes/parties vuot gioi han")
        if len(o["resolution_actions"]) > 5:
            e("actions vuot 5")

        if rca["ranked_causes"][0]["cause_code"] != cause:
            e(f"cause {rca['ranked_causes'][0]['cause_code']}, mong doi {cause}")
        if rca["ranked_causes"] != [{"cause_code": cause, "rank": 1}]:
            e(f"ranked_causes {rca['ranked_causes']}, mong doi dung 1 cause rank 1")
        if o["resolution_actions"] != [action]:
            e(f"actions {o['resolution_actions']}, mong doi [{action}]")
        ptypes = {p["party_type"] for p in rca["responsible_parties"]}
        if want_party is None and ptypes:
            e(f"co responsible_parties {ptypes} cho issue khong loi")
        if want_party and ptypes != {want_party}:
            e(f"party_type {ptypes}, mong doi {want_party}")

        # du lieu tu CSV
        if oid not in orders.index:
            e(f"order {oid} khong ton tai")
            continue
        row = orders.loc[oid]
        its = items[items["order_id"] == oid]
        ps = pays[pays["order_id"] == oid]
        item_total = round(float(its["price"].sum()), 2) if len(its) else 0.0
        freight_total = round(float(its["freight_value"].sum()), 2) if len(its) else 0.0
        pay_total = round(float(ps["payment_value"].sum()), 2) if len(ps) else 0.0

        fin = o["financial_resolution"]
        if fin["currency"] != "BRL":
            e("currency khac BRL")
        for key in (
            "item_total_brl", "freight_total_brl", "payment_total_brl",
            "recommended_refund_brl",
        ):
            if not _is_finite_number(fin[key]):
                e(f"{key} khong phai so huu han")
                fin[key] = float("inf")
        if abs(fin["item_total_brl"] - item_total) > 0.005:
            e(f"item_total {fin['item_total_brl']} != {item_total}")
        if abs(fin["freight_total_brl"] - freight_total) > 0.005:
            e(f"freight_total {fin['freight_total_brl']} != {freight_total}")
        if abs(fin["payment_total_brl"] - pay_total) > 0.005:
            e(f"payment_total {fin['payment_total_brl']} != {pay_total}")

        want_refund = {
            "canceled_order_paid": pay_total,
            "unavailable_order_paid": pay_total,
            "late_delivery_seller": freight_total,
            "late_delivery_logistics": freight_total,
            "valid_split_payment": 0.0,
            "unsupported_late_claim": 0.0,
        }[issue]
        if abs(fin["recommended_refund_brl"] - round(want_refund, 2)) > 0.005:
            e(f"refund {fin['recommended_refund_brl']} != {round(want_refund, 2)}")

        # dieu kien rule theo thu tu uu tien
        delivered = row["order_delivered_customer_date"]
        estimated = row["order_estimated_delivery_date"]
        carrier = row["order_delivered_carrier_date"]
        late = (
            not pd.isna(delivered)
            and not pd.isna(estimated)
            and delivered > estimated
        )
        seller_late_ids = sorted(
            set(
                it["seller_id"]
                for _, it in its.iterrows()
                if not pd.isna(carrier)
                and not pd.isna(it["shipping_limit_date"])
                and carrier > it["shipping_limit_date"]
            )
        )
        pay_match = abs(pay_total - round(item_total + freight_total, 2)) <= TOL
        if row["order_status"] == "canceled" and pay_total > 0:
            expect = "canceled_order_paid"
        elif row["order_status"] == "unavailable" and pay_total > 0:
            expect = "unavailable_order_paid"
        elif late and seller_late_ids:
            expect = "late_delivery_seller"
        elif late:
            expect = "late_delivery_logistics"
        elif len(ps) >= 2 and pay_match:
            expect = "valid_split_payment"
        elif not late and pay_match:
            expect = "unsupported_late_claim"
        else:
            expect = None
            warns.append(f"{cid}: khong rule nao khop (status={row['order_status']})")
        if expect and issue != expect:
            e(f"primary_issue {issue}, tinh lai tu CSV ra {expect}")
        expected_parties = {
            "canceled_order_paid": [
                {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
            ],
            "unavailable_order_paid": [
                {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
            ],
            "late_delivery_seller": [
                {"party_type": "seller", "party_id": sid}
                for sid in seller_late_ids
            ],
            "late_delivery_logistics": [
                {
                    "party_type": "logistics_provider",
                    "party_id": "LOGISTICS_PROVIDER",
                }
            ],
            "valid_split_payment": [],
            "unsupported_late_claim": [],
        }[issue]
        if rca["responsible_parties"] != expected_parties:
            e(
                f"responsible_parties {rca['responsible_parties']} "
                f"!= {expected_parties}"
            )

        # evidence: dinh dang + ton tai
        valid_items = {f"{oid}:{int(i)}" for i in its["order_item_id"]}
        valid_pays = {f"{oid}:{int(s)}" for s in ps["payment_sequential"]}
        valid_sellers = set(its["seller_id"])
        expected_seller_evidence = (
            {f"seller:{sid}" for sid in seller_late_ids}
            if issue == "late_delivery_seller"
            else set()
        )
        expected_evidence = (
            {f"order:{oid}", f"policy:{cause}"}
            | {f"item:{x}" for x in valid_items}
            | {f"payment:{x}" for x in valid_pays}
            | expected_seller_evidence
        )
        if len(o["evidence_ids"]) != len(set(o["evidence_ids"])):
            e("evidence co ID trung lap")
        if set(o["evidence_ids"]) != expected_evidence:
            missing = sorted(expected_evidence - set(o["evidence_ids"]))
            extra = sorted(set(o["evidence_ids"]) - expected_evidence)
            e(f"evidence chua day du/chinh xac; thieu={missing}, du={extra}")
        for ev in o["evidence_ids"]:
            parts = ev.split(":")
            kind = parts[0]
            rest = ev[len(kind) + 1:]
            ok = (
                (kind == "order" and rest == oid)
                or (kind == "item" and rest in valid_items)
                or (kind == "payment" and rest in valid_pays)
                or (kind == "seller" and rest in valid_sellers)
                or (kind == "policy" and rest == cause)
            )
            if not ok:
                e(f"evidence khong hop le: {ev}")

        # affected entities ton tai
        if ae["order_ids"] != [oid]:
            e(f"order_ids {ae['order_ids']}")
        if len(ae["item_ids"]) != len(set(ae["item_ids"])):
            e("item_ids bi trung")
        if len(ae["seller_ids"]) != len(set(ae["seller_ids"])):
            e("seller_ids bi trung")
        if len(ae["payment_ids"]) != len(set(ae["payment_ids"])):
            e("payment_ids bi trung")
        if set(ae["item_ids"]) != valid_items:
            e(f"item_ids khong day du: {ae['item_ids']} != {sorted(valid_items)}")
        if set(ae["seller_ids"]) != valid_sellers:
            e(
                f"seller_ids khong day du: {ae['seller_ids']} "
                f"!= {sorted(valid_sellers)}"
            )
        if set(ae["payment_ids"]) != valid_pays:
            e(
                f"payment_ids khong day du: {ae['payment_ids']} "
                f"!= {sorted(valid_pays)}"
            )
        for x in ae["item_ids"]:
            if x not in valid_items:
                e(f"item_id la {x}")
        for x in ae["seller_ids"]:
            if x not in valid_sellers:
                e(f"seller_id la {x}")
        for x in ae["payment_ids"]:
            if x not in valid_pays:
                e(f"payment_id la {x}")

    print(f"Checked {len(out_files)} files: {len(errors)} errors, {len(warns)} warnings")
    for w in warns:
        print("WARN:", w)
    for err in errors:
        print("ERROR:", err)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
