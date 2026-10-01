"""Command-line helpers for the chat API.

uv run python -m apps.api.cli migrate
uv run python -m apps.api.cli load-data            # reference data + all documents
uv run python -m apps.api.cli load-data --fake     # same, with the fake embedder (offline)
"""

import argparse
import sys
from pathlib import Path

import psycopg
import yaml

from apps.api.config import Settings
from apps.api.db import make_pool, run_migrations
from apps.api.ingest import DocumentIn, ingest_document
from apps.api.llm import build_models

DATA = Path(__file__).resolve().parents[2] / "data"


def load_reference_data(conn: psycopg.Connection, plan_file: Path) -> None:
    plan = yaml.safe_load(plan_file.read_text())
    with conn.transaction():
        for college in plan["colleges"]:
            conn.execute(
                "INSERT INTO colleges (code, name) VALUES (%(code)s, %(name)s)"
                " ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name",
                college,
            )
        for office in plan["offices"]:
            conn.execute(
                """
                INSERT INTO offices (name, college_code, category, email, phone, hours)
                VALUES (%(name)s, %(college_code)s, %(category)s, %(email)s, %(phone)s, %(hours)s)
                ON CONFLICT (college_code, category) DO UPDATE SET name = EXCLUDED.name,
                    email = EXCLUDED.email, phone = EXCLUDED.phone, hours = EXCLUDED.hours
                """,
                office,
            )


def load_documents(documents_file: Path) -> list[DocumentIn]:
    return [DocumentIn(**item) for item in yaml.safe_load(documents_file.read_text())]


def load_all(conn: psycopg.Connection, embedder, data_dir: Path = DATA, quiet: bool = False) -> dict:
    load_reference_data(conn, data_dir / "plan.yaml")
    counts = {"created": 0, "replaced": 0, "unchanged": 0}
    for doc in load_documents(data_dir / "documents.yaml"):
        result = ingest_document(conn, embedder, doc)
        counts[result.status] += 1
        if not quiet:
            print(f"{result.status:9} {doc.slug} (v{result.version}, {result.chunks} chunks)")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apps.api.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    load = sub.add_parser("load-data")
    load.add_argument("--fake", action="store_true", help="use the fake embedder (no Ollama)")
    args = parser.parse_args(argv)

    settings = Settings()
    applied = run_migrations(settings.database_url)
    print(f"migrations applied: {applied or 'none pending'}")
    if args.command == "migrate":
        return 0

    if args.fake:
        settings.ai_backend = "fake"
    models = build_models(settings)
    pool = make_pool(settings.database_url)
    try:
        with pool.connection() as conn:
            counts = load_all(conn, models.embedder)
    finally:
        pool.close()
    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
