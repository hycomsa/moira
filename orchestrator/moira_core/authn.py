"""Identity / authentication (must-fix #2).

One contract — `verify a JWT -> claims -> Principal` — with the role *source*
swapped by `MOIRA_AUTH_MODE`:

- `off`    : enforcement disabled (current behaviour; the API treats every caller
             as a full-access local admin). Default, so nothing breaks until the
             frontend is wired and the operator flips the mode.
- `local`  : the sidecar self-issues + verifies a short-lived HS256 JWT with its
             own secret. Zero IdP/Keycloak needed for desktop/dev. Roles come from
             the token's `roles` claim (default `[admin]` for the local user).
- `oidc`   : a real IdP issues the JWT; verified via JWKS (lazy-imports PyJWT —
             optional dep, like psycopg for Postgres). Roles map from group claims
             via a `group->role` map in Moira settings (NOT per-user in the repo).

HS256 local tokens are implemented with the stdlib (hmac/hashlib/base64) to keep
the zero-dependency core intact.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from .authz import Principal


def auth_mode() -> str:
    return os.environ.get("MOIRA_AUTH_MODE", "off").lower()


_LOCAL_SECRET: str | None = None


def local_secret() -> str:
    """Process-stable HS256 secret: from env if set, else a random per-process key."""
    global _LOCAL_SECRET
    if _LOCAL_SECRET is None:
        _LOCAL_SECRET = os.environ.get("MOIRA_AUTH_SECRET") or secrets.token_urlsafe(32)
    return _LOCAL_SECRET


# ---- HS256 JWT (stdlib) --------------------------------------------------- #
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def mint_local_token(subject: str, roles: list[str], secret: str | None = None,
                     ttl_seconds: int = 3600, now: float | None = None) -> str:
    secret = secret or local_secret()
    iat = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": subject, "roles": list(roles), "iat": iat,
               "exp": iat + ttl_seconds, "src": "local"}
    seg = (_b64u(json.dumps(header, separators=(",", ":")).encode())
           + "." + _b64u(json.dumps(payload, separators=(",", ":")).encode()))
    sig = _b64u(hmac.new(secret.encode(), seg.encode(), hashlib.sha256).digest())
    return f"{seg}.{sig}"


def verify_local(token: str, secret: str | None = None, now: float | None = None) -> dict:
    secret = secret or local_secret()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    h, p, s = parts
    expected = _b64u(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, s):
        raise ValueError("bad signature")
    try:
        payload = json.loads(_b64u_decode(p))
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"bad payload: {e}") from e
    nowi = int(now if now is not None else time.time())
    if int(payload.get("exp", 0)) < nowi:
        raise ValueError("expired")
    return payload


# ---- claims -> principal -------------------------------------------------- #
def map_groups_to_roles(groups: list[str], group_role_map: dict[str, str]) -> list[str]:
    out: set[str] = set()
    for g in groups or []:
        role = group_role_map.get(g)
        if role:
            out.add(role)
    return sorted(out)


def claims_to_principal(claims: dict, auth_source: str = "local",
                        group_role_map: dict[str, str] | None = None) -> Principal:
    roles = claims.get("roles")
    if roles is None and group_role_map is not None:
        roles = map_groups_to_roles(claims.get("groups", []), group_role_map)
    return Principal(
        subject=claims.get("sub", "unknown"),
        display_name=claims.get("name", claims.get("sub", "")),
        roles=list(roles or []),
        auth_source=auth_source,
    )


def _group_role_map() -> dict[str, str]:
    """OIDC group->role map from MOIRA_OIDC_GROUP_ROLES (JSON), Moira settings — not the repo."""
    raw = os.environ.get("MOIRA_OIDC_GROUP_ROLES", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def principal_from_token(token: str | None) -> Principal | None:
    """Resolve a bearer token to a Principal per MOIRA_AUTH_MODE. None if invalid.

    In `off` mode the caller is treated as a full local admin (no enforcement).
    """
    mode = auth_mode()
    if mode == "off":
        return Principal(subject="local", display_name="local", roles=["admin"], auth_source="off")
    if not token:
        return None
    if mode == "local":
        try:
            return claims_to_principal(verify_local(token), auth_source="local")
        except ValueError:
            return None
    if mode == "oidc":
        try:
            import jwt  # lazy — only the OIDC path needs PyJWT[crypto]
            from jwt import PyJWKClient
        except ImportError as e:
            raise RuntimeError("MOIRA_AUTH_MODE=oidc requires PyJWT[crypto]") from e
        jwks_url = os.environ["MOIRA_OIDC_JWKS_URL"]
        audience = os.environ.get("MOIRA_OIDC_AUDIENCE")
        issuer = os.environ.get("MOIRA_OIDC_ISSUER")
        try:
            signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, signing_key, algorithms=["RS256"],
                                audience=audience, issuer=issuer)
        except Exception:  # noqa: BLE001 — any verification failure = unauthenticated
            return None
        return claims_to_principal(claims, auth_source="oidc", group_role_map=_group_role_map())
    return None
