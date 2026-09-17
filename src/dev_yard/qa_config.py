from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
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
_MANAGED_TOP_KEYS = frozenset({"active_env", "browser", "workers", "envs"})
_MANAGED_BROWSER_KEYS = frozenset({"channel", "headed"})
_MANAGED_WORKER_KEYS = frozenset(
    {"id", "provider", "model", "concurrency", "priority"}
)
# managed keys nested one level below the env; the rest are carried through.
# Legacy `*_env` keys are included so a save drops them instead of carrying
# dead fields forward.
_MANAGED_ACCOUNT_KEYS = frozenset(
    {"username", "password", "state_file", "username_env", "password_env"}
)
_MANAGED_AUTH_KEYS = frozenset({"default", "accounts"})
_MANAGED_DB_KEYS = frozenset({"url", "url_env"})
_MANAGED_SCRIPT_KEYS = frozenset({"runner"})

# Secrets are stored plaintext in qa.yaml (gitignored). The web form never sees
# them: payloads carry this sentinel instead, and a save that returns it keeps
# the value already on disk.
MASK = "********"
# Matches `password: x`, `password="x"` and inline `{url: "x"}` alike. Quoted
# values run to their closing quote; bare values run to end of line or the flow
# delimiter, so an unterminated value (as a YAML parse error may echo) is still
# fully masked.
_SECRET = re.compile(
    r"""(?<![A-Za-z0-9_])(password|passwd|url|token|secret|api_key|apikey"""
    r"""|secret_key|access_key|private_key)([ \t]*[:=][ \t]*)"""
    r"""(?:"[^"]*"|'[^']*'|[^\n,}\]]+)"""
)
# Credentials embedded in a URL (`scheme://user:pass@host`), e.g. a base_url.
# Requires a colon in the userinfo so a bare `ssh://git@host` is left alone.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s:]*:[^/@\s]*@")


def redact_url(text: str) -> str:
    """Mask credentials embedded in URLs while keeping scheme and host."""
    return _URL_USERINFO.sub(lambda m: f"{m.group(1)}{MASK}@", text)


def redact_qa_yaml(text: str) -> str:
    """Mask credential values in a qa.yaml dump (or a parse error) for display."""
    masked = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    return redact_url(masked)


@dataclass(frozen=True)
class QaAccount:
    name: str
    username: str = ""
    password: str = ""
    state_file: str = ""


@dataclass(frozen=True)
class QaEnv:
    name: str
    base_url: str
    auth_default: str = "default"
    accounts: dict[str, QaAccount] = field(default_factory=dict)
    db_url: str = ""
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
    env_names: tuple[str, ...] = ()

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
            username=_blank(item.get("username")),
            password=_blank(item.get("password")),
            state_file=_blank(item.get("state_file")),
        )
    return out


def _parse_auth(raw: Any) -> tuple[str, dict[str, QaAccount]]:
    auth = raw if isinstance(raw, dict) else {}
    default = _blank(auth.get("default")) or "default"
    return default, _parse_accounts(auth.get("accounts"))


