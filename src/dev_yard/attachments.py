"""Human-added requirement attachments.

Files land in `reqs/<JIRA>/uploads/`, a directory `req open` never touches, so
they survive re-extraction. Machine-fetched screenshots stay in `assets/`.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from dev_yard import paths
from dev_yard.fsutil import atomic_write_bytes, atomic_write_text

UPLOADS_DIRNAME = "uploads"
MAX_BYTES = 50 * 1024 * 1024
MAX_NAME = 180

_CTRL = re.compile(r"[\x00-\x1f\x7f]")

_START = "<!-- yard:uploads:start -->"
_END = "<!-- yard:uploads:end -->"
_BLOCK_RE = re.compile(re.escape(_START) + r".*?" + re.escape(_END), re.S)


def safe_name(name: str) -> str:
    """Reduce an uploaded filename to a single safe path segment.

    Unicode (e.g. Chinese) is kept; separators, control chars and leading dots
    are stripped so the result cannot escape the uploads directory.
    """
    raw = (name or "").replace("\\", "/")
    base = _CTRL.sub("", raw.rsplit("/", 1)[-1]).strip().strip(".")
    if base in {"", ".", ".."}:
        raise ValueError(f"invalid attachment name {name!r}")
    if len(base) > MAX_NAME:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 16:
            base = stem[: MAX_NAME - len(ext) - 1] + "." + ext
        else:
            base = base[:MAX_NAME]
    return base


def uploads_dir(root: Path, jira: str) -> Path:
    return paths.req_dir(root, jira) / UPLOADS_DIRNAME


def _free_name(dest_dir: Path, name: str) -> str:
    if not (dest_dir / name).exists():
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(1, 1000):
        cand = f"{stem}-{i}{'.' + ext if ext else ''}"
        if not (dest_dir / cand).exists():
            return cand
    raise ValueError(f"too many attachments named like {name!r}")


def add_bytes(root: Path, jira: str, name: str, data: bytes) -> str:
    """Store bytes under uploads/; returns the (deduped) stored filename.

    Re-adding byte-identical content under the same name is a no-op, so
    re-running `req attach` stays idempotent.
    """
    if len(data) > MAX_BYTES:
        raise ValueError(f"attachment too large (>{MAX_BYTES // (1024 * 1024)}MB)")
    dest_dir = uploads_dir(root, jira)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_name(name)
    target = dest_dir / safe
    if target.is_file() and target.read_bytes() == data:
        return safe
    stored = _free_name(dest_dir, safe)
    atomic_write_bytes(dest_dir / stored, data)
    return stored


def add_file(root: Path, jira: str, src: Path, name: str | None = None) -> str:
    src = Path(src).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"attachment source not found: {src}")
    return add_bytes(root, jira, name or src.name, src.read_bytes())


def list_names(root: Path, jira: str) -> list[str]:
    d = uploads_dir(root, jira)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def resolve(root: Path, jira: str, name: str) -> Path:
    d = uploads_dir(root, jira).resolve()
    target = (d / name).resolve()
    try:
        target.relative_to(d)
    except ValueError as e:
        raise ValueError(f"invalid attachment {name!r}") from e
    if not target.is_file():
        raise FileNotFoundError(name)
    return target


def remove(root: Path, jira: str, name: str) -> None:
    resolve(root, jira, name).unlink()


def _doc_block(names: list[str]) -> str:
    lines = [_START, "", "## 补充附件", "", "人工上传；重抽需求不会清空。", ""]
    lines.extend(f"- [{n}](uploads/{quote(n)})" for n in names)
    lines.extend(["", _END])
    return "\n".join(lines)


def sync_uploads_section(req: Path, names: list[str]) -> bool:
    """Rewrite the managed uploads list in REQUIREMENT.md; True if it changed."""
    path = req / "REQUIREMENT.md"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    stripped = _BLOCK_RE.sub("", text).rstrip("\n")
    new = (stripped + "\n\n" + _doc_block(names) + "\n") if names else stripped + "\n"
    if new == text:
        return False
    atomic_write_text(path, new)
    return True


def sync_doc(root: Path, jira: str) -> bool:
    """Point REQUIREMENT.md at whatever is currently in uploads/."""
    return sync_uploads_section(paths.req_dir(root, jira), list_names(root, jira))

