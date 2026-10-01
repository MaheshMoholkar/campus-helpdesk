"""Shared fixtures.

Database tests need the docker-compose Postgres (`docker compose up -d postgres`)
or any Postgres with pgvector, given as TEST_DATABASE_URL. They use their own
database and are skipped when none is reachable.
"""

import os
from datetime import date
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from apps.api.auth import TokenVerifier
from apps.api.chat import ChatService, ChatSettings
from apps.api.cli import load_all
from apps.api.db import make_pool, run_migrations
from apps.api.llm import Models
from apps.api.llm.fake import FakeEmbedder, FakeLLM
from apps.api.retrieval import RetrievalConfig
from apps.api.tools.student_records import StudentRecords

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://helpdesk:helpdesk@localhost:5433/helpdesk_test"
)
DATA_TODAY = date(2026, 10, 1)  # the day the invented data is written for (data/plan.yaml)
ROOT = Path(__file__).resolve().parents[1]


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
        conn.execute("DROP SCHEMA IF EXISTS student_api CASCADE")
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
def student_api(database_url, tmp_path_factory):
    from apps.student_api.main import Settings as StudentSettings
    from apps.student_api.main import create_app as create_student_app

    settings = StudentSettings(
        database_url=database_url,
        student_api_keys_dir=tmp_path_factory.mktemp("keys"),
        student_records_file=ROOT / "data" / "student_records.yaml",
    )
    with TestClient(create_student_app(settings), base_url="http://student-api") as client:
        yield client


@pytest.fixture(scope="session")
def login(student_api):
    tokens: dict[str, str] = {}

    def _login(username: str) -> str:
        if username not in tokens:
            response = student_api.post("/login", json={"username": username, "password": "password"})
            response.raise_for_status()
            tokens[username] = response.json()["access_token"]
        return tokens[username]

    return _login


@pytest.fixture(scope="session")
def verifier(student_api):
    key = student_api.app.state.key
    from cryptography.hazmat.primitives import serialization

    public_key = serialization.load_pem_private_key(key.private_pem, password=None).public_key()
    return TokenVerifier("campus-student-api", "campus-helpdesk", key_for_token=lambda _token: public_key)


@pytest.fixture(scope="session")
def chat_service(loaded, student_api):
    models = Models(embedder=FakeEmbedder(), llm=FakeLLM(), reranker=None)
    settings = ChatSettings(retrieval=RetrievalConfig(today=DATA_TODAY), include_retrieval_debug=True)
    return ChatService(loaded, models, StudentRecords(client=student_api), settings)
