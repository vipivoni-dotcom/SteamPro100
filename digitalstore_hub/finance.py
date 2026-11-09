"""Продажи и финансовая аналитика.

Налог НПД считается оценочно: 4 % с продаж физлицам, 6 % — юрлицам и ИП,
от полной суммы продажи (комиссия площадки базу не уменьшает). Налоговый
вычет 10 000 ₽ не учитывается. Возвраты исключаются (чек аннулируется).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import db

router = APIRouter(tags=["finance"])

NPD_ANNUAL_LIMIT_RUB = 2_400_000
NPD_RATES = {"individual": 0.04, "legal": 0.06}


class Channel(str, Enum):
    DIRECT = "direct"
    OZON = "ozon"
    YANDEX_MARKET = "yandex_market"
    WILDBERRIES = "wildberries"
    OTHER = "other"


class BuyerType(str, Enum):
    INDIVIDUAL = "individual"
    LEGAL = "legal"


class SaleIn(BaseModel):
    product_id: int
    channel: Channel = Channel.DIRECT
    buyer_type: BuyerType = BuyerType.INDIVIDUAL
    quantity: int = Field(default=1, gt=0)
    unit_price_rub: float = Field(gt=0)
    commission_rub: float = Field(default=0, ge=0)
    sold_at: date = Field(default_factory=date.today)
    receipt_number: Optional[str] = None
    note: Optional[str] = None


class SalePatch(BaseModel):
    receipt_number: Optional[str] = None
    refunded: Optional[bool] = None
    note: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sale_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["refunded"] = bool(data["refunded"])
    gross = data["quantity"] * data["unit_price_rub"]
    tax = 0.0 if data["refunded"] else gross * NPD_RATES[data["buyer_type"]]
    data["gross_rub"] = round(gross, 2)
    data["tax_estimate_rub"] = round(tax, 2)
    data["net_rub"] = 0.0 if data["refunded"] else round(gross - data["commission_rub"] - tax, 2)
    return data


def _get(conn: sqlite3.Connection, sale_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Продажа не найдена")
    return row


@router.post("/sales", status_code=201)
def create_sale(body: SaleIn):
    if body.sold_at > date.today():
        raise HTTPException(422, "Дата продажи в будущем")
    with db.connect() as conn:
        product = conn.execute("SELECT status FROM products WHERE id = ?",
                               (body.product_id,)).fetchone()
        if not product:
            raise HTTPException(404, f"Товар {body.product_id} не найден")
        if product["status"] == "archived":
            raise HTTPException(422, "Товар в архиве")
        if body.commission_rub > body.quantity * body.unit_price_rub:
            raise HTTPException(422, "Комиссия больше суммы продажи")
        cur = conn.execute(
            """INSERT INTO sales (product_id, channel, buyer_type, quantity, unit_price_rub,
                   commission_rub, sold_at, receipt_number, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.product_id, body.channel.value, body.buyer_type.value, body.quantity,
             body.unit_price_rub, body.commission_rub, body.sold_at.isoformat(),
             body.receipt_number, body.note, _now()),
        )
        return sale_row(_get(conn, cur.lastrowid))


@router.get("/sales")
def list_sales(product_id: Optional[int] = None, channel: Optional[Channel] = None):
    sql, params = "SELECT * FROM sales WHERE 1=1", []
    if product_id is not None:
        sql += " AND product_id = ?"
        params.append(product_id)
    if channel:
        sql += " AND channel = ?"
        params.append(channel.value)
    with db.connect() as conn:
        return [sale_row(r) for r in conn.execute(sql + " ORDER BY sold_at DESC, id DESC", params)]


@router.patch("/sales/{sale_id}")
def update_sale(sale_id: int, body: SalePatch):
    changes = body.model_dump(exclude_unset=True)
    with db.connect() as conn:
        _get(conn, sale_id)
        if "refunded" in changes:
            changes["refunded"] = int(bool(changes["refunded"]))
        if changes:
            cols = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE sales SET {cols} WHERE id = ?", (*changes.values(), sale_id))
        return sale_row(_get(conn, sale_id))


@router.get("/analytics/summary")
def summary(year: Optional[int] = None):
    year = year or date.today().year
    with db.connect() as conn:
        rows = [sale_row(r) for r in conn.execute(
            "SELECT * FROM sales WHERE substr(sold_at, 1, 4) = ?", (str(year),))]
        titles = {r["id"]: (r["sku"], r["title"])
                  for r in conn.execute("SELECT id, sku, title FROM products")}

    def bucket():
        return {"sales": 0, "units": 0, "gross_rub": 0.0, "commission_rub": 0.0,
                "tax_estimate_rub": 0.0, "net_rub": 0.0}

    total, refunds = bucket(), {"count": 0, "amount_rub": 0.0}
    by_product, by_channel, by_month = defaultdict(bucket), defaultdict(bucket), defaultdict(bucket)
    missing_receipts = 0

    for s in rows:
        if s["refunded"]:
            refunds["count"] += 1
            refunds["amount_rub"] += s["gross_rub"]
            continue
        if not s["receipt_number"]:
            missing_receipts += 1
        for b in (total, by_product[s["product_id"]], by_channel[s["channel"]],
                  by_month[s["sold_at"][:7]]):
            b["sales"] += 1
            b["units"] += s["quantity"]
            b["gross_rub"] += s["gross_rub"]
            b["commission_rub"] += s["commission_rub"]
            b["tax_estimate_rub"] += s["tax_estimate_rub"]
            b["net_rub"] += s["net_rub"]

    def rounded(b):
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in b.items()}

    products = []
    for pid, b in by_product.items():
        sku, title = titles.get(pid, ("?", "?"))
        products.append({"product_id": pid, "sku": sku, "title": title, **rounded(b)})
    products.sort(key=lambda p: p["gross_rub"], reverse=True)

    used = total["gross_rub"]
    return {
        "year": year,
        "total": rounded(total),
        "refunds": rounded(refunds),
        "sales_without_receipt": missing_receipts,
        "npd_limit": {
            "limit_rub": NPD_ANNUAL_LIMIT_RUB,
            "used_rub": round(used, 2),
            "used_pct": round(used / NPD_ANNUAL_LIMIT_RUB * 100, 2),
            "warning": used >= NPD_ANNUAL_LIMIT_RUB * 0.8,
        },
        "by_product": products,
        "by_channel": [{"channel": k, **rounded(v)} for k, v in sorted(by_channel.items())],
        "by_month": [{"month": k, **rounded(v)} for k, v in sorted(by_month.items())],
    }
