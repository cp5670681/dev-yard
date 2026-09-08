from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_env(root: Path | None = None) -> Path | None:
    """Load `.env` without overriding already-set process env."""
    candidates: list[Path] = []
    if root:
        candidates.append(root / ".env")
    candidates.append(Path.cwd() / ".env")
    for p in candidates:
        if p.is_file():
            load_dotenv(p, override=False)
            return p
    load_dotenv(override=False)
    return None
