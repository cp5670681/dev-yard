from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.config import resolve_pi_choice


class TestRejected(ValueError):
    """req test refused (gate, config, mutation, DAG)."""

    __test__ = False


class QaConfigUnreadable(TestRejected):
    """qa.yaml exists but cannot be parsed; the form must not clobber it."""


# env keys the form owns; anything else in that env is carried through a save
_MANAGED_ENV_KEYS = frozenset({"base_url", "auth", "db", "script", "notes"})


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


def _parse_config(root: Path, data: dict[str, Any]) -> QaConfig:
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


def load_qa_config(root: Path) -> QaConfig:
    path = paths.qa_yaml(root)
    if not path.is_file():
        raise TestRejected("missing qa.yaml; add workspace qa.yaml with envs.local.base_url")
    return _parse_config(root, _read_data(root))


def _read_data(root: Path) -> dict[str, Any]:
    """qa.yaml as a mapping. Missing or empty reads as {}; unreadable raises."""
    path = paths.qa_yaml(root)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise QaConfigUnreadable(f"qa.yaml is not UTF-8: {e}") from e
    except OSError as e:
        raise QaConfigUnreadable(f"qa.yaml cannot be read: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise QaConfigUnreadable(f"qa.yaml is not readable YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise QaConfigUnreadable("qa.yaml must be a mapping")
    if data.get("envs") is not None and not isinstance(data["envs"], dict):
        raise QaConfigUnreadable("qa.yaml envs must be a mapping")
    return data


def _raw_mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TestRejected(f"qa.yaml {field} must be a mapping")
    return value


def _env_to_raw(raw: Any, field: str) -> dict[str, Any]:
    """Form payload for one env → the YAML mapping that env is written as."""
    env = _raw_mapping(raw, field)
    auth = _raw_mapping(env.get("auth"), f"{field}.auth")
    accounts_raw = _raw_mapping(auth.get("accounts"), f"{field}.auth.accounts")
    accounts: dict[str, Any] = {}
    for name, item in accounts_raw.items():
        entry_in = _raw_mapping(item, f"{field}.auth.accounts.{name}")
        entry: dict[str, Any] = {}
        for key in ("username_env", "password_env", "state_file"):
            value = _blank(entry_in.get(key))
            if value:
                entry[key] = value
        if entry:
            accounts[str(name)] = entry
    out: dict[str, Any] = {"base_url": _blank(env.get("base_url"))}
    auth_out: dict[str, Any] = {"default": _blank(auth.get("default")) or "default"}
    if accounts:
        auth_out["accounts"] = accounts
    out["auth"] = auth_out
    db = _raw_mapping(env.get("db"), f"{field}.db")
    if _blank(db.get("url_env")):
        out["db"] = {"url_env": _blank(db.get("url_env"))}
    script = _raw_mapping(env.get("script"), f"{field}.script")
    if _blank(script.get("runner")):
        out["script"] = {"runner": _blank(script.get("runner"))}
    notes = env.get("notes") or []
    if not isinstance(notes, list):
        raise TestRejected("qa.yaml notes must be a list")
    out["notes"] = [str(n).strip() for n in notes if str(n).strip()]
    return out


def _workers_to_raw(raw: Any) -> list[dict[str, Any]]:
    """Form payload rows → worker mappings; defaults are left implicit."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TestRejected("qa.yaml workers must be a list")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        row = _raw_mapping(item, f"workers[{i}]")
        entry: dict[str, Any] = {}
        wid = _blank(row.get("id"))
        if wid:
            entry["id"] = wid
        for key in ("provider", "model"):
            value = _blank(row.get(key))
            if value:
                entry[key] = value
        conc = _int(row.get("concurrency"), f"workers[{i}].concurrency", 1)
        if conc != 1:
            entry["concurrency"] = conc
        priority = _int(row.get("priority"), f"workers[{i}].priority", 100)
        if priority != 100:
            entry["priority"] = priority
        out.append(entry)
    return out


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _workers_payload(root: Path, raw: Any) -> list[dict[str, Any]]:
    """Project the workers key onto form rows without validating it.

    Missing provider/model is shown as the qa-run fallback it will resolve to,
    so the form previews what would actually run.
    """
    rows = raw if isinstance(raw, list) and raw else [None]
    fallback = resolve_pi_choice(root, "qa-run")
    out: list[dict[str, Any]] = []
    for item in rows:
        row = item if isinstance(item, dict) else {}
        provider = _blank(row.get("provider"))
        model = _blank(row.get("model"))
        if not provider and not model:
            provider, model = fallback[0] or "", fallback[1] or ""
        out.append(
            {
                "id": _blank(row.get("id")),
                "provider": provider,
                "model": model,
                "concurrency": _int_or(row.get("concurrency"), 1),
                "priority": _int_or(row.get("priority"), 100),
            }
        )
    return out


def _env_payload(raw: Any) -> dict[str, Any]:
    """Project one env onto form fields without validating it."""
    env = raw if isinstance(raw, dict) else {}
    auth = env.get("auth") if isinstance(env.get("auth"), dict) else {}
    accounts_raw = auth.get("accounts") if isinstance(auth.get("accounts"), dict) else {}
    accounts: dict[str, Any] = {}
    for name, item in accounts_raw.items():
        row = item if isinstance(item, dict) else {}
        accounts[str(name)] = {
            "username_env": _blank(row.get("username_env")),
            "password_env": _blank(row.get("password_env")),
            "state_file": _blank(row.get("state_file")),
        }
    db = env.get("db") if isinstance(env.get("db"), dict) else {}
    script = env.get("script") if isinstance(env.get("script"), dict) else {}
    notes = env.get("notes") if isinstance(env.get("notes"), list) else []
    return {
        "base_url": _blank(env.get("base_url")),
        "auth": {
            "default": _blank(auth.get("default")) or "default",
            "accounts": accounts,
        },
        "db": {"url_env": _blank(db.get("url_env"))},
        "script": {"runner": _blank(script.get("runner"))},
        "notes": [str(n) for n in notes],
    }


def _browser_payload(raw: Any) -> dict[str, Any]:
    browser = raw if isinstance(raw, dict) else {}
    headed = browser.get("headed", False)
    if isinstance(headed, str):
        headed = headed.lower() in {"true", "yes", "1"}
    return {"channel": _blank(browser.get("channel")) or "chrome", "headed": bool(headed)}


def default_qa_payload(root: Path) -> dict[str, Any]:
    """What the form opens with when qa.yaml is absent or has no envs yet.

    The worker row is pre-filled from the qa-run fallback so that a bare
    workspace can be saved straight from the form.
    """
    return {
        "active_env": "local",
        "browser": _browser_payload(None),
        "workers": _workers_payload(root, None),
        "envs": {"local": _env_payload(None)},
        "other_envs": [],
    }


def qa_payload(root: Path) -> dict[str, Any]:
    """Read qa.yaml as form fields.

    Deliberately lenient: an unreadable file raises, but a parsable file that
    fails validation still projects — repairing it is the point of the form.
    Every readable key is projected even when there is no env to edit, or a
    save would silently replace the keys the form never showed.
    Only the active env is editable; the rest are named for read-only display.
    """
    data = _read_data(root)
    envs = data.get("envs") if isinstance(data.get("envs"), dict) else {}
    browser = _browser_payload(data.get("browser"))
    workers = _workers_payload(root, data.get("workers"))
    if not envs:
        return {
            "active_env": "local",
            "browser": browser,
            "workers": workers,
            "envs": {"local": _env_payload(None)},
            "other_envs": [],
        }
    try:
        env_name, env_raw = _pick_env(data)
    except TestRejected:
        # Only non-local envs exist; v1 cannot edit those, but the page must
        # still open so a local env can be added.
        env_name, env_raw = "local", None
    return {
        "active_env": env_name,
        "browser": browser,
        "workers": workers,
        "envs": {env_name: _env_payload(env_raw)},
        "other_envs": sorted(k for k in envs if k != env_name),
    }


def save_qa_config(root: Path, payload: Any) -> None:
    """Validate the form payload with the same parsers as load, then write.

    Keys for envs other than the active one (test/k8s) are carried through
    untouched; v1 only edits one env. A qa.yaml that cannot be parsed is
    never overwritten — fix or delete it first.
    """
    if not isinstance(payload, dict):
        raise TestRejected("qa payload must be a mapping")
    data = _read_data(root)
    env_name = _blank(payload.get("active_env")) or "local"
    envs_in = payload.get("envs")
    if not isinstance(envs_in, dict) or env_name not in envs_in:
        raise TestRejected(f"qa payload must carry envs.{env_name}")
    out: dict[str, Any] = {}
    if env_name != "local":
        out["active_env"] = env_name
    browser_raw = _raw_mapping(payload.get("browser"), "browser")
    headed = browser_raw.get("headed", False)
    if isinstance(headed, str):
        headed = headed.lower() in {"true", "yes", "1"}
    out["browser"] = {
        "channel": _blank(browser_raw.get("channel")) or "chrome",
        "headed": bool(headed),
    }
    workers = _workers_to_raw(payload.get("workers"))
    if workers:
        out["workers"] = workers
    merged = dict(_raw_mapping(data.get("envs"), "envs"))
    previous = merged.get(env_name)
    rebuilt = _env_to_raw(envs_in[env_name], f"envs.{env_name}")
    if isinstance(previous, dict):
        # Keys the form does not own stay as written; the ones it does own
        # follow the form, so clearing a field clears it.
        kept = {k: v for k, v in previous.items() if k not in _MANAGED_ENV_KEYS}
        merged[env_name] = {**kept, **rebuilt}
    else:
        merged[env_name] = rebuilt
    out["envs"] = merged
    # Validate the env actually written, not whichever _pick_env would read.
    _parse_env(env_name, merged[env_name])
    _parse_workers(root, out.get("workers"))
    path = paths.qa_yaml(root)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    os.replace(tmp, path)
