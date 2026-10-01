"""Signing keys, passwords and tokens for the mock student API.

This service plays the identity provider: it holds the private key and signs
login tokens. Anyone can fetch the public key (JWKS) to verify them.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

TOKEN_LIFETIME_SECONDS = 8 * 3600


@dataclass
class SigningKey:
    private_pem: bytes
    kid: str

    @property
    def public_jwk(self) -> dict:
        public_key = serialization.load_pem_private_key(self.private_pem, password=None).public_key()
        jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}


def load_or_create_key(directory: Path) -> SigningKey:
    """Load the RSA key, generating one on first run. The folder is git-ignored."""
    path = directory / "private.pem"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        path.write_bytes(pem)
        path.chmod(0o600)
    pem = path.read_bytes()
    # Key id = short hash of the key, so a rotated key gets a new id automatically.
    kid = hashlib.sha256(pem).hexdigest()[:16]
    return SigningKey(pem, kid)


def issue_token(
    key: SigningKey,
    issuer: str,
    audience: str,
    sub: str,
    role: str,
    college: str,
    lifetime: int = TOKEN_LIFETIME_SECONDS,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "role": role,
        "college": college,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(claims, key.private_pem, algorithm="RS256", headers={"kid": key.kid})


def read_token(key: SigningKey, token: str, issuer: str, audience: str) -> dict:
    public_key = serialization.load_pem_private_key(key.private_pem, password=None).public_key()
    return jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=audience,
        options={"require": ["exp", "sub"]},
    )


# scrypt from the standard library: slow on purpose, so stolen hashes are hard to crack.
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))
