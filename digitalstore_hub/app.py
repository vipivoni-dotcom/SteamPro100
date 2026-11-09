"""DigitalStore Hub 0.2 — каталог собственных цифровых продуктов
с встроенным юридическим фильтром НПД, реестром поставщиков, учётом
документов, продаж и административной панелью.

Запуск: uvicorn app:app --reload  →  http://127.0.0.1:8000/admin
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import db
import documents
import finance
from legal import Ownership, ProductType, TaxRegime, check_product
from suppliers import CRITERIA, SupplierDecision, evaluate

VERSION = "0.2.0"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def current_regime() -> TaxRegime:
    return TaxRegime(os.environ.get("DSH_TAX_REGIME", TaxRegime.NPD.value).lower())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="DigitalStore Hub", version=VERSION, lifespan=lifespan)
app.include_router(documents.router)
app.include_router(finance.router)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Если задан DSH_ADMIN_PASSWORD — все запросы требуют HTTP Basic (логин admin)."""
    password = os.environ.get("DSH_ADMIN_PASSWORD")
    if password:
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = (secrets.compare_digest(user, "admin")
                      and secrets.compare_digest(pwd.encode(), password.encode()))
            except (binascii.Error, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Требуется авторизация", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="DigitalStore Hub"'})
    return await call_next(request)


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
        "admin": "/admin",
    }


@app.get("/admin", include_in_schema=False)
def admin_panel():
    return FileResponse(STATIC_DIR / "admin.html", media_type="text/html")


@app.get("/dashboard")
def dashboard():
    """Сводка для главной страницы админ-панели."""
    regime = current_regime()
    with db.connect() as conn:
        products = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM products GROUP BY status")}
        suppliers = {r["decision"]: r["n"] for r in conn.execute(
            "SELECT decision, COUNT(*) AS n FROM suppliers GROUP BY decision")}
        docs = [documents.doc_row(r) for r in conn.execute(
            "SELECT * FROM documents WHERE valid_until IS NOT NULL")]
    attention = [d for d in docs if d["status"] in ("expired", "expiring")]
    attention.sort(key=lambda d: d["valid_until"])
    stats = finance.summary()
    return {
        "version": VERSION,
        "tax_regime": regime.value,
        "line_b_blocked": regime is TaxRegime.NPD,
        "products": {"draft": products.get("draft", 0), "active": products.get("active", 0),
                     "archived": products.get("archived", 0)},
        "suppliers": {d.value: suppliers.get(d.value, 0) for d in SupplierDecision},
        "documents_attention": attention,
        "finance": {k: stats[k] for k in ("year", "total", "refunds",
                                          "sales_without_receipt", "npd_limit")},
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
        if not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            raise HTTPException(404, "Товар не найден")
        if conn.execute("SELECT 1 FROM sales WHERE product_id = ?", (product_id,)).fetchone():
            raise HTTPException(409, "По товару есть продажи — переведите его в архив")
        if conn.execute("SELECT 1 FROM documents WHERE owner_type = 'product' AND owner_id = ?",
                        (product_id,)).fetchone():
            raise HTTPException(409, "К товару привязаны документы — сначала удалите их")
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))


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
