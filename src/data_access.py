"""Tool truy xuat du lieu Olist: load CSV va dung fact sheet cho tung order.

Day la lop "tool" deterministic ma cac agent goi de lay du lieu co the
kiem chung, thay vi tin vao noi dung khieu nai cua khach hang.
"""
import pandas as pd

from . import config

_orders = None
_items = None
_payments = None


def load_data():
    """Load 3 bang can thiet cho nghiep vu (orders, order_items, order_payments)."""
    global _orders, _items, _payments
    if _orders is not None:
        return
    _orders = pd.read_csv(
        config.DATA_DIR / "olist_orders_dataset.csv",
        parse_dates=[
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    ).set_index("order_id", drop=False)
    _items = pd.read_csv(
        config.DATA_DIR / "olist_order_items_dataset.csv",
        parse_dates=["shipping_limit_date"],
    )
    _payments = pd.read_csv(config.DATA_DIR / "olist_order_payments_dataset.csv")


def _ts(value):
    """Chuyen Timestamp -> chuoi ISO, NaT -> None."""
    if pd.isna(value):
        return None
    return value.isoformat()


def order_exists(order_id: str) -> bool:
    load_data()
    return order_id in _orders.index


def get_order_facts(order_id: str) -> dict:
    """Dung fact sheet day du cho mot order: trang thai, items, payments,
    tong tien va cac phep so sanh thoi gian da tinh san."""
    load_data()
    row = _orders.loc[order_id]

    items_df = _items[_items["order_id"] == order_id].sort_values("order_item_id")
    pays_df = _payments[_payments["order_id"] == order_id].sort_values("payment_sequential")

    carrier_ts = row["order_delivered_carrier_date"]
    delivered_ts = row["order_delivered_customer_date"]
    estimated_ts = row["order_estimated_delivery_date"]

    items = []
    sellers_past_limit = []
    for _, it in items_df.iterrows():
        # Quy uoc README: seller bi coi la ban giao muon neu
        # order_delivered_carrier_date > shipping_limit_date cua item thuoc seller do.
        if pd.isna(carrier_ts) or pd.isna(it["shipping_limit_date"]):
            after_limit = None
        else:
            after_limit = bool(carrier_ts > it["shipping_limit_date"])
        if after_limit and it["seller_id"] not in sellers_past_limit:
            sellers_past_limit.append(it["seller_id"])
        items.append(
            {
                "order_item_id": int(it["order_item_id"]),
                "product_id": it["product_id"],
                "seller_id": it["seller_id"],
                "shipping_limit_date": _ts(it["shipping_limit_date"]),
                "price": round(float(it["price"]), 2),
                "freight_value": round(float(it["freight_value"]), 2),
                "carrier_after_limit": after_limit,
            }
        )

    payments = [
        {
            "payment_sequential": int(p["payment_sequential"]),
            "payment_type": p["payment_type"],
            "payment_installments": int(p["payment_installments"]),
            "payment_value": round(float(p["payment_value"]), 2),
        }
        for _, p in pays_df.iterrows()
    ]

    item_total = round(float(items_df["price"].sum()), 2) if len(items_df) else 0.0
    freight_total = round(float(items_df["freight_value"].sum()), 2) if len(items_df) else 0.0
    payment_total = round(float(pays_df["payment_value"].sum()), 2) if len(pays_df) else 0.0

    if pd.isna(delivered_ts) or pd.isna(estimated_ts):
        delivered_after_estimate = None
    else:
        delivered_after_estimate = bool(delivered_ts > estimated_ts)

    payment_matches = (
        abs(payment_total - round(item_total + freight_total, 2))
        <= config.PAYMENT_TOLERANCE_BRL
    )
    seller_handoff_assessment_complete = bool(items) and carrier_ts is not None
    if pd.isna(carrier_ts):
        seller_handoff_assessment_complete = False
    if any(item["shipping_limit_date"] is None for item in items):
        seller_handoff_assessment_complete = False

    return {
        "order_id": order_id,
        "order_status": row["order_status"],
        "purchase_ts": _ts(row["order_purchase_timestamp"]),
        "carrier_ts": _ts(carrier_ts),
        "delivered_ts": _ts(delivered_ts),
        "estimated_ts": _ts(estimated_ts),
        "items": items,
        "payments": payments,
        "n_items": len(items),
        "n_payments": len(payments),
        "item_total": item_total,
        "freight_total": freight_total,
        "payment_total": payment_total,
        "delivered_after_estimate": delivered_after_estimate,
        "payment_matches_order_value": bool(payment_matches),
        "sellers_past_limit": sellers_past_limit,
        "seller_handoff_assessment_complete": bool(
            seller_handoff_assessment_complete
        ),
    }
