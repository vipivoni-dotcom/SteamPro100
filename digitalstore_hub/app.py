"""DigitalStore Hub MVP 0.1 — каталог собственных цифровых продуктов
с встроенным юридическим фильтром НПД и реестром поставщиков.

Запуск: uvicorn app:app --reload
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import db
from legal import Ownership, ProductType, TaxRegime, check_product
from suppliers import CRITERIA, SupplierDecision, evaluate

VERSION = "0.1.0"


def current_regime() -> TaxRegime:
    return TaxRegime(os.environ.get("DSH_TAX_REGIME", TaxRegime.NPD.value).lower())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="DigitalStore Hub", version=VERSION, lifespan=lifespan)


# ---------- Схемы ----------

class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    product_type: ProductType
    ownership: Ownership
    price_rub: float = Field(gt=0)
    category_id: Optional[int] = None
    supplier_id: Optional[int] = None


class ProductUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    price_rub: Optional[float] = Field(default=None, gt=0)
    category_id: Optional[int] = None
    status: Optional[str] = Field(default=None, pattern=r"^(draft|active|archived)$")


class SupplierChecks(BaseModel):
    origin_confirmed: bool = False
    resale_rights: bool = False
    contract_documents: bool = False
    api_delivery: bool = False
    product_verified: bool = False
    refund_terms: bool = False
    territory_ok: bool = False


class SupplierIn(BaseModel):
    name: str = Field(min_length=1)
    legal_entity: Optional[str] = None
    inn: Optional[str] = Field(default=None, pattern=r"^(\d{10}|\d{12})$")
    website: Optional[str] = None
    contact: Optional[str] = None
    country: Optional[str] = None
    product_kind: Optional[str] = None
    contract: Optional[str] = None
    origin_documents: Optional[str] = None
    territory: Optional[str] = None
    license_term: Optional[str] = None
    refund_terms_text: Optional[str] = None
    checks: SupplierChecks = SupplierChecks()


# ---------- Вспомогательное ----------

def product_row(row: sqlite3.Row) -> dict:
    return dict(row)


def supplier_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["checks"] = json.loads(data.pop("checks_json"))
    return data


def ensure_category(conn: sqlite3.Connection, category_id: Optional[int]) -> None:
    if category_id is not None and not conn.execute(
        "SELECT 1 FROM categories WHERE id = ?", (category_id,)
    ).fetchone():
        raise HTTPException(404, f"Категория {category_id} не найдена")


# ---------- Служебное ----------

@app.get("/")
def root():
    regime = current_regime()
    return {
        "app": "DigitalStore Hub",
        "version": VERSION,
        "tax_regime": regime.value,
        "line_a_own_products": "enabled",
        "line_b_third_party": "blocked" if regime is TaxRegime.NPD else "requires approved supplier",
        "docs": "/docs",
    }


# ---------- Категории ----------

@app.post("/categories", status_code=201)
def create_category(body: CategoryIn):
    with db.connect() as conn:
        try:
            cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (body.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Категория «{body.name}» уже существует")
        return {"id": cur.lastrowid, "name": body.name}


@app.get("/categories")
def list_categories():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY name")]


# ---------- Юридическая проверка ----------

@app.post("/legal/check")
def legal_check(product_type: ProductType, ownership: Ownership):
    """Проверить сценарий без создания товара."""
    d = check_product(product_type, ownership, current_regime())
    return {"allowed": d.allowed, "line": d.line, "message": d.message,
            "tax_regime": current_regime().value}


# ---------- Товары ----------

@app.post("/products", status_code=201)
def create_product(body: ProductIn):
    regime = current_regime()
    with db.connect() as conn:
        ensure_category(conn, body.category_id)

        supplier_approved = False
        if body.supplier_id is not None:
            sup = conn.execute("SELECT decision FROM suppliers WHERE id = ?",
                               (body.supplier_id,)).fetchone()
            if not sup:
                raise HTTPException(404, f"Поставщик {body.supplier_id} не найден")
            supplier_approved = sup["decision"] == SupplierDecision.CANDIDATE.value

        decision = check_product(body.product_type, body.ownership, regime, supplier_approved)
        if not decision.allowed:
            raise HTTPException(422, {"legal_check": decision.message, "line": decision.line})

        if conn.execute("SELECT 1 FROM products WHERE sku = ?", (body.sku,)).fetchone():
            raise HTTPException(409, f"SKU {body.sku} уже существует")

        cur = conn.execute(
            """INSERT INTO products (sku, title, description, product_type, ownership, line,
                   price_rub, category_id, supplier_id, legal_check, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.sku, body.title, body.description, body.product_type.value,
             body.ownership.value, decision.line, body.price_rub, body.category_id,
             body.supplier_id, decision.message, now()),
        )
        row = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
        return product_row(row)


