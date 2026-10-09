"""The one place where "who may see what" is decided (docs/spec.md section 10.4).

Rule: a chunk is visible if its audience is `public`, or its audience is one the
user's role may see and its college is the user's college or `all`.

Every search function takes a Scope and puts `scope_filter()` in its WHERE clause.
Nothing else in the codebase builds a visibility filter.
"""

from dataclasses import dataclass
from datetime import date

ALL_COLLEGES = "all"

# Audiences beyond `public` that each role may see.
_RESTRICTED_BY_ROLE: dict[str, tuple[str, ...]] = {
    "anonymous": (),
    "student": ("student",),
    "staff": ("student", "staff"),
}


@dataclass(frozen=True)
class Claims:
    """Who CampusERP says the user is (apps/api/erp.py builds these from /auth/me)."""

    sub: str  # "<college>:<CampusERP user id>"
    role: str  # "student" | "staff"
    college: str  # CampusERP institute code
    name: str | None = None
    person_kind: str | None = None  # "student" | "staff" | None (a login linked to neither)


@dataclass(frozen=True)
class Scope:
    role: str
    user_sub: str | None
    college: str | None
    restricted_audiences: tuple[str, ...]
    colleges: tuple[str, ...]


def build_scope(claims: Claims | None) -> Scope:
    """Turn token claims (or their absence) into a Scope. Unknown roles get nothing extra."""
    if claims is None:
        return Scope("anonymous", None, None, (), ())
    restricted = _RESTRICTED_BY_ROLE.get(claims.role)
    if restricted is None:
        raise ValueError(f"unknown role: {claims.role!r}")
    return Scope(claims.role, claims.sub, claims.college, restricted, (claims.college, ALL_COLLEGES))


def is_visible(scope: Scope, audience: str, college_code: str) -> bool:
    """The rule in plain Python. Used by tests as the reference for the SQL below."""
    if audience == "public":
        return True
    return audience in scope.restricted_audiences and college_code in scope.colleges


def scope_filter(scope: Scope, alias: str = "c", today: date | None = None) -> tuple[str, dict]:
    """SQL condition (and its parameters) for the chunks table.

    Covers visibility and validity: not expired, and the current version of its series.
    """
    condition = (
        f"({alias}.audience = 'public'"
        f" OR ({alias}.audience = ANY(%(scope_audiences)s)"
        f" AND {alias}.college_code = ANY(%(scope_colleges)s)))"
        f" AND {alias}.is_current"
        f" AND ({alias}.expires_on IS NULL OR {alias}.expires_on >= %(scope_today)s)"
    )
    params = {
        "scope_audiences": list(scope.restricted_audiences),
        "scope_colleges": list(scope.colleges),
        "scope_today": today or date.today(),
    }
    return condition, params
