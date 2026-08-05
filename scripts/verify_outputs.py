"""Kiem tra doc lap 50 file output truoc khi nop.

Doc truc tiep CSV (khong dung lai policy_engine cua pipeline) va kiem tra:
schema, gioi han so luong, dinh dang + su ton tai cua evidence ID, so tien,
va dieu kien rule theo README muc 4.

Chay: python scripts/verify_outputs.py
"""
import json
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
    out_files = sorted((ROOT / "output").glob("EC_*.json"))
    if len(out_files) != 50:
        errors.append(f"output/ co {len(out_files)} file, can dung 50")

    for path in out_files:
        cid = path.stem
        o = json.loads(path.read_text(encoding="utf-8"))
        e = lambda msg: errors.append(f"{cid}: {msg}")

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
        if not (0 <= conf <= 1):
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
