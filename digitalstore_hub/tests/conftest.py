import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DSH_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("DSH_TAX_REGIME", raising=False)
    monkeypatch.delenv("DSH_ADMIN_PASSWORD", raising=False)
    from app import app
    with TestClient(app) as c:
        yield c
