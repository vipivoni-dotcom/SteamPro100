"""SQLite-хранилище."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    legal_entity TEXT, inn TEXT, website TEXT, contact TEXT, country TEXT,
    product_kind TEXT, contract TEXT, origin_documents TEXT,
    territory TEXT, license_term TEXT, refund_terms_text TEXT,
    checks_json TEXT NOT NULL,
    score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    decision_note TEXT NOT NULL,
    last_checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    product_type TEXT NOT NULL,
    ownership TEXT NOT NULL,
    line TEXT NOT NULL,
    price_rub REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    category_id INTEGER REFERENCES categories(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    legal_check TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('supplier', 'product')),
    owner_id INTEGER NOT NULL,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    number TEXT,
    issued_at TEXT,
    valid_until TEXT,
    note TEXT,
    file_name TEXT,
    stored_name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    channel TEXT NOT NULL,
    buyer_type TEXT NOT NULL CHECK (buyer_type IN ('individual', 'legal')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_rub REAL NOT NULL CHECK (unit_price_rub > 0),
    commission_rub REAL NOT NULL DEFAULT 0 CHECK (commission_rub >= 0),
    sold_at TEXT NOT NULL,
    receipt_number TEXT,
    refunded INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.environ.get("DSH_DB_PATH", "digitalstore.db")


def uploads_dir() -> str:
    return os.environ.get("DSH_UPLOADS_DIR", "uploads")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
