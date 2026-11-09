"""Учёт документов: договоры, подтверждения происхождения и права
перепродажи, закрывающие документы, лицензии."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".txt"}
EXPIRY_WARNING_DAYS = 30


class OwnerType(str, Enum):
    SUPPLIER = "supplier"
    PRODUCT = "product"


class DocType(str, Enum):
    CONTRACT = "contract"                    # договор
    ORIGIN = "origin"                        # подтверждение происхождения
    RESALE_PERMISSION = "resale_permission"  # письменное право перепродажи
    INVOICE = "invoice"                      # счёт / инвойс
    CLOSING = "closing"                      # акт / УПД
    LICENSE = "license"                      # лицензия / права на продукт
    AUTHORSHIP = "authorship"                # подтверждение авторства
    OTHER = "other"


class DocumentIn(BaseModel):
    owner_type: OwnerType
    owner_id: int
    doc_type: DocType
    title: str = Field(min_length=1, max_length=200)
    number: Optional[str] = None
    issued_at: Optional[date] = None
    valid_until: Optional[date] = None
    note: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def doc_status(valid_until: Optional[str], today: Optional[date] = None) -> str:
    if not valid_until:
        return "no_expiry"
    today = today or date.today()
    until = date.fromisoformat(valid_until)
    if until < today:
        return "expired"
    if until <= today + timedelta(days=EXPIRY_WARNING_DAYS):
        return "expiring"
    return "valid"


def doc_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data.pop("stored_name", None)
    data["status"] = doc_status(data["valid_until"])
    data["has_file"] = bool(data["file_name"])
    return data


def _ensure_owner(conn: sqlite3.Connection, owner_type: OwnerType, owner_id: int) -> None:
    table = "suppliers" if owner_type is OwnerType.SUPPLIER else "products"
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (owner_id,)).fetchone():
        raise HTTPException(404, f"{owner_type.value} {owner_id} не найден")


def _get(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Документ не найден")
    return row


@router.post("", status_code=201)
def create_document(body: DocumentIn):
    if body.issued_at and body.valid_until and body.valid_until < body.issued_at:
        raise HTTPException(422, "Срок действия раньше даты выдачи")
    with db.connect() as conn:
        _ensure_owner(conn, body.owner_type, body.owner_id)
        cur = conn.execute(
            """INSERT INTO documents (owner_type, owner_id, doc_type, title, number,
                   issued_at, valid_until, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.owner_type.value, body.owner_id, body.doc_type.value, body.title,
             body.number, body.issued_at and body.issued_at.isoformat(),
             body.valid_until and body.valid_until.isoformat(), body.note, _now()),
        )
        return doc_row(_get(conn, cur.lastrowid))


@router.get("")
def list_documents(owner_type: Optional[OwnerType] = None, owner_id: Optional[int] = None,
                   status: Optional[str] = None):
    sql, params = "SELECT * FROM documents WHERE 1=1", []
    if owner_type:
        sql += " AND owner_type = ?"
        params.append(owner_type.value)
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    with db.connect() as conn:
        docs = [doc_row(r) for r in conn.execute(sql + " ORDER BY id", params)]
    if status:
        docs = [d for d in docs if d["status"] == status]
    return docs


@router.get("/{doc_id}")
def get_document(doc_id: int):
    with db.connect() as conn:
        return doc_row(_get(conn, doc_id))


@router.post("/{doc_id}/file")
async def upload_file(doc_id: int, file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"Допустимые форматы: {', '.join(sorted(ALLOWED_EXT))}")
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ")
    with db.connect() as conn:
        old = _get(conn, doc_id)["stored_name"]
        os.makedirs(db.uploads_dir(), exist_ok=True)
        stored = f"doc-{doc_id}{ext}"  # имя на диске не зависит от имени от пользователя
        Path(db.uploads_dir(), stored).write_bytes(content)
        if old and old != stored:
            Path(db.uploads_dir(), old).unlink(missing_ok=True)
        conn.execute("UPDATE documents SET file_name = ?, stored_name = ? WHERE id = ?",
                     (Path(file.filename).name, stored, doc_id))
        return doc_row(_get(conn, doc_id))


@router.get("/{doc_id}/file")
def download_file(doc_id: int):
    with db.connect() as conn:
        row = _get(conn, doc_id)
    if not row["stored_name"]:
        raise HTTPException(404, "Файл не загружен")
    path = Path(db.uploads_dir(), row["stored_name"])
    if not path.exists():
        raise HTTPException(404, "Файл отсутствует на диске")
    return FileResponse(path, filename=row["file_name"])


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: int):
    with db.connect() as conn:
        row = _get(conn, doc_id)
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    if row["stored_name"]:
        Path(db.uploads_dir(), row["stored_name"]).unlink(missing_ok=True)
