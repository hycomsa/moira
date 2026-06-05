"""Tamper-evidence for the audit trail — a per-run hash chain.

Each audit record is "sealed": it carries `prev_hash` (the previous record's hash
in the same run) and `hash` = sha256 over its content + prev_hash. Altering any
record (or reordering / dropping one) breaks the chain from that point on, which
`verify_chain` detects. The hashes live inside the stored record JSON, so the git
mirror (.moira-runs/<run>/audit/*.json) carries the evidence too.

This is intentionally lightweight (no external deps, no signing) — it proves the
log wasn't *silently* edited, which is the compliance ask in ADR-005.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = ""  # prev_hash of the first record in a run


def seal(body: dict[str, Any], prev: str) -> dict[str, Any]:
    """Return a copy of `body` with `prev_hash` + `hash` set (recomputable)."""
    b = {k: v for k, v in body.items() if k not in ("hash", "prev_hash")}
    b["prev_hash"] = prev
    digest = hashlib.sha256(json.dumps(b, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    b["hash"] = digest
    return b


def verify_chain(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Re-derive every record's hash from its content + the running prev_hash.

    Returns {ok, sealed, length, broken_at, head}. `broken_at` is the index of the
    first record whose stored hash doesn't match (or whose link is wrong); None if
    ok. `sealed` is False for legacy records written before hashing existed — those
    are reported ok-but-unsealed rather than broken.
    """
    if not any(r.get("hash") for r in records):
        return {"ok": True, "sealed": False, "length": len(records), "broken_at": None, "head": ""}
    prev = GENESIS
    for i, r in enumerate(records):
        if seal(r, prev)["hash"] != r.get("hash"):
            return {"ok": False, "sealed": True, "length": len(records), "broken_at": i, "head": prev}
        prev = r["hash"]
    return {"ok": True, "sealed": True, "length": len(records), "broken_at": None, "head": prev}
