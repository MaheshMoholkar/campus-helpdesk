"""Login token verification (docs/spec.md section 10.5).

The mock student API signs tokens with its private key. This service only ever
holds the public key, fetched from the JWKS endpoint, and checks signature,
issuer, audience and expiry.
"""

from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWKClient

from apps.api.scope import Claims


class InvalidToken(Exception):
    pass


class TokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_for_token: Callable[[str], Any] | None = None,
    ) -> None:
        """Pass `jwks_url` in production; `key_for_token` lets tests supply a key directly."""
        self._issuer = issuer
        self._audience = audience
        if key_for_token is not None:
            self._key_for_token = key_for_token
        elif jwks_url is not None:
            client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
            self._key_for_token = lambda token: client.get_signing_key_from_jwt(token).key
        else:
            raise ValueError("either jwks_url or key_for_token is required")

    def verify(self, token: str) -> Claims:
        try:
            payload = jwt.decode(
                token,
                self._key_for_token(token),
                algorithms=["RS256"],  # never let the token choose its own algorithm
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:  # bad signature, expired, wrong issuer, key fetch failed
            raise InvalidToken(str(exc)) from exc

        role, college = payload.get("role"), payload.get("college")
        if role not in ("student", "staff") or not isinstance(college, str) or not college:
            raise InvalidToken("token is missing a valid role or college")
        return Claims(sub=payload["sub"], role=role, college=college)
