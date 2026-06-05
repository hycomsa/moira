"""Minimal, dependency-free YAML I/O for Moira's git-native definitions.

Moira stores agents (.ai/agents/*.yml) and pipelines (.ai/pipelines/*.yml) as
YAML in the AI SDLC repo. We control the file format (Moira writes them), so we
emit a deliberately restricted subset and parse exactly that subset — keeping the
orchestrator zero-dependency (no PyYAML), which matters for distribution.

Supported subset (sufficient for agent/pipeline schemas):
- top-level block mapping: `key: <scalar>`
- scalars: str, int, float, bool, null (and double-quoted strings with \\n)
- inline flow lists:  `key: [a, b, c]`
- inline flow maps:   `key: {a: 1, b: [x, y]}`   (used for gate config)
- block sequences of mappings:
    nodes:
      - id: analyze
        agent: requirements-analyst
      - id: gate-x
        gate: {mode: human, persona: client}

NOT supported (and never emitted): block scalars (|, >), anchors, multi-doc,
deeply nested block mappings. Nested structure is emitted as inline flow.
"""
from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- #
# Dump
# --------------------------------------------------------------------------- #
def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # quote if it could be misread or contains special chars
    needs_quote = (
        s == "" or s in ("null", "true", "false")
        or any(c in s for c in [":", "#", "{", "}", "[", "]", ",", "\n", '"', "'", "&", "*"])
        or s.strip() != s
    )
    if needs_quote:
        esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    return s


def _flow(v: Any) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_flow(val)}" for k, val in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    return _scalar(v)


def dump(data: dict[str, Any]) -> str:
    """Emit a top-level mapping. Lists-of-mappings become block sequences;
    everything else (scalars, scalar lists, nested maps) becomes inline flow."""
    lines: list[str] = []
    for key, val in data.items():
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            lines.append(f"{key}:")
            for item in val:
                first = True
                for k, v in item.items():
                    prefix = "  - " if first else "    "
                    lines.append(f"{prefix}{k}: {_flow(v)}")
                    first = False
                if first:  # empty mapping item
                    lines.append("  - {}")
        elif isinstance(val, (list, dict)):
            lines.append(f"{key}: {_flow(val)}")
        else:
            lines.append(f"{key}: {_scalar(val)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def _parse_scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "" or tok == "null" or tok == "~":
        return None if tok != "" else ""
    if tok == "true":
        return True
    if tok == "false":
        return False
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        return tok[1:-1].replace('\\n', "\n").replace('\\"', '"').replace("\\\\", "\\")
    if len(tok) >= 2 and tok[0] == "'" and tok[-1] == "'":
        return tok[1:-1]
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _parse_flow(s: str) -> Any:
    """Parse an inline flow value: {..}, [..], or a scalar."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        return _parse_flow_map(s[1:-1])
    if s.startswith("[") and s.endswith("]"):
        return _parse_flow_list(s[1:-1])
    return _parse_scalar(s)


def _split_top(s: str) -> list[str]:
    """Split on commas not nested inside {}, [] or quotes."""
    out, depth, buf, q = [], 0, [], None
    for c in s:
        if q:
            buf.append(c)
            if c == q:
                q = None
            continue
        if c in ('"', "'"):
            q = c; buf.append(c); continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        if c == "," and depth == 0:
            out.append("".join(buf)); buf = []
        else:
            buf.append(c)
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def _parse_flow_list(s: str) -> list[Any]:
    if not s.strip():
        return []
    return [_parse_flow(x) for x in _split_top(s)]


def _parse_flow_map(s: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in _split_top(s):
        if ":" not in pair:
            continue
        k, v = pair.split(":", 1)
        out[k.strip()] = _parse_flow(v)
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def load(text: str) -> dict[str, Any]:
    """Parse the restricted subset back into a dict."""
    # drop comments / blank lines
    raw = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in raw if ln.strip() and not ln.lstrip().startswith("#")]
    result: dict[str, Any] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        key, _, rest = line.lstrip().partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            # could be a block sequence (next lines start with '- ')
            if i + 1 < n and lines[i + 1].lstrip().startswith("- "):
                seq, i = _parse_block_seq(lines, i + 1, _indent(lines[i + 1]))
                result[key] = seq
                continue
            result[key] = None
            i += 1
        else:
            result[key] = _parse_flow(rest)
            i += 1
    return result


def _parse_block_seq(lines: list[str], start: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    i = start
    n = len(lines)
    while i < n and _indent(lines[i]) == indent and lines[i].lstrip().startswith("- "):
        # start of an item: "- key: val"
        item: dict[str, Any] = {}
        first = lines[i].lstrip()[2:]  # after "- "
        k, _, v = first.partition(":")
        item[k.strip()] = _parse_flow(v.strip())
        i += 1
        # continuation lines: deeper indent, not a new "- "
        cont_indent = indent + 2
        while i < n and _indent(lines[i]) >= cont_indent and not lines[i].lstrip().startswith("- "):
            ck, _, cv = lines[i].lstrip().partition(":")
            item[ck.strip()] = _parse_flow(cv.strip())
            i += 1
        items.append(item)
    return items, i
