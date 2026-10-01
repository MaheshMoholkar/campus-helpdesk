"""Tiny migration runner: numbered .sql files, applied once each, per schema.

Each service owns one schema and one migrations folder. Applied file names are
recorded in <schema>.schema_migrations.
"""

from pathlib import Path

import psycopg
from psycopg import sql


def migrate(database_url: str, schema: str, directory: Path, extra_search_path: str = "") -> list[str]:
    """Apply pending migrations and return the names that were applied."""
    applied_now: list[str] = []
    ident = sql.Identifier(schema)
    search_path = sql.SQL("SET LOCAL search_path TO {}" + extra_search_path).format(ident)

    with psycopg.connect(database_url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident))
        conn.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {}.schema_migrations ("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            ).format(ident)
        )
        conn.commit()

        for path in sorted(directory.glob("*.sql")):
            # One transaction per file. The advisory lock stops two processes
            # that start together from applying the same file twice.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"migrate:{schema}",))
            done = conn.execute(
                sql.SQL("SELECT 1 FROM {}.schema_migrations WHERE name = %s").format(ident),
                (path.name,),
            ).fetchone()
            if done:
                conn.rollback()
                continue
            conn.execute(search_path)
            conn.execute(path.read_text())  # no parameters, so several statements are allowed
            conn.execute(
                sql.SQL("INSERT INTO {}.schema_migrations (name) VALUES (%s)").format(ident),
                (path.name,),
            )
            conn.commit()
            applied_now.append(path.name)

    return applied_now