@app.get("/products")
def list_products(status: Optional[str] = None, category_id: Optional[int] = None):
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if category_id is not None:
        sql += " AND category_id = ?"
        params.append(category_id)
    with db.connect() as conn:
        return [product_row(r) for r in conn.execute(sql + " ORDER BY id", params)]


@app.get("/products/{product_id}")
def get_product(product_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Товар не найден")
        return product_row(row)


@app.patch("/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate):
    changes = body.model_dump(exclude_unset=True)
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            raise HTTPException(404, "Товар не найден")
        ensure_category(conn, changes.get("category_id"))
        if changes:
            cols = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE products SET {cols} WHERE id = ?",
                         (*changes.values(), product_id))
        return product_row(conn.execute("SELECT * FROM products WHERE id = ?",
                                        (product_id,)).fetchone())


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int):
    with db.connect() as conn:
        if conn.execute("DELETE FROM products WHERE id = ?", (product_id,)).rowcount == 0:
            raise HTTPException(404, "Товар не найден")


# ---------- Поставщики ----------

@app.get("/suppliers/criteria")
def supplier_criteria():
    return [{"key": k, "points": p, "title": t} for k, (p, t) in CRITERIA.items()]


@app.post("/suppliers", status_code=201)
def create_supplier(body: SupplierIn):
    checks = body.checks.model_dump()
    total, decision, note = evaluate(checks)
    fields = body.model_dump(exclude={"checks"})
    with db.connect() as conn:
        cur = conn.execute(
            f"""INSERT INTO suppliers ({", ".join(fields)}, checks_json, score, decision,
                    decision_note, last_checked_at)
                VALUES ({", ".join("?" * len(fields))}, ?, ?, ?, ?, ?)""",
            (*fields.values(), json.dumps(checks), total, decision.value, note, now()),
        )
        row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (cur.lastrowid,)).fetchone()
        result = supplier_row(row)
    if current_regime() is TaxRegime.NPD:
        result["regime_note"] = ("На НПД закупка и продажа товаров поставщика заблокированы;"
                                 " запись ведётся только для исследования.")
    return result


@app.put("/suppliers/{supplier_id}/checks")
def recheck_supplier(supplier_id: int, checks: SupplierChecks):
    data = checks.model_dump()
    total, decision, note = evaluate(data)
    with db.connect() as conn:
        updated = conn.execute(
            """UPDATE suppliers SET checks_json = ?, score = ?, decision = ?,
                   decision_note = ?, last_checked_at = ? WHERE id = ?""",
            (json.dumps(data), total, decision.value, note, now(), supplier_id),
        ).rowcount
        if not updated:
            raise HTTPException(404, "Поставщик не найден")
        return supplier_row(conn.execute("SELECT * FROM suppliers WHERE id = ?",
                                         (supplier_id,)).fetchone())


@app.get("/suppliers")
def list_suppliers():
    with db.connect() as conn:
        return [supplier_row(r) for r in conn.execute("SELECT * FROM suppliers ORDER BY id")]


@app.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Поставщик не найден")
        return supplier_row(row)
