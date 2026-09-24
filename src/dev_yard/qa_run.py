"""Per-run lifecycle: one authoritative status field per evidence run.

``progress.yaml`` is the high-frequency live view and ``result.yaml`` the
detailed evidence; neither answers "is this run still resumable?". This module
owns that answer so pause / resume / rerun all read one field instead of
inferring it from case states or from whether ``result.yaml`` exists.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

RUN_FILE = "run.yaml"

RUNNING = "running"
PAUSED = "paused"
CONCLUDED = "concluded"

# A resume continues these; anything else (concluded, and a future `abandoned`)
# is terminal.
RESUMABLE = frozenset({RUNNING, PAUSED})


def run_path(run_dir: Path) -> Path:
    return run_dir / RUN_FILE


def load(run_dir: Path) -> dict[str, Any]:
    """The recorded run document, or {} when absent/unreadable."""
    path = run_path(run_dir)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def status(run_dir: Path) -> str:
    """The run's lifecycle status.

    Backward compatible with runs that predate ``run.yaml``: a written
    ``result.yaml`` means the run concluded; otherwise it is still running.
    """
    recorded = str(load(run_dir).get("status") or "").strip()
    if recorded:
        return recorded
    if (run_dir / "result.yaml").is_file():
        return CONCLUDED
    return RUNNING


def save(run_dir: Path, *, _clear: tuple[str, ...] = (), **fields: Any) -> Path:
    """Merge fields into the run document and write it atomically.

    Keys in `_clear` are removed first (so a re-opened run drops a stale
    `outcome`), then non-None fields are merged in.
    """
    path = run_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load(run_dir)
    for key in _clear:
        data.pop(key, None)
    for key, value in fields.items():
        if value is None:
            continue
        data[key] = value
    data.setdefault("run_id", run_dir.name)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def set_status(run_dir: Path, value: str, *, outcome: str | None = None) -> None:
    """Record the lifecycle status (never raises on I/O).

    `outcome` is meaningful only for `concluded`; any other status clears it.
    """
    fields: dict[str, Any] = {"status": value}
    clear: tuple[str, ...] = ()
    if outcome is not None:
        fields["outcome"] = outcome
    else:
        clear = ("outcome",)
    try:
        save(run_dir, _clear=clear, **fields)
    except OSError:
        pass
