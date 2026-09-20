from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dev_yard import gitops, paths

# Stages whose model pair may be overridden under repos.yaml `pi.stages`.
# Test stages (qa-design, qa-run) are deliberately absent: their models live in
# qa.yaml (`design`, `workers`) so all test model config sits in one file.
PI_STAGES = (
    "open",
    "grill",
    "spec",
    "tickets",
    "implement",
    "review",
    "contract",
    "assistant",
)


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
    provider: str | None = None
    model: str | None = None
    test_branch: str | None = None

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
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("repos.yaml must be a mapping")
    return data


def dump_workspace(root: Path, data: dict[str, Any]) -> None:
    paths.repos_yaml(root).write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


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


def _pair(provider: Any, model: Any) -> tuple[str, str] | None:
    p, m = _blank(provider), _blank(model)
    if p and m:
        return p, m
    return None


def require_pair(provider: Any, model: Any) -> tuple[str, str] | None:
    pair = _pair(provider, model)
    if pair:
        return pair
    if _blank(provider) or _blank(model):
        raise ValueError("provider and model must be set together")
    return None


def resolve_pi_choice(
    root: Path,
    bundle: str,
    repo: str | None = None,
) -> tuple[str | None, str | None]:
    """Whole-pair fallback. Empty means omit --provider/--model.

    implement: repo pair → stage implement pair → workspace pair → env pair.
    Other stages (including review, contract): stage pair → workspace pair → env pair.
    Incomplete pairs (only provider or only model) are skipped.
    """
    import os

    settings = load_pi_settings(root)
    layers: list[tuple[str, str] | None] = []
    if bundle == "implement" and repo:
        found = load_repos(root).get(repo)
        if found:
            layers.append(_pair(found.provider, found.model))
    stage = settings.stages.get(bundle) or StageModel()
    layers.append(_pair(stage.provider, stage.model))
    layers.append(_pair(settings.provider, settings.model))
    layers.append(
        _pair(os.environ.get("YARD_PI_PROVIDER"), os.environ.get("YARD_PI_MODEL"))
    )
    for item in layers:
        if item:
            return item
    return None, None


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
            provider=_blank(raw.get("provider")),
            model=_blank(raw.get("model")),
            test_branch=_blank(raw.get("test_branch")),
        )
    return out


DEFAULT_FREEZE_BRANCH = "req/{jira}"
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
_SAMPLE_JIRA = "PROJ-101"
_SAMPLE_TICKET = "T1"


@dataclass
class GitSettings:
    freeze_branch: str = DEFAULT_FREEZE_BRANCH
    ai_resolve_conflicts: bool = True


def normalize_freeze_template(template: Any) -> str:
    text = str(template or "").strip() or DEFAULT_FREEZE_BRANCH
    names = _PLACEHOLDER_RE.findall(text)
    if "jira" not in names:
        raise ValueError("git.freeze_branch must contain {jira}")
    extra = sorted({n for n in names if n != "jira"})
    if extra:
        raise ValueError(
            "git.freeze_branch unknown placeholders: "
            + ", ".join("{" + n + "}" for n in extra)
        )
    sample = text.replace("{jira}", _SAMPLE_JIRA)
    gitops.assert_branch_name(sample)
    gitops.assert_branch_name(ticket_branch_name(sample, _SAMPLE_TICKET))
    return text


def render_freeze_branch(template: str, jira: str) -> str:
    key = (jira or "").strip()
    if not key:
        raise ValueError("jira key is required")
    branch = normalize_freeze_template(template).replace("{jira}", key)
    gitops.assert_branch_name(branch)
    return branch


def ticket_branch_name(freeze: str, ticket_id: str) -> str:
    """Child branch of a freeze ref. Hyphen, not slash: git forbids nested refs."""
    tid = (ticket_id or "").strip()
    if not tid:
        raise ValueError("ticket id is required")
    name = f"{freeze}-{tid}"
    gitops.assert_branch_name(name)
    return name


