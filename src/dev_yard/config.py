from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dev_yard import paths

PI_STAGES = ("open", "grill", "spec", "tickets", "implement", "review")


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


def load_workspace(root: Path) -> dict[str, Any]:
    path = paths.repos_yaml(root)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("repos.yaml must be a mapping")
    return data


def dump_workspace(root: Path, data: dict[str, Any]) -> None:
    paths.repos_yaml(root).write_text(yaml.safe_dump(data, sort_keys=False))


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class StageModel:
    provider: str | None = None
    model: str | None = None


@dataclass
class PiSettings:
    provider: str | None = None
    model: str | None = None
    stages: dict[str, StageModel] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        stages: dict[str, dict[str, str]] = {}
        for name, stage in self.stages.items():
            entry: dict[str, str] = {}
            if stage.provider:
                entry["provider"] = stage.provider
            if stage.model:
                entry["model"] = stage.model
            if entry:
                stages[name] = entry
        if stages:
            payload["stages"] = stages
        return payload


def load_pi_settings(root: Path) -> PiSettings:
    raw = load_workspace(root).get("pi") or {}
    if not isinstance(raw, dict):
        raise ValueError("repos.yaml pi must be a mapping")
    stages: dict[str, StageModel] = {}
    for name, entry in (raw.get("stages") or {}).items():
        if name not in PI_STAGES:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"repos.yaml pi.stages.{name} must be a mapping")
        stages[name] = StageModel(
            provider=_blank(entry.get("provider")),
            model=_blank(entry.get("model")),
        )
    return PiSettings(
        provider=_blank(raw.get("provider")),
        model=_blank(raw.get("model")),
        stages=stages,
    )


def save_pi_settings(root: Path, settings: PiSettings) -> None:
    unknown = [n for n in settings.stages if n not in PI_STAGES]
    if unknown:
        raise ValueError(f"unknown pi stages: {', '.join(unknown)}")
    data = load_workspace(root)
    data.setdefault("repos", data.get("repos") or {})
    payload = settings.to_payload()
    if payload:
        data["pi"] = payload
    else:
        data.pop("pi", None)
    dump_workspace(root, data)


def resolve_pi_choice(root: Path, bundle: str) -> tuple[str | None, str | None]:
    """Stage yaml → workspace default → env. Empty means omit --provider/--model."""
    import os

    settings = load_pi_settings(root)
    stage = settings.stages.get(bundle) or StageModel()
    provider = (
        stage.provider
        or settings.provider
        or _blank(os.environ.get("YARD_PI_PROVIDER"))
    )
    model = stage.model or settings.model or _blank(os.environ.get("YARD_PI_MODEL"))
    return provider, model


def load_repos(root: Path) -> dict[str, Repo]:
    data = load_workspace(root)
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
    data = load_workspace(root)
    data["repos"] = {
        a: {
            "url": r.url,
            "default_base": r.default_base,
            "role": r.role,
            **({"path": str(r.path)} if r.path else {}),
        }
        for a, r in repos.items()
    }
    dump_workspace(root, data)
