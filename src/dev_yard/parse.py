"""Coercion helpers shared by the ticket / finding parsers.

Ticket bullets, contract findings, test findings and case frontmatter all
accept the same loose shapes (comma/space string, list, nested list). Keeping
one implementation here stops the three copies from drifting.
"""

from __future__ import annotations

from typing import Any

_TRUE = frozenset({"true", "yes", "1", "on"})
_FALSE = frozenset({"false", "no", "0", "off"})


def as_list(val: Any) -> list[str]:
    """Flatten a scalar/list/nested-list into a list of strings."""
    if val is None or val is False:
        return []
    if isinstance(val, str):
        return [x.strip() for x in val.replace(",", " ").split() if x.strip()]
    if isinstance(val, (list, tuple)):
        out: list[str] = []
        for x in val:
            out.extend(as_list(x))
        return out
    return [str(val)]


def as_name_list(val: Any) -> list[str]:
    """`depends_on` / `covers`: never raises, drops empties."""
    return [s for s in (str(x).strip() for x in as_list(val)) if s]


def as_bool(val: Any, default: bool | None = None) -> bool | None:
    """Tri-state boolean coercion; `default` for anything unrecognised."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    text = str(val).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default
