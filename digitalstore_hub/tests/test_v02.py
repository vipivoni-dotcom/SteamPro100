from datetime import date, timedelta

from documents import doc_status

GUIDE = {"sku": "GUIDE-001", "title": "Инструкция", "product_type": "ebook",
         "ownership": "own_created", "price_rub": 490}


def make_product(client, **kw):
    return client.post("/products", json={**GUIDE, **kw}).json()["id"]


def test_admin_page_served(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "DigitalStore Hub" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_doc_status():
    today = date(2026, 11, 9)
    assert doc_status(None, today) == "no_expiry"
    assert doc_status("2026-11-01", today) == "expired"
    assert doc_status("2026-11-30", today) == "expiring"
    assert doc_status("2027-06-01", today) == "valid"


def test_documents_crud_and_file(client, tmp_path):
    sid = client.post("/suppliers", json={"name": "S"}).json()["id"]
    soon = (date.today() + timedelta(days=5)).isoformat()
    r = client.post("/documents", json={"owner_type": "supplier", "owner_id": sid,
                                        "doc_type": "contract", "title": "Договор №1",
                                        "valid_until": soon})
    assert r.status_code == 201
    doc = r.json()
    assert doc["status"] == "expiring" and doc["has_file"] is False and "stored_name" not in doc

    assert client.post("/documents", json={"owner_type": "supplier", "owner_id": 999,
                                           "doc_type": "contract", "title": "X"}).status_code == 404
    assert client.post("/documents", json={"owner_type": "supplier", "owner_id": sid,
                                           "doc_type": "contract", "title": "X",
                                           "issued_at": "2026-05-01",
                                           "valid_until": "2026-01-01"}).status_code == 422

    bad = client.post(f"/documents/{doc['id']}/file", files={"file": ("x.exe", b"MZ")})
    assert bad.status_code == 415
    up = client.post(f"/documents/{doc['id']}/file",
                     files={"file": ("../../договор.pdf", b"%PDF-1.4 test")})
    assert up.status_code == 200 and up.json()["file_name"] == "договор.pdf"
    assert (tmp_path / "uploads" / f"doc-{doc['id']}.pdf").exists()
    got = client.get(f"/documents/{doc['id']}/file")
    assert got.status_code == 200 and got.content == b"%PDF-1.4 test"

    dash = client.get("/dashboard").json()
    assert [d["id"] for d in dash["documents_attention"]] == [doc["id"]]

    assert client.delete(f"/documents/{doc['id']}").status_code == 204
    assert not (tmp_path / "uploads" / f"doc-{doc['id']}.pdf").exists()
    assert client.get(f"/documents/{doc['id']}").status_code == 404


def test_sales_and_analytics(client):
    pid = make_product(client)
    pid2 = make_product(client, sku="TPL-1", product_type="template", price_rub=1000)
    today = date.today().isoformat()
    s1 = client.post("/sales", json={"product_id": pid, "quantity": 2, "unit_price_rub": 500,
                                     "commission_rub": 100, "channel": "ozon",
                                     "receipt_number": "R1", "sold_at": today}).json()
    assert s1["gross_rub"] == 1000 and s1["tax_estimate_rub"] == 40 and s1["net_rub"] == 860
    s2 = client.post("/sales", json={"product_id": pid2, "unit_price_rub": 1000,
                                     "buyer_type": "legal", "sold_at": today}).json()
    assert s2["tax_estimate_rub"] == 60
    s3 = client.post("/sales", json={"product_id": pid2, "unit_price_rub": 1000,
                                     "sold_at": today}).json()
    client.patch(f"/sales/{s3['id']}", json={"refunded": True})

    summary = client.get("/analytics/summary").json()
    t = summary["total"]
    assert t["sales"] == 2 and t["gross_rub"] == 2000 and t["commission_rub"] == 100
    assert t["tax_estimate_rub"] == 100 and t["net_rub"] == 1800
    assert summary["refunds"] == {"count": 1, "amount_rub": 1000}
    assert summary["sales_without_receipt"] == 1
    assert summary["npd_limit"]["used_rub"] == 2000 and summary["npd_limit"]["warning"] is False
    assert {(p["sku"], p["gross_rub"]) for p in summary["by_product"]} == \
        {("GUIDE-001", 1000), ("TPL-1", 1000)}
    assert {c["channel"] for c in summary["by_channel"]} == {"ozon", "direct"}
    assert len(summary["by_month"]) == 1

    client.patch(f"/sales/{s2['id']}", json={"receipt_number": "R2"})
    assert client.get("/analytics/summary").json()["sales_without_receipt"] == 0


def test_sale_validation(client):
    pid = make_product(client)
    assert client.post("/sales", json={"product_id": 999, "unit_price_rub": 1}).status_code == 404
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.post("/sales", json={"product_id": pid, "unit_price_rub": 1,
                                       "sold_at": future}).status_code == 422
    assert client.post("/sales", json={"product_id": pid, "unit_price_rub": 100,
                                       "commission_rub": 200}).status_code == 422
    client.patch(f"/products/{pid}", json={"status": "archived"})
    assert client.post("/sales", json={"product_id": pid, "unit_price_rub": 1}).status_code == 422


def test_npd_limit_warning(client):
    pid = make_product(client)
    client.post("/sales", json={"product_id": pid, "unit_price_rub": 2_000_000})
    lim = client.get("/analytics/summary").json()["npd_limit"]
    assert lim["warning"] is True and lim["used_pct"] == 83.33


def test_product_delete_guarded(client):
    pid = make_product(client)
    client.post("/sales", json={"product_id": pid, "unit_price_rub": 490})
    assert client.delete(f"/products/{pid}").status_code == 409
    pid2 = make_product(client, sku="G2")
    client.post("/documents", json={"owner_type": "product", "owner_id": pid2,
                                    "doc_type": "authorship", "title": "Авторство"})
    assert client.delete(f"/products/{pid2}").status_code == 409


def test_dashboard(client):
    make_product(client)
    client.post("/suppliers", json={"name": "S"})
    d = client.get("/dashboard").json()
    assert d["version"] == "0.2.0" and d["line_b_blocked"] is True
    assert d["products"]["draft"] == 1 and d["suppliers"]["rejected"] == 1


def test_basic_auth(client, monkeypatch):
    monkeypatch.setenv("DSH_ADMIN_PASSWORD", "s3cret")
    assert client.get("/admin").status_code == 401
    assert client.get("/products", auth=("admin", "wrong")).status_code == 401
    assert client.get("/products", auth=("admin", "s3cret")).status_code == 200
    r = client.get("/", headers={"Authorization": "Basic !!!"})
    assert r.status_code == 401
