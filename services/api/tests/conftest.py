import base64
import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="sushrut-test-"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}"
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DB_POOL"] = "null"  # asyncpg connections must not outlive per-test event loops
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["MASTER_KEY_B64"] = base64.b64encode(b"k" * 32).decode()
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["SUSHRUT_ENV"] = "dev"
os.environ["SMTP_HOST"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["SARVAM_API_KEY"] = ""
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"  # unreachable: tests never depend on a running model

import httpx  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

# Real rows as published on the PMBJP product MRP list (janaushadhi.gov.in), Nov 2026.
JA_ROWS = [
    {"productId": 1, "genericName": "Aceclofenac 100mg and Paracetamol 325mg Tablets",
     "groupName": "Analgesic/Antipyretic/Anti-Inflammatory", "drugCode": 1, "unitSize": "10's", "mrp": 10.32, "status": 1},
    {"productId": 686, "genericName": "Aceclofenac Tablets IP 100 mg",
     "groupName": "Analgesic/Antipyretic/Anti-Inflammatory", "drugCode": 2, "unitSize": "10's", "mrp": 8.25, "status": 1},
    {"productId": 1644, "genericName": "Pregabalin Capsules IP 75 mg", "groupName": "Central Nervous System (CNS)",
     "drugCode": 3, "unitSize": "10's", "mrp": 22.69, "status": 1},
    {"productId": 88, "genericName": "Etoricoxib Tablets IP 120 mg",
     "groupName": "Analgesic/Antipyretic/Anti-Inflammatory", "drugCode": 12, "unitSize": "10's", "mrp": 35.63, "status": 1},
    {"productId": 173, "genericName": "Ibuprofen 400mg and Paracetamol 325mg Tablets IP",
     "groupName": "Analgesic/Antipyretic/Anti-Inflammatory", "drugCode": 14, "unitSize": "10's", "mrp": 8.25, "status": 1},
    {"productId": 337, "genericName": "Ibuprofen Tablets IP 400 mg",
     "groupName": "Analgesic/Antipyretic/Anti-Inflammatory", "drugCode": 16, "unitSize": "15's", "mrp": 9.08, "status": 1},
]


@pytest.fixture(scope="session")
def tmpdir_path() -> Path:
    return _tmp


@pytest.fixture
async def client():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


@pytest.fixture
async def guest(client):
    r = await client.post("/auth/guest")
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    await client.post("/consents", json={"purposes": ["core_service", "history_storage"]}, headers=h)
    return h
