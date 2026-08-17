"""RBAC authorization matrix (must-fix #2).

Reuses the five roles designed in operating-model.md (Admin/Developer/Compliance/
Client/Viewer). A role grants a set of ACTIONS and a set of approvable gate
PERSONAS — separation of duties: the people who build (Developer) cannot approve
compliance/legal gates, the Client sees docs without touching config or code.

The matrix is a fixed product contract (code constant), independent of WHERE a
principal's roles come from (see authn.py: local self-issued JWT or OIDC claims).
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLES = ("admin", "developer", "compliance", "client", "viewer")

# action buckets map to route groups (see the enforcement table in moira_api)
ROLE_ACTIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "developer": {"configure", "launch", "approve_gate", "run_eval", "read_sensitive", "read"},
    "compliance": {"approve_gate", "governance_override", "run_eval", "read_sensitive", "read"},
    "client": {"approve_gate", "read"},
    "viewer": {"read"},
}

# which gate personas each role may approve / override
ROLE_PERSONAS: dict[str, set[str]] = {
    "admin": {"*"},
    "developer": {"ba", "lead-dev", "architect", "qa", "accessibility-lead"},
    "compliance": {"compliance", "ciso", "compliance-lead"},
    "client": {"client"},
    "viewer": set(),
}


@dataclass
class Principal:
    """The authenticated subject the API acts on behalf of."""
    subject: str
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    auth_source: str = "unknown"


def can(principal: Principal, action: str) -> bool:
    """True if any of the principal's roles grants `action` (admin holds `*`)."""
    for r in principal.roles:
        acts = ROLE_ACTIONS.get(r, set())
        if "*" in acts or action in acts:
            return True
    return False


def can_approve(principal: Principal, persona: str) -> bool:
    """True if any role lets the principal approve/override the given gate persona."""
    for r in principal.roles:
        personas = ROLE_PERSONAS.get(r, set())
        if "*" in personas or persona in personas:
            return True
    return False


# ---- request authorization (default-deny) --------------------------------- #
# Sensitive reads expose filesystem/logs/config; everything else under /api is a
# plain read (Viewer+). Mutations need launch/configure. Anything not matched
# still requires a permission (no anonymous access) — never silently public.
_SENSITIVE_GET = {"/api/file", "/api/files", "/api/browse", "/api/logs",
                  "/api/workspaces", "/api/runner", "/api/health"}
_PUBLIC = {"/api/ready"}  # readiness only — no paths/secrets


def required_action(method: str, path: str) -> str | None:
    """Permission an HTTP request needs, or None if the route is PUBLIC.

    Public: non-`/api/*` (static frontend), `/api/ready`, and any OPTIONS preflight.
    """
    if method == "OPTIONS" or not path.startswith("/api/") or path in _PUBLIC:
        return None
    if method == "GET":
        if path in _SENSITIVE_GET or path.endswith("/debug"):
            return "read_sensitive"
        return "read"
    # POST / DELETE — mutations
    if path == "/api/runs" or path == "/api/discovery":
        return "launch"
    if path.endswith("/approve") or path.endswith("/reject") or path.endswith("/retry"):
        return "approve_gate"  # retry is the third decision at the same checkpoint (ADR-013)
    if path.endswith("/rerun") or path.endswith("/cancel"):
        return "launch"
    if path.endswith("/report"):
        return "read_sensitive"
    if path == "/api/eval":
        return "run_eval"
    if path.startswith("/api/gate/simulate"):
        return "read"
    # pipelines / agents / workspaces writes + any other mutation -> configure
    return "configure"


def authorize_request(method: str, path: str, principal: Principal | None):
    """Return None if allowed, else (status, body) — 401 unauthenticated / 403 forbidden."""
    action = required_action(method, path)
    if action is None:
        return None  # public
    if principal is None:
        return (401, {"error": "authentication required"})
    if not can(principal, action):
        return (403, {"error": "forbidden", "action": action})
    return None
