"""Shared fixtures.

Database tests need the docker-compose Postgres (`docker compose up -d postgres`)
or any Postgres with pgvector, given as TEST_DATABASE_URL. Tests that call CampusERP
need its API running (ERP_TEST_URL, default http://localhost:8000) with demo data.
Each group is skipped when its service is not reachable.
"""

import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from apps.api.chat import ChatService, ChatSettings
from apps.api.cli import load_all
from apps.api.db import make_pool, run_migrations
from apps.api.erp import ErpClient, ErpCredentials, credentials_from_cookie_header
from apps.api.llm import Models
from apps.api.llm.fake import FakeEmbedder, FakeLLM
from apps.api.retrieval import RetrievalConfig

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://helpdesk:helpdesk@localhost:5433/helpdesk_test"
)
DATA_TODAY = date(2026, 10, 1)  # the day the invented data is written for (data/plan.yaml)
ROOT = Path(__file__).resolve().parents[1]
ERP_TEST_URL = os.environ.get("ERP_TEST_URL", "http://localhost:8000")
DEMO_PASSWORD = "campus-demo-password"  # CampusERP `make seed`
ERP_SESSIONS = ROOT / ".erp-sessions.json"  # demo sessions reused across runs (git-ignored)


def _reachable() -> bool:
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _reachable():
        pytest.skip(f"no test database at {TEST_DATABASE_URL}")
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS helpdesk CASCADE")
    run_migrations(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def pool(database_url):
    pool = make_pool(database_url)
    yield pool
    pool.close()


@pytest.fixture(scope="session")
def loaded(pool):
    """The full invented corpus, embedded with the fake embedder."""
    with pool.connection() as conn:
        load_all(conn, FakeEmbedder(), quiet=True)
    return pool


@pytest.fixture(scope="session")
def erp():
    """The real CampusERP API (`make dev-api` in the CampusERP repo, after `make seed`).

    Tests that need it are skipped when it is not running.
    """
    import httpx

    try:
        httpx.get(f"{ERP_TEST_URL}/health/live", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no CampusERP API at {ERP_TEST_URL}")
    return ErpClient(ERP_TEST_URL)


@pytest.fixture(scope="session")
def login(erp):
    """Log in to CampusERP; returns the Cookie header CampusERP's web app would forward."""
    cookies: dict[str, str] = {}

    def _login(email: str) -> str:
        if email not in cookies:
            creds = erp.reusable_login(email, DEMO_PASSWORD, ERP_SESSIONS)
            cookies[email] = f"{erp.session_cookie}={creds.session}; {erp.csrf_cookie}={creds.csrf}"
        return cookies[email]

    return _login


@pytest.fixture(scope="session")
def creds(login, erp):
    def _creds(email: str) -> ErpCredentials:
        return credentials_from_cookie_header(login(email), erp.session_cookie, erp.csrf_cookie)

    return _creds


@pytest.fixture(scope="session")
def chat_service(loaded, erp):
    models = Models(embedder=FakeEmbedder(), llm=FakeLLM(), reranker=None)
    settings = ChatSettings(retrieval=RetrievalConfig(today=DATA_TODAY), include_retrieval_debug=True)
    return ChatService(loaded, models, erp, settings)
