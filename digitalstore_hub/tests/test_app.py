GUIDE = {
    "sku": "GUIDE-001",
    "title": "Авторская инструкция по резервному копированию",
    "product_type": "ebook",
    "ownership": "own_created",
    "price_rub": 490,
}

FULL_CHECKS = {k: True for k in (
    "origin_confirmed", "resale_rights", "contract_documents", "api_delivery",
    "product_verified", "refund_terms", "territory_ok")}


def test_create_own_product(client):
    r = client.post("/products", json=GUIDE)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == 1 and body["status"] == "draft" and body["line"] == "A"
    assert body["legal_check"].startswith("Предварительно допустимо")


def test_duplicate_sku(client):
    assert client.post("/products", json=GUIDE).status_code == 201
    assert client.post("/products", json=GUIDE).status_code == 409


def test_resale_blocked_on_npd(client):
    r = client.post("/products", json={**GUIDE, "sku": "KEY-1",
                                       "product_type": "game_key", "ownership": "resale"})
    assert r.status_code == 422
    assert "Заблокировано на НПД" in r.json()["detail"]["legal_check"]


def test_agency_blocked_on_npd(client):
    r = client.post("/products", json={**GUIDE, "sku": "AG-1", "ownership": "agency"})
    assert r.status_code == 422
    assert "посредническая" in r.json()["detail"]["legal_check"]


def test_third_party_type_blocked_even_if_marked_own(client):
    r = client.post("/products", json={**GUIDE, "sku": "GC-1",
                                       "product_type": "gift_card"})
    assert r.status_code == 422


def test_resale_blocked_on_npd_even_with_good_supplier(client):
    sid = client.post("/suppliers", json={"name": "S", "checks": FULL_CHECKS}).json()["id"]
    r = client.post("/products", json={**GUIDE, "sku": "KEY-2", "product_type": "license_key",
                                       "ownership": "resale", "supplier_id": sid})
    assert r.status_code == 422


def test_resale_on_ooo_requires_candidate_supplier(client, monkeypatch):
    monkeypatch.setenv("DSH_TAX_REGIME", "ooo")
    payload = {**GUIDE, "sku": "KEY-3", "product_type": "license_key", "ownership": "resale"}
    assert client.post("/products", json=payload).status_code == 422
    weak = client.post("/suppliers", json={"name": "W", "checks": {
        "origin_confirmed": True, "resale_rights": True}}).json()["id"]
    assert client.post("/products", json={**payload, "supplier_id": weak}).status_code == 422
    good = client.post("/suppliers", json={"name": "G", "checks": FULL_CHECKS}).json()["id"]
    r = client.post("/products", json={**payload, "supplier_id": good})
    assert r.status_code == 201 and r.json()["line"] == "B"


def test_supplier_scoring(client):
    r = client.post("/suppliers", json={"name": "Full", "checks": FULL_CHECKS}).json()
    assert r["score"] == 100 and r["decision"] == "candidate" and "regime_note" in r

    no_rights = {**FULL_CHECKS, "resale_rights": False}
    r = client.post("/suppliers", json={"name": "NoRights", "checks": no_rights}).json()
    assert r["decision"] == "rejected"

    no_origin = {**FULL_CHECKS, "origin_confirmed": False}
    r = client.post("/suppliers", json={"name": "NoOrigin", "checks": no_origin}).json()
    assert r["decision"] == "rejected"

    mid = {**FULL_CHECKS, "api_delivery": False}  # 85
    r = client.post("/suppliers", json={"name": "Mid", "checks": mid}).json()
    assert r["score"] == 85 and r["decision"] == "request_documents"

    low = {**mid, "product_verified": False, "refund_terms": False}  # 65
    sid = client.post("/suppliers", json={"name": "Low", "checks": low}).json()["id"]
    assert client.get(f"/suppliers/{sid}").json()["decision"] == "rejected"

    r = client.put(f"/suppliers/{sid}/checks", json=FULL_CHECKS).json()
    assert r["decision"] == "candidate"


def test_categories_and_update(client):
    cid = client.post("/categories", json={"name": "Инструкции"}).json()["id"]
    assert client.post("/categories", json={"name": "Инструкции"}).status_code == 409
    pid = client.post("/products", json={**GUIDE, "category_id": cid}).json()["id"]
    assert client.post("/products", json={**GUIDE, "sku": "X", "category_id": 999}).status_code == 404
    r = client.patch(f"/products/{pid}", json={"status": "active", "price_rub": 590})
    assert r.json()["status"] == "active" and r.json()["price_rub"] == 590
    assert len(client.get("/products", params={"status": "active"}).json()) == 1
    assert client.delete(f"/products/{pid}").status_code == 204
    assert client.get(f"/products/{pid}").status_code == 404


def test_legal_check_endpoint(client):
    r = client.post("/legal/check", params={"product_type": "template",
                                            "ownership": "own_created"}).json()
    assert r["allowed"] is True
    r = client.post("/legal/check", params={"product_type": "subscription",
                                            "ownership": "resale"}).json()
    assert r["allowed"] is False
