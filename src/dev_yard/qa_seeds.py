"""Host-side seed registry: setup stdout → qa/design-verify/seeds.yaml.

Setup runs remotely and only stdout comes back. Generated primary keys are
recorded here (never as a table in the business catalog) so verify.sql can
use `:seed.<key>` placeholders the host substitutes before usql.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_SEED_LINE = re.compile(r"^QA_SEED\s+(.+)$", re.M | re.I)
_PAIR = re.compile(r"([A-Za-z_][\w]*)=(\S+)")
# SQL uses `:seed.key`. Case prose and page paths use `<seed.key>`.
_PLACEHOLDER = re.compile(
    r":seed\.([A-Za-z_][\w]*)|<seed\.([A-Za-z_][\w]*)>"
)


_SEED_KEY = re.compile(r"^[A-Za-z_][\w]*$")


def seeds_from_columns(row: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Map a verify.sql result row onto the seed registry.

    Returns `(seeds, blank_aliases)`. Column aliases are the `<seed.alias>`
    keys. A null or blank cell is listed in `blank_aliases` and is not a seed,
    so the caller can tell an empty value from a missing column.
    """
    out: dict[str, dict[str, str]] = {}
    blank: list[str] = []
    for key, val in row.items():
        name = str(key).strip().lower()
        if not _SEED_KEY.match(name):
            continue
        if val is None:
            blank.append(name)
            continue
        text = str(val).strip()
        if not text:
            blank.append(name)
            continue
        out[name] = {"id": text}
    return out, blank


def sql_literal(value: str) -> str:
    """Quote a seed cell for splicing into SQL text. Standard '' escaping."""
    return "'" + str(value).replace("'", "''") + "'"


def explain_missing_seeds(
    missing: list[str], blank: list[str], *, qa_seed: bool = False
) -> str:
    """Why a placeholder did not resolve. Blank cells are not missing columns."""
    blank_l = {name.lower() for name in blank}
    empty = [key for key in missing if key.lower() in blank_l]
    absent = [key for key in missing if key.lower() not in blank_l]
    parts: list[str] = []
    if absent:
        label = "缺 QA_SEED " if qa_seed else "查询没有列 "
        parts.append(label + ", ".join(absent))
    if empty:
        parts.append(
            "列 " + ", ".join(empty) + " 查到了但是空值，换一行或换条件，不要改别名"
        )
    return "；".join(parts)


def parse_qa_seeds(stdout: str) -> dict[str, dict[str, str]]:
    """`{key: {id, table?}}` from `QA_SEED key=task id=123 table=firm_tasks` lines."""
    out: dict[str, dict[str, str]] = {}
    for m in _SEED_LINE.finditer(stdout or ""):
        pairs = {k.lower(): v for k, v in _PAIR.findall(m.group(1))}
        key = pairs.get("key") or pairs.get("name")
        entity = pairs.get("id") or pairs.get("entity_id")
        if not key or not entity:
            continue
        rec: dict[str, str] = {"id": entity}
        if pairs.get("table"):
            rec["table"] = pairs["table"]
        out[key.lower()] = rec
    return out


def seeds_path(qa: Path) -> Path:
    return qa / "design-verify" / "seeds.yaml"


def write_case_seeds(qa: Path, case_id: str, seeds: dict[str, dict[str, str]]) -> Path:
    path = seeds_path(qa)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, yaml.YAMLError):
            data = {}
    if seeds:
        data[case_id] = seeds
    elif case_id in data:
        del data[case_id]
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def apply_seed_placeholders(
    sql: str,
    seeds: dict[str, dict[str, str]],
    *,
    sql_literals: bool = False,
) -> tuple[str, list[str]]:
    """Replace `:seed.key` and `<seed.key>` with the recorded id.

    Returns `(text, missing keys)`. `sql_literals=False` is the case markdown
    the browser reads: the cell is inserted raw. `sql_literals=True` is
    verify.sql, identity SQL, and db-assertion SQL: the cell is a quoted literal.
    """
    missing: list[str] = []
    lower_seeds = {k.lower(): v for k, v in seeds.items()}

    def repl(m: re.Match[str]) -> str:
        key = m.group(1) or m.group(2)
        rec = lower_seeds.get(key.lower())
        if not rec or not rec.get("id"):
            missing.append(key)
            return m.group(0)
        if sql_literals:
            return sql_literal(rec["id"])
        return rec["id"]

    replaced = _PLACEHOLDER.sub(repl, sql)
    # Deduplicate missing while preserving order of first appearance
    seen: set[str] = set()
    deduped_missing: list[str] = []
    for k in missing:
        if k not in seen:
            seen.add(k)
            deduped_missing.append(k)
    return replaced, deduped_missing


def has_seed_placeholders(sql: str) -> bool:
    return bool(_PLACEHOLDER.search(sql or ""))


def load_case_seeds(qa: Path, case_id: str) -> dict[str, dict[str, str]]:
    """Seeds this case's own setup wrote. Missing file or id yields `{}`."""
    path = seeds_path(qa)
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    rec = loaded.get(case_id) if isinstance(loaded, dict) else None
    if not isinstance(rec, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, val in rec.items():
        if isinstance(val, dict) and val.get("id"):
            out[str(key).lower()] = {str(k): str(v) for k, v in val.items()}
    return out


def seeds_from_deps(qa: Path, job: Any, cases: list[Any]) -> dict[str, dict[str, str]]:
    """Union of QA_SEED rows written by `depends_on` ancestors. Own keys are not included."""
    by_id = {c.id: c for c in cases}
    merged: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    def walk(cid: str) -> None:
        if cid in seen or cid == job.id:
            return
        seen.add(cid)
        parent = by_id.get(cid)
        if parent is None:
            return
        for dep in parent.depends_on:
            walk(dep)
        merged.update(load_case_seeds(qa, cid))

    for dep in job.depends_on:
        walk(dep)
    return merged


_SECTION = re.compile(r"(?m)^(?=##\s+)")
_ACTION_SECTION = re.compile(r"^##\s+(步骤|预期)\b")


def seed_action_text(text: str) -> str:
    """Steps and expected results, where a placeholder must resolve.

    Frontmatter and 前置/备注 may quote the `<seed.name>` syntax. Those
    occurrences are substituted when the key exists, but a missing key there
    is not a case defect.
    """
    parts = _SECTION.split(text or "")
    kept = [part for part in parts if part and _ACTION_SECTION.match(part)]
    if kept:
        return "\n".join(kept)
    body = text or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            return body[end + 4 :]
    return body