def _parse_env(name: str, raw: Any) -> QaEnv:
    if not isinstance(raw, dict):
        raise TestRejected(f"qa.yaml envs.{name} must be a mapping")
    base_url = _blank(raw.get("base_url"))
    if not base_url:
        raise TestRejected(f"qa.yaml envs.{name} is missing base_url")
    db = raw.get("db") if isinstance(raw.get("db"), dict) else {}
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    notes_raw = raw.get("notes") or []
    if notes_raw and not isinstance(notes_raw, list):
        raise TestRejected("qa.yaml notes must be a list")
    default, accounts = _parse_auth(raw.get("auth"))
    return QaEnv(
        name=name,
        base_url=base_url,
        auth_default=default,
        accounts=accounts,
        db_url=_blank(db.get("url")),
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


def _env_names(envs: dict[str, Any]) -> list[str]:
    return [str(k) for k in envs]


def _default_env_name(data: dict[str, Any], envs: dict[str, Any]) -> str:
    """Pick the env a run uses when no explicit name is given."""
    active = _blank(data.get("active_env"))
    if active and active in envs:
        return active
    if "local" in envs:
        return "local"
    return next(iter(envs))


def _select_env(
    data: dict[str, Any], env: str | None = None
) -> tuple[str, dict[str, Any]]:
    envs = data.get("envs") or {}
    if not isinstance(envs, dict) or not envs:
        raise TestRejected("qa.yaml needs envs.<name> with base_url")
    wanted = _blank(env)
    if wanted:
        if wanted not in envs:
            names = ", ".join(_env_names(envs))
            raise TestRejected(
                f"qa.yaml has no envs.{wanted}; available: {names}"
            )
        name = wanted
    else:
        name = _default_env_name(data, envs)
    raw = envs[name]
    if not isinstance(raw, dict):
        raise TestRejected(f"qa.yaml envs.{name} must be a mapping")
    return name, raw


def _parse_config(root: Path, data: dict[str, Any], env: str | None = None) -> QaConfig:
    env_name, env_raw = _select_env(data, env)
    env = _parse_env(env_name, env_raw)
    envs = data.get("envs")
    envs = envs if isinstance(envs, dict) else {}
    browser_raw = data.get("browser") if isinstance(data.get("browser"), dict) else {}
    headed = browser_raw.get("headed", False)
    if isinstance(headed, str):
        headed = headed.lower() in {"true", "yes", "1"}
    browser = QaBrowser(
        channel=_blank(browser_raw.get("channel")) or "chrome",
        headed=bool(headed),
    )
    workers = _parse_workers(root, data.get("workers"))
    return QaConfig(
        active_env=env_name,
        env=env,
        browser=browser,
        workers=workers,
        env_names=tuple(_env_names(envs)) or (env_name,),
    )


def load_qa_config(
    root: Path, env: str | None = None, jira: str | None = None
) -> QaConfig:
    """Workspace qa.yaml, with a requirement's accounts overlaid when present.

    A requirement that needs more than the global default keeps its accounts in
    `.yard-qa/requirements/<JIRA>/accounts.yaml` (gitignored). That file only
    carries `accounts`/`default`; base_url, db, script and notes stay global.
    """
    path = paths.qa_yaml(root)
    if not path.is_file():
        raise TestRejected(
            "missing qa.yaml; add workspace qa.yaml with envs.<name>.base_url"
        )
    cfg = _parse_config(root, _read_data(root), env)
    if not jira:
        return cfg
    overlay = load_req_accounts(root, jira, cfg.active_env)
    if overlay is None:
        return cfg
    default, accounts = overlay
    return replace(cfg, env=replace(cfg.env, auth_default=default, accounts=accounts))


def load_req_accounts(
    root: Path, jira: str, env: str
) -> tuple[str, dict[str, QaAccount]] | None:
    """The requirement's accounts for `env`, or None to use the global config."""
    path = paths.req_accounts_yaml(root, jira)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise TestRejected(
            f"{path} is not readable YAML: {redact_qa_yaml(str(e))}"
        ) from e
    envs = data.get("envs") if isinstance(data, dict) else None
    raw = envs.get(env) if isinstance(envs, dict) else None
    if not isinstance(raw, dict):
        return None
    default, accounts = _parse_auth(raw)
    if not accounts:
        return None
    return default, accounts


def _accounts_to_raw(accounts: dict[str, QaAccount]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name, acct in accounts.items():
        entry: dict[str, str] = {}
        for key, value in (
            ("username", acct.username),
            ("password", acct.password),
            ("state_file", acct.state_file),
        ):
            if value:
                entry[key] = value
        out[str(name)] = entry
    return out


def _assert_secret_ignored(root: Path, path: Path) -> None:
    """Refuse to write plaintext secrets into a tracked / unignored git path."""
    import subprocess

    def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    inside = _git("rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = str(path)
    tracked = _git("ls-files", "--error-unmatch", rel)
    if tracked is not None and tracked.returncode == 0:
        raise TestRejected(
            f"{rel} is tracked by git; plaintext secrets must not be committed. "
            "Run `git rm --cached` and add it to .gitignore."
        )
    ignored = _git("check-ignore", "-q", rel)
    if ignored is not None and ignored.returncode != 0:
        raise TestRejected(
            f"{rel} is not gitignored; refusing to write plaintext secrets there. "
            "Add it (or `.yard-qa/`) to .gitignore."
        )


def save_req_accounts(
    root: Path,
    jira: str,
    env: str,
    default: str,
    accounts: dict[str, QaAccount],
) -> Path:
    """Write a requirement's accounts for one env, preserving its other envs."""
    if not accounts:
        raise TestRejected("requirement accounts must not be empty")
    if default not in accounts:
        raise TestRejected(
            f"default {default!r} is not one of the accounts: {', '.join(accounts)}"
        )
    path = paths.req_accounts_yaml(root, jira)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as e:
            raise TestRejected(
                f"{path} is not readable YAML: {redact_qa_yaml(str(e))}"
            ) from e
        if isinstance(loaded, dict):
            data = loaded
    envs = data.get("envs") if isinstance(data.get("envs"), dict) else {}
    prev = envs.get(env) if isinstance(envs.get(env), dict) else {}
    entry = {k: v for k, v in prev.items() if k not in {"default", "accounts"}}
    entry["default"] = default or "default"
    entry["accounts"] = _accounts_to_raw(accounts)
    envs[env] = entry
    data["envs"] = envs
    _assert_secret_ignored(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


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
        raise QaConfigUnreadable(
            f"qa.yaml is not readable YAML: {redact_qa_yaml(str(e))}"
        ) from e
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


def _env_to_raw(raw: Any, field: str, previous: Any = None) -> dict[str, Any]:
    """Form payload for one env → the YAML mapping that env is written as.

    Keys the form does not own — at the env level and inside auth/db/script —
    are carried over from `previous`, so a save never drops hand-written config.
    """
    env = _raw_mapping(raw, field)
    prev = previous if isinstance(previous, dict) else {}

    prev_auth = prev.get("auth") if isinstance(prev.get("auth"), dict) else {}
    prev_accounts = (
        prev_auth.get("accounts") if isinstance(prev_auth.get("accounts"), dict) else {}
    )
    auth = _raw_mapping(env.get("auth"), f"{field}.auth")
    accounts_raw = _raw_mapping(auth.get("accounts"), f"{field}.auth.accounts")
    accounts: dict[str, Any] = {}
    for name, item in accounts_raw.items():
        entry_in = _raw_mapping(item, f"{field}.auth.accounts.{name}")
        prev_entry = (
            prev_accounts.get(name) if isinstance(prev_accounts.get(name), dict) else {}
        )
        entry = {k: v for k, v in prev_entry.items() if k not in _MANAGED_ACCOUNT_KEYS}
        for key in ("username", "password", "state_file"):
            value = _blank(entry_in.get(key))
            if not value:
                continue
            if key == "password" and value == MASK:
                value = _blank(prev_entry.get("password"))
                if not value:
                    continue
            entry[key] = value
        if entry:
            accounts[str(name)] = entry
    out: dict[str, Any] = {"base_url": _blank(env.get("base_url"))}
    auth_out = {k: v for k, v in prev_auth.items() if k not in _MANAGED_AUTH_KEYS}
    auth_out["default"] = _blank(auth.get("default")) or "default"
    if accounts:
        auth_out["accounts"] = accounts
    out["auth"] = auth_out

    db = _raw_mapping(env.get("db"), f"{field}.db")
    prev_db = prev.get("db") if isinstance(prev.get("db"), dict) else {}
    db_out = {k: v for k, v in prev_db.items() if k not in _MANAGED_DB_KEYS}
    db_url = _blank(db.get("url"))
    if db_url == MASK:
        db_url = _blank(prev_db.get("url"))
    if db_url:
        db_out["url"] = db_url
    if db_out:
        out["db"] = db_out

    script = _raw_mapping(env.get("script"), f"{field}.script")
    prev_script = prev.get("script") if isinstance(prev.get("script"), dict) else {}
    script_out = {k: v for k, v in prev_script.items() if k not in _MANAGED_SCRIPT_KEYS}
    if _blank(script.get("runner")):
        script_out["runner"] = _blank(script.get("runner"))
    if script_out:
        out["script"] = script_out

    notes = env.get("notes") or []
    if not isinstance(notes, list):
        raise TestRejected("qa.yaml notes must be a list")
    out["notes"] = [str(n).strip() for n in notes if str(n).strip()]
    return out


def _workers_to_raw(raw: Any, previous: Any = None) -> list[dict[str, Any]]:
    """Form payload rows → worker mappings; defaults are left implicit.

    Worker keys the form does not own are carried over by row index from
    `previous`, so a save never drops hand-written per-worker config.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TestRejected("qa.yaml workers must be a list")
    prev = previous if isinstance(previous, list) else []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        row = _raw_mapping(item, f"workers[{i}]")
        prev_row = prev[i] if i < len(prev) and isinstance(prev[i], dict) else {}
        entry: dict[str, Any] = {
            k: v for k, v in prev_row.items() if k not in _MANAGED_WORKER_KEYS
        }
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
            "username": _blank(row.get("username")),
            "password": MASK if _blank(row.get("password")) else "",
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
        "db": {"url": MASK if _blank(db.get("url")) else ""},
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
        "env_names": ["local"],
    }


def qa_payload(root: Path) -> dict[str, Any]:
    """Read qa.yaml as form fields.

    Deliberately lenient: an unreadable file raises, but a parsable file that
    fails validation still projects — repairing it is the point of the form.
    Every readable key is projected; every env is editable, and `active_env`
    names the one a run uses unless overridden at run time.
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
            "env_names": ["local"],
        }
    names = _env_names(envs)
    active = _default_env_name(data, envs)
    return {
        "active_env": active,
        "browser": browser,
        "workers": workers,
        "envs": {name: _env_payload(envs.get(name)) for name in names},
        "env_names": names,
    }


def qa_env_choices(root: Path) -> tuple[list[str], str]:
    """Env names in qa.yaml plus the default one, without validating base_url.

    Returns ([], "") when the file is missing or has no envs; raises nothing.
    """
    try:
        data = _read_data(root)
    except QaConfigUnreadable:
        return [], ""
    envs = data.get("envs") if isinstance(data.get("envs"), dict) else {}
    if not envs:
        return [], ""
    return _env_names(envs), _default_env_name(data, envs)


def save_qa_config(root: Path, payload: Any) -> None:
    """Validate the form payload with the same parsers as load, then write.

    Every env in the payload is written; envs dropped from the payload are
    removed from qa.yaml. Keys the form does not own stay as written, so
    unknown config survives. Only the active env must be fully valid — other
    envs may be placeholders with no base_url yet. A qa.yaml that cannot be
    parsed is never overwritten — fix or delete it first.
    """
    if not isinstance(payload, dict):
        raise TestRejected("qa payload must be a mapping")
    data = _read_data(root)
    envs_in = payload.get("envs")
    if not isinstance(envs_in, dict) or not envs_in:
        raise TestRejected("qa payload must carry at least one env under envs")
    existing = _raw_mapping(data.get("envs"), "envs")
    renamed_raw = payload.get("renamed")
    renamed = renamed_raw if isinstance(renamed_raw, dict) else {}
    merged: dict[str, Any] = {}
    for raw_name, env_raw in envs_in.items():
        name = _blank(raw_name)
        if not name:
            raise TestRejected("qa.yaml env name must not be empty")
        previous = existing.get(name)
        if previous is None:
            # The form renamed this env: recover its old entry so masked
            # secrets resolve to the stored value instead of being dropped.
            old = renamed.get(name)
            if isinstance(old, str):
                previous = existing.get(old)
        prev_env = previous if isinstance(previous, dict) else {}
        # Keys the form does not own stay as written; the ones it does own
        # follow the form, so clearing a field clears it.
        kept = {k: v for k, v in prev_env.items() if k not in _MANAGED_ENV_KEYS}
        merged[name] = {**kept, **_env_to_raw(env_raw, f"envs.{name}", prev_env)}
    env_name = _blank(payload.get("active_env")) or next(iter(merged))
    if env_name not in merged:
        raise TestRejected(f"active_env {env_name!r} is not one of the envs")
    # The env a run would use must be runnable; the rest may be unfinished.
    _parse_env(env_name, merged[env_name])
    # Unknown top-level keys survive the save (the docstring's promise).
    out: dict[str, Any] = {
        k: v for k, v in data.items() if k not in _MANAGED_TOP_KEYS
    }
    out["active_env"] = env_name
    browser_raw = _raw_mapping(payload.get("browser"), "browser")
    headed = browser_raw.get("headed", False)
    if isinstance(headed, str):
        headed = headed.lower() in {"true", "yes", "1"}
    prev_browser = data.get("browser") if isinstance(data.get("browser"), dict) else {}
    browser_out = {
        k: v for k, v in prev_browser.items() if k not in _MANAGED_BROWSER_KEYS
    }
    browser_out["channel"] = _blank(browser_raw.get("channel")) or "chrome"
    browser_out["headed"] = bool(headed)
    out["browser"] = browser_out
    workers = _workers_to_raw(payload.get("workers"), data.get("workers"))
    if workers:
        out["workers"] = workers
    out["envs"] = merged
    _parse_workers(root, out.get("workers"))
    path = paths.qa_yaml(root)
    _assert_secret_ignored(root, path)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    os.replace(tmp, path)
