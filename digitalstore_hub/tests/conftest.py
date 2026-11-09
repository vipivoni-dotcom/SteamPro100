import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("DSH_TAX_REGIME", raising=False)
    from app import app
    with TestClient(app) as c:
        yield c
