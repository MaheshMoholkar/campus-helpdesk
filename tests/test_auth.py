"""Token verification: the chat API must accept only tokens the student API signed."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from apps.api.auth import InvalidToken, TokenVerifier
from apps.student_api.security import issue_token, load_or_create_key

ISS, AUD = "campus-student-api", "campus-helpdesk"


@pytest.fixture
def key(tmp_path):
    return load_or_create_key(tmp_path)


@pytest.fixture
def verifier(key):
    public = serialization.load_pem_private_key(key.private_pem, password=None).public_key()
    return TokenVerifier(ISS, AUD, key_for_token=lambda _t: public)


def test_valid_token(key, verifier):
    claims = verifier.verify(issue_token(key, ISS, AUD, "S1001", "student", "coe"))
    assert (claims.sub, claims.role, claims.college) == ("S1001", "student", "coe")


@pytest.mark.parametrize(
    "token_args",
    [
        {"issuer": "someone-else"},
        {"audience": "another-app"},
        {"lifetime": -10},
        {"role": "admin"},
    ],
)
def test_rejected_tokens(key, verifier, token_args):
    args = {"issuer": ISS, "audience": AUD, "sub": "S1001", "role": "student", "college": "coe", **token_args}
    with pytest.raises(InvalidToken):
        verifier.verify(issue_token(key, **args))


def test_token_signed_by_another_key_is_rejected(tmp_path, verifier):
    other = load_or_create_key(tmp_path / "other")
    with pytest.raises(InvalidToken):
        verifier.verify(issue_token(other, ISS, AUD, "S1001", "student", "coe"))


def test_unsigned_and_symmetric_tokens_are_rejected(verifier):
    claims = {
        "iss": ISS,
        "aud": AUD,
        "sub": "S1001",
        "role": "staff",
        "college": "coe",
        "exp": int(time.time()) + 600,
    }
    for token in (jwt.encode(claims, None, algorithm="none"), jwt.encode(claims, "guess", algorithm="HS256")):
        with pytest.raises(InvalidToken):
            verifier.verify(token)
