from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dev_yard import paths


def git_project_name(url: str) -> str:
    """Last path segment of a git URL or local path, without `.git`."""
    raw = (url or "").strip()
    if "://" not in raw and ":" in raw:
        path = raw.split(":", 1)[1]
    else:
        path = urlparse(raw).path or raw
    name = Path(path.rstrip("/")).name
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not name or name in {".", ".."}:
        raise ValueError(f"cannot derive alias from url {url!r}")
    return name


@dataclass
class Repo:
    alias: str
    url: str
    default_base: str = "main"
    role: str = "svc"
    path: Path | None = None

    def source_path(self, root: Path) -> Path:
        if self.path:
            p = self.path.expanduser()
            if not p.is_absolute():
                p = root / p
            return p.resolve()
        return paths.repos_dir(root) / self.alias


def load_repos(root: Path) -> dict[str, Repo]:
    data = yaml.safe_load(paths.repos_yaml(root).read_text()) or {}
    out: dict[str, Repo] = {}
    for alias, raw in (data.get("repos") or {}).items():
        if not isinstance(raw, dict) or "url" not in raw:
            raise ValueError(f"repos.yaml {alias} missing url")
        out[alias] = Repo(
            alias=alias,
            url=raw["url"],
            default_base=raw.get("default_base", "main"),
            role=raw.get("role", "svc"),
            path=Path(raw["path"]) if raw.get("path") else None,
        )
    return out


def save_repos(root: Path, repos: dict[str, Repo]) -> None:
    payload: dict[str, Any] = {
        "repos": {
            a: {
                "url": r.url,
                "default_base": r.default_base,
                "role": r.role,
                **({"path": str(r.path)} if r.path else {}),
            }
            for a, r in repos.items()
        }
    }
    paths.repos_yaml(root).write_text(yaml.safe_dump(payload, sort_keys=False))
