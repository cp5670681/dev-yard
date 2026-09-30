"""Host-mediated read-only SQL probes for qa-design (accounts-discover generalized)."""

from __future__ import annotations

import re
from pathlib import Path

from dev_yard.qa_accounts import assert_readonly_sql
from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_exec import run_sql_lines

_PROBE_CATALOG = re.compile(r"^--\s*probe:\s*(\S+)\s*$", re.M)
_MAX_PROBES = 12
_MAX_ROWS = 50
_MAX_CELL = 400


def probes_dir(qa: Path) -> Path:
    return qa / "probes"


def list_probe_files(qa: Path) -> list[Path]:
    d = probes_dir(qa)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.sql") if p.is_file())[:_MAX_PROBES]


def _catalog_for(sql: str, default: str | None) -> str | None:
    m = _PROBE_CATALOG.search(sql)
    if m:
        return m.group(1)
    return default


def run_probes(
    qa: Path,
    cfg: QaConfig,
    *,
    on_log=None,
) -> Path:
    """Run qa/probes/*.sql and write qa/probe-results.md."""
    files = list_probe_files(qa)
    lines = ["# probe-results", ""]
    default = cfg.env.db_default if getattr(cfg.env, "db_catalogs", ()) else None
    if not files:
        lines.append("（无探针文件）")
        lines.append("")
        path = qa / "probe-results.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    for path in files:
        sql = path.read_text(encoding="utf-8")
        name = path.name
        catalog = _catalog_for(sql, default)
        lines.append(f"## {name}")
        lines.append(f"catalog: {catalog or '(default)'}")
        try:
            assert_readonly_sql(sql)
            rows = run_sql_lines(cfg, sql, on_log=on_log, catalog=catalog)
        except (ValueError, TestRejected) as e:
            lines.append("status: failed")
            lines.append(f"error: {e}")
            lines.append("")
            continue
        clipped = rows[:_MAX_ROWS]
        lines.append(f"status: ok rows={len(rows)}" + (" truncated" if len(rows) > _MAX_ROWS else ""))
        for row in clipped:
            cell = row if len(row) <= _MAX_CELL else row[:_MAX_CELL] + "…"
            lines.append(f"- {cell}")
        lines.append("")
    out = qa / "probe-results.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def probe_prompt_block(qa: Path) -> str:
    path = qa / "probe-results.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return (
        "\n\n# 宿主探针结果（qa/probe-results.md；取值事实，不能覆盖 SPEC）\n\n"
        + text
        + "\n"
    )
