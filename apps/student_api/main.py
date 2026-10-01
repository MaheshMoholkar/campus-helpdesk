"""Mock student API: stands in for a university ERP (docs/spec.md sections 5 and 11.6).

Run from the repository root:
    uv run uvicorn apps.student_api.main:app --reload --port 8001
"""

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import psycopg
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from apps.dbmigrate import migrate
from apps.student_api.security import (
    TOKEN_LIFETIME_SECONDS,
    check_password,
    hash_password,
    issue_token,
    load_or_create_key,
    read_token,
)

SCHEMA = "student_api"
HERE = Path(__file__).parent
ROOT = HERE.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env.lab", ".env"), extra="ignore")

    database_url: str = "postgresql://helpdesk:helpdesk@localhost:5433/helpdesk"
    jwt_issuer: str = "campus-student-api"
    jwt_audience: str = "campus-helpdesk"
    student_api_keys_dir: Path = HERE / "keys"
    student_records_file: Path = ROOT / "data" / "student_records.yaml"
    cors_origins: list[str] = ["http://localhost:5173"]


def seed(conn: psycopg.Connection, records_file: Path) -> None:
    """Load the invented student records. Safe to run repeatedly: rows are replaced."""
    data = yaml.safe_load(records_file.read_text())
    with conn.transaction():
        for user in data["users"]:
            existing = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()
            password_hash = existing["password_hash"] if existing else hash_password(user["password"])
            conn.execute(
                """
                INSERT INTO users (id, kind, name, college_code, program, year, section, password_hash)
                VALUES (%(id)s, %(kind)s, %(name)s, %(college)s, %(program)s, %(year)s, %(section)s, %(hash)s)
                ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name,
                    college_code = EXCLUDED.college_code, program = EXCLUDED.program,
                    year = EXCLUDED.year, section = EXCLUDED.section
                """,
                {**{"program": None, "year": None, "section": None}, **user, "hash": password_hash},
            )
        conn.execute("DELETE FROM fee_dues")
        conn.execute("DELETE FROM attendance")
        conn.execute("DELETE FROM timetable")
        for fee in data["fee_dues"]:
            conn.execute(
                "INSERT INTO fee_dues (student_id, term, amount_due, due_date, status)"
                " VALUES (%(student_id)s, %(term)s, %(amount_due)s, %(due_date)s, %(status)s)",
                fee,
            )
        for row in data["attendance"]:
            conn.execute(
                "INSERT INTO attendance (student_id, course_code, attended, total, as_of)"
                " VALUES (%(student_id)s, %(course_code)s, %(attended)s, %(total)s, %(as_of)s)",
                row,
            )
        for row in data["timetable"]:
            conn.execute(
                "INSERT INTO timetable (college_code, program, year, section, day, slot, course_code, room)"
                " VALUES (%(college)s, %(program)s, %(year)s, %(section)s, %(day)s, %(slot)s,"
                "         %(course_code)s, %(room)s)",
                row,
            )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate(settings.database_url, SCHEMA, HERE / "migrations")
        pool = ConnectionPool(
            settings.database_url,
            kwargs={"options": f"-c search_path={SCHEMA}", "row_factory": dict_row},
            min_size=1,
            max_size=4,
            open=True,
        )
        with pool.connection() as conn:
            seed(conn, settings.student_records_file)
        app.state.pool = pool
        app.state.key = load_or_create_key(settings.student_api_keys_dir)
        yield
        pool.close()

    app = FastAPI(title="Mock Student API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def conn(request: Request):
        with request.app.state.pool.connection() as connection:
            yield connection

    def current_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict:
        """Who is calling, taken only from the signed token."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "login required")
        try:
            claims = read_token(request.app.state.key, token, settings.jwt_issuer, settings.jwt_audience)
        except Exception as exc:
            raise HTTPException(401, "invalid token") from exc
        return claims

    def current_student(claims: Annotated[dict, Depends(current_user)]) -> str:
        if claims.get("role") != "student":
            raise HTTPException(403, "student accounts only")
        return claims["sub"]

    @app.get("/health")
    def health(db: Annotated[psycopg.Connection, Depends(conn)]):
        db.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/.well-known/jwks.json")
    def jwks(request: Request):
        return {"keys": [request.app.state.key.public_jwk]}

    class LoginIn(BaseModel):
        username: str
        password: str

    @app.post("/login")
    def login(body: LoginIn, request: Request, db: Annotated[psycopg.Connection, Depends(conn)]):
        user = db.execute("SELECT * FROM users WHERE id = %s", (body.username.upper(),)).fetchone()
        if not user or not check_password(body.password, user["password_hash"]):
            raise HTTPException(401, "wrong username or password")
        token = issue_token(
            request.app.state.key,
            settings.jwt_issuer,
            settings.jwt_audience,
            sub=user["id"],
            role=user["kind"],
            college=user["college_code"],
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": TOKEN_LIFETIME_SECONDS,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "role": user["kind"],
                "college": user["college_code"],
            },
        }

    @app.get("/me/fees")
    def fees(
        student_id: Annotated[str, Depends(current_student)], db: Annotated[psycopg.Connection, Depends(conn)]
    ):
        rows = db.execute(
            "SELECT term, amount_due, due_date, status FROM fee_dues WHERE student_id = %s ORDER BY due_date",
            (student_id,),
        ).fetchall()
        return [{**row, "amount_due": float(row["amount_due"]), "currency": "INR"} for row in rows]

    @app.get("/me/attendance")
    def attendance(
        student_id: Annotated[str, Depends(current_student)], db: Annotated[psycopg.Connection, Depends(conn)]
    ):
        rows = db.execute(
            "SELECT course_code, attended, total, as_of FROM attendance WHERE student_id = %s ORDER BY course_code",
            (student_id,),
        ).fetchall()
        return [
            {**row, "percent": round(100 * row["attended"] / row["total"], 1) if row["total"] else None}
            for row in rows
        ]

    @app.get("/me/timetable")
    def timetable(
        student_id: Annotated[str, Depends(current_student)], db: Annotated[psycopg.Connection, Depends(conn)]
    ):
        return db.execute(
            """
            SELECT t.day, t.slot, t.course_code, t.room FROM timetable t
            JOIN users u ON u.college_code = t.college_code AND u.program = t.program
                        AND u.year = t.year AND u.section = t.section
            WHERE u.id = %s
            ORDER BY array_position(ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'], t.day),
                     t.slot
            """,
            (student_id,),
        ).fetchall()

    class BonafideIn(BaseModel):
        purpose: str = Field(min_length=1, max_length=200)

    @app.post("/me/bonafide-requests", status_code=201)
    def request_bonafide(
        body: BonafideIn,
        student_id: Annotated[str, Depends(current_student)],
        db: Annotated[psycopg.Connection, Depends(conn)],
    ):
        row = db.execute(
            "INSERT INTO bonafide_requests (student_id, purpose) VALUES (%s, %s)"
            " RETURNING id, purpose, status, created_at",
            (student_id, body.purpose),
        ).fetchone()
        return {**row, "id": str(row["id"]), "ready_by": _working_days_from_today(3).isoformat()}

    return app


def _working_days_from_today(days: int) -> date:
    from datetime import timedelta

    current = date.today()
    while days:
        current += timedelta(days=1)
        if current.weekday() < 6:  # Monday to Saturday are working days
            days -= 1
    return current


app = create_app()
