"""Database access for the chat API: migrations and a connection pool."""

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from apps.dbmigrate import migrate

SCHEMA = "helpdesk"
MIGRATIONS = Path(__file__).parent / "migrations"


def run_migrations(database_url: str) -> list[str]:
    return migrate(database_url, SCHEMA, MIGRATIONS, extra_search_path=", public")


def _configure(conn: psycopg.Connection) -> None:
    # Teach psycopg the pgvector type. The lookup opens a transaction, and the
    # pool wants connections handed back idle, hence the commit.
    register_vector(conn)
    conn.commit()


def make_pool(database_url: str) -> ConnectionPool:
    """Pool whose connections see the helpdesk schema first and return rows as dicts."""
    return ConnectionPool(
        database_url,
        kwargs={"options": f"-c search_path={SCHEMA},public", "row_factory": dict_row},
        configure=_configure,
        min_size=1,
        max_size=5,
        open=True,
    )