def load_git_settings(root: Path) -> GitSettings:
    raw = load_workspace(root).get("git")
    if not raw:
        return GitSettings()
    if not isinstance(raw, dict):
        raise ValueError("repos.yaml git must be a mapping")
    template = raw.get("freeze_branch", DEFAULT_FREEZE_BRANCH)
    resolve = raw.get("ai_resolve_conflicts", True)
    if not isinstance(resolve, bool):
        raise ValueError("repos.yaml git.ai_resolve_conflicts must be a boolean")
    return GitSettings(
        freeze_branch=normalize_freeze_template(template),
        ai_resolve_conflicts=resolve,
    )


def _load_mutable_section(root: Path, section: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load repos.yaml with a copy of `section` ready to mutate."""
    data = load_workspace(root)
    data.setdefault("repos", data.get("repos") or {})
    current = data.get(section)
    return data, dict(current) if isinstance(current, dict) else {}


def _store_section(root: Path, data: dict[str, Any], section: str, values: dict[str, Any]) -> None:
    if values:
        data[section] = values
    else:
        data.pop(section, None)
    dump_workspace(root, data)


def save_git_settings(root: Path, settings: GitSettings) -> None:
    template = normalize_freeze_template(settings.freeze_branch)
    data, git = _load_mutable_section(root, "git")
    if template == DEFAULT_FREEZE_BRANCH:
        git.pop("freeze_branch", None)
    else:
        git["freeze_branch"] = template
    if settings.ai_resolve_conflicts:
        git.pop("ai_resolve_conflicts", None)
    else:
        git["ai_resolve_conflicts"] = False
    _store_section(root, data, "git", git)


def resolve_freeze_branch(
    root: Path,
    jira: str,
    data: dict[str, Any] | None = None,
    worktree: Path | None = None,
) -> str:
    """Prefer the name recorded at freeze, then the worktree checkout, then the template."""
    if data:
        stored = data.get("branch")
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
    if worktree is not None and (worktree / ".git").exists():
        try:
            name = gitops.current_branch(worktree)
        except gitops.GitError:
            name = ""
        if name and name != "HEAD":
            return name
    return render_freeze_branch(load_git_settings(root).freeze_branch, jira)


def git_settings_out(settings: GitSettings) -> dict[str, Any]:
    freeze = render_freeze_branch(settings.freeze_branch, _SAMPLE_JIRA)
    return {
        "freeze_branch": settings.freeze_branch,
        "default_freeze_branch": DEFAULT_FREEZE_BRANCH,
        "ai_resolve_conflicts": settings.ai_resolve_conflicts,
        "preview": freeze,
        "ticket_preview": ticket_branch_name(freeze, _SAMPLE_TICKET),
        "placeholders": ["{jira}"],
        "examples": [DEFAULT_FREEZE_BRANCH, "feature/{jira}", "feat/{jira}", "{jira}"],
    }


@dataclass
class DevSettings:
    tdd: bool = True


def load_dev_settings(root: Path) -> DevSettings:
    raw = load_workspace(root).get("dev")
    if not raw:
        return DevSettings()
    if not isinstance(raw, dict):
        raise ValueError("repos.yaml dev must be a mapping")
    if "tdd" not in raw:
        return DevSettings()
    value = raw["tdd"]
    if not isinstance(value, bool):
        raise ValueError("repos.yaml dev.tdd must be a boolean")
    return DevSettings(tdd=value)


def save_dev_settings(root: Path, settings: DevSettings) -> None:
    data, dev = _load_mutable_section(root, "dev")
    if settings.tdd:
        dev.pop("tdd", None)
    else:
        dev["tdd"] = False
    _store_section(root, data, "dev", dev)


def save_repos(root: Path, repos: dict[str, Repo]) -> None:
    data = load_workspace(root)
    data["repos"] = {}
    for a, r in repos.items():
        entry: dict[str, Any] = {
            "url": r.url,
            "default_base": r.default_base,
            "role": r.role,
        }
        if r.path:
            entry["path"] = str(r.path)
        test_branch = _blank(r.test_branch)
        if test_branch:
            gitops.assert_branch_name(test_branch)
            entry["test_branch"] = test_branch
        pair = _pair(r.provider, r.model)
        if pair:
            entry["provider"], entry["model"] = pair
        elif _blank(r.provider) or _blank(r.model):
            raise ValueError(f"{a}: provider and model must be set together")
        data["repos"][a] = entry
    dump_workspace(root, data)
