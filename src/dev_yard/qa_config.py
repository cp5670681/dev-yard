from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.config import resolve_pi_choice


class TestRejected(ValueError):
    """req test refused (gate, config, mutation, DAG)."""

    __test__ = False


@dataclass(frozen=True)
class QaAccount:
    name: str
    username_env: str = ""
    password_env: str = ""
    state_file: str = ""


@dataclass(frozen=True)
class QaEnv:
    name: str
    base_url: str
    auth_default: str = "default"
    accounts: dict[str, QaAccount] = field(default_factory=dict)
    db_url_env: str = ""
    script_runner: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class QaWorker:
    id: str
    provider: str | None
    model: str | None
    concurrency: int
    priority: int


@dataclass(frozen=True)
class QaBrowser:
    channel: str = "chrome"
    headed: bool = False


@dataclass(frozen=True)
class QaConfig:
    active_env: str
    env: QaEnv
    browser: QaBrowser
    workers: tuple[QaWorker, ...]

    @property
    def total_concurrency(self) -> int:
        return sum(w.concurrency for w in self.workers)

    @property
    def headed(self) -> bool:
        if self.total_concurrency > 1:
            return False
        return self.browser.headed


def _blank(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, field: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError) as e:
        raise TestRejected(f"qa.yaml {field} must be an integer") from e
    return n


def _parse_accounts(raw: Any) -> dict[str, QaAccount]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise TestRejected("qa.yaml auth.accounts must be a mapping")
    out: dict[str, QaAccount] = {}
    for name, item in raw.items():
        if not isinstance(item, dict):
            raise TestRejected(f"qa.yaml auth.accounts.{name} must be a mapping")
        out[str(name)] = QaAccount(
            name=str(name),
            username_env=_blank(item.get("username_env")),
            password_env=_blank(item.get("password_env")),
            state_file=_blank(item.get("state_file")),
        )
    return out


def _parse_env(name: str, raw: Any) -> QaEnv:
    if not isinstance(raw, dict):
        raise TestRejected(f"qa.yaml envs.{name} must be a mapping")
    base_url = _blank(raw.get("base_url"))
    if not base_url:
        raise TestRejected("qa.yaml is missing base_url; add envs.local.base_url")
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    db = raw.get("db") if isinstance(raw.get("db"), dict) else {}
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    notes_raw = raw.get("notes") or []
    if notes_raw and not isinstance(notes_raw, list):
        raise TestRejected("qa.yaml notes must be a list")
    default = _blank(auth.get("default")) or "default"
    accounts = _parse_accounts(auth.get("accounts"))
    return QaEnv(
        name=name,
        base_url=base_url,
        auth_default=default,
        accounts=accounts,
        db_url_env=_blank(db.get("url_env")),
        script_runner=_blank(script.get("runner")),
        notes=tuple(str(n) for n in notes_raw),
    )


def _parse_workers(root: Path, raw: Any) -> tuple[QaWorker, ...]:
    if raw is None:
        raw = [{}]
    if not isinstance(raw, list) or not raw:
        raise TestRejected("qa.yaml workers must be a non-empty list")
    used_ids: set[str] = set()
    workers: list[QaWorker] = []
    auto_n = 1
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TestRejected(f"qa.yaml workers[{i}] must be a mapping")
        provider = _blank(item.get("provider")) or None
        model = _blank(item.get("model")) or None
        if (provider and not model) or (model and not provider):
            raise TestRejected(
                f"qa.yaml workers[{i}]: provider and model must be set together"
            )
        if not provider and not model:
            pair = resolve_pi_choice(root, "qa-run")
            provider, model = pair[0], pair[1]
            if not provider or not model:
                raise TestRejected(
                    "qa.yaml worker has no provider/model and "
                    "resolve_pi_choice(qa-run) is empty"
                )
        wid = _blank(item.get("id")) or (model or f"w{auto_n}")
        auto_n += 1
        if wid in used_ids:
            raise TestRejected(f"qa.yaml workers id {wid!r} is not unique")
        used_ids.add(wid)
        conc = _int(item.get("concurrency"), f"workers[{i}].concurrency", 1)
        if conc < 1:
            raise TestRejected(f"qa.yaml workers[{i}].concurrency must be >= 1")
        if conc > 8:
            raise TestRejected(f"qa.yaml workers[{i}].concurrency must be <= 8")
        priority = _int(item.get("priority"), f"workers[{i}].priority", 100)
        workers.append(
            QaWorker(
                id=wid,
                provider=provider,
                model=model,
                concurrency=conc,
                priority=priority,
            )
        )
    total = sum(w.concurrency for w in workers)
    if total > 8:
        raise TestRejected(f"qa.yaml workers concurrency sum is {total}; max is 8")
    return tuple(workers)


def _pick_env(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    envs = data.get("envs") or {}
    if not isinstance(envs, dict) or not envs:
        raise TestRejected("qa.yaml needs envs.local (or a single envs key)")
    active = _blank(data.get("active_env")) or "local"
    if "local" in envs and isinstance(envs["local"], dict):
        return "local", envs["local"]
    if len(envs) == 1:
        name = next(iter(envs))
        raw = envs[name]
        if not isinstance(raw, dict):
            raise TestRejected(f"qa.yaml envs.{name} must be a mapping")
        return name, raw
    if active in envs and active not in {"test", "k8s"}:
        raw = envs[active]
        if not isinstance(raw, dict):
            raise TestRejected(f"qa.yaml envs.{active} must be a mapping")
        return active, raw
    raise TestRejected("qa.yaml needs envs.local (first knife is local only)")


def load_qa_config(root: Path) -> QaConfig:
    path = paths.qa_yaml(root)
    if not path.is_file():
        raise TestRejected("missing qa.yaml; add workspace qa.yaml with envs.local.base_url")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TestRejected("qa.yaml must be a mapping")
    env_name, env_raw = _pick_env(data)
    env = _parse_env(env_name, env_raw)
    browser_raw = data.get("browser") if isinstance(data.get("browser"), dict) else {}
    headed = browser_raw.get("headed", False)
    if isinstance(headed, str):
        headed = headed.lower() in {"true", "yes", "1"}
    browser = QaBrowser(
        channel=_blank(browser_raw.get("channel")) or "chrome",
        headed=bool(headed),
    )
    workers = _parse_workers(root, data.get("workers"))
    return QaConfig(active_env=env_name, env=env, browser=browser, workers=workers)
