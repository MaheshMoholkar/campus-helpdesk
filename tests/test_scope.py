"""The visibility rule (docs/spec.md sections 2 and 10.4), checked exhaustively."""

import itertools

import pytest

from apps.api.scope import Claims, build_scope, is_visible

COLLEGES = ("coe", "cas", "com")
AUDIENCES = ("public", "student", "staff")
DOC_COLLEGES = (*COLLEGES, "all")


def expected(role: str, user_college: str | None, audience: str, doc_college: str) -> bool:
    """The rule written out a second, independent way, straight from the spec table."""
    if audience == "public":
        return True  # public documents are visible to everyone, from every college
    if role == "anonymous":
        return False
    allowed = {"student": {"student"}, "staff": {"student", "staff"}}[role]
    return audience in allowed and doc_college in (user_college, "all")


USERS = [("anonymous", None)] + [(role, c) for role in ("student", "staff") for c in COLLEGES]


@pytest.mark.parametrize(("role", "college"), USERS)
def test_full_matrix(role, college):
    claims = None if role == "anonymous" else Claims(sub="X", role=role, college=college)
    scope = build_scope(claims)
    for audience, doc_college in itertools.product(AUDIENCES, DOC_COLLEGES):
        assert is_visible(scope, audience, doc_college) == expected(role, college, audience, doc_college), (
            role,
            college,
            audience,
            doc_college,
        )


def test_logging_in_never_shows_less_than_anonymous():
    anonymous = build_scope(None)
    for role, college in USERS[1:]:
        scope = build_scope(Claims("X", role, college))
        for audience, doc_college in itertools.product(AUDIENCES, DOC_COLLEGES):
            if is_visible(anonymous, audience, doc_college):
                assert is_visible(scope, audience, doc_college)


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError):
        build_scope(Claims("X", "admin", "coe"))
