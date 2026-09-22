"""Requirement-scoped account discovery and one-click auto-fill.

Discovery is a *read-only* single-statement query the QA design stage writes to
``qa/accounts-discover.sql``. It returns one row per candidate: the ``username``
in the first column and an optional semantic ``account_key`` in the second
(e.g. ``has_perm`` / ``no_perm``). ``account_key`` is the name a case references
in its frontmatter ``account:`` field.

Accounts always land in ``.yard-qa/requirements/<JIRA>/accounts.yaml`` — the
global ``qa.yaml`` is never touched. This keeps a requirement's permission mix
private to that requirement and safe to regenerate when permissions drift.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dev_yard import paths
from dev_yard.qa_config import (
    QaAccount,
    QaConfig,
    TestRejected,
    default_state_file,
    load_req_accounts,
    save_req_accounts,
)

_READONLY_SQL = frozenset({"select", "show", "desc", "describe", "explain", "with", "table"})
# Heuristic, not a real SQL parser: blocks obvious writes/exfiltration. The DB
# account's own privileges remain the real guard.
_WRITE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge"
    r"|call|do|copy|vacuum|analyze|refresh|into|outfile|load_file|dblink"
    r"|pg_read_file|lo_import|lo_export)\b",
    re.I,
)

_ROW_SEP = "|"
_FALLBACK_KEY = "auto"


@dataclass(frozen=True)
class Candidate:
    """One discovered login the design stage can point a case at."""

    username: str
    key: str = ""


@dataclass(frozen=True)
class FillResult:
    path: Path
    default: str
    accounts: dict[str, str]
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def parse_candidates(stdout: str) -> list[Candidate]:
    """Parse ``usql -A -t`` rows: ``username`` or ``username|account_key``."""
    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(_ROW_SEP)
        username = parts[0].strip()
        key = parts[1].strip() if len(parts) > 1 else ""
        if not username:
            continue
        item = (key, username)
        if item in seen:
            continue
        seen.add(item)
        out.append(Candidate(username=username, key=key))
    return out


def discover(db_url: str, sql: str, timeout: int = 30) -> list[Candidate]:
    """Run the read-only discovery query and return parsed candidates."""
    if not db_url:
        raise ValueError("qa.yaml envs.<env>.db.url is not configured; cannot run --sql")
    binary = shutil.which("usql")
    if not binary:
        raise ValueError("usql not found; install it to discover accounts")
    text = sql.strip()
    if not text:
        raise ValueError("--sql must not be empty")
    if ";" in text.rstrip(";"):
        raise ValueError("--sql must be a single statement (no `;`)")
    head = text.split(None, 1)[0].lower()
    if head not in _READONLY_SQL:
        raise ValueError("--sql must be read-only (select/show/desc/explain/table)")
    if _WRITE_SQL.search(text):
        raise ValueError("--sql must be read-only (no write keywords)")
    try:
        r = subprocess.run(
            [binary, db_url, "-t", "-A", "-c", text],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError("discovery query timed out") from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
        raise ValueError(f"discovery query failed: {err}")
    return parse_candidates(r.stdout)


def first_per_key(
    candidates: list[Candidate],
    keys: set[str] | None = None,
    exclude: set[str] | None = None,
) -> list[Candidate]:
    """Keep the first candidate per ``account_key`` (row order wins).

    A distinct ``account_key`` implies a distinct login, so usernames in
    ``exclude`` (e.g. the global default account) are skipped — unless that
    would leave a key with nothing, in which case its first row is used.
    """
    exclude = exclude or set()
    groups: dict[str, list[Candidate]] = {}
    for cand in candidates:
        key = cand.key or _FALLBACK_KEY
        if keys and key not in keys:
            continue
        groups.setdefault(key, []).append(Candidate(username=cand.username, key=key))
    picked: list[Candidate] = []
    for items in groups.values():
        picked.append(next((c for c in items if c.username not in exclude), items[0]))
    return picked


def accounts_overview(root: Path, jira: str, cfg: QaConfig) -> dict:
    """Current requirement-scoped accounts, with passwords masked.

    Falls back to the global config when the requirement has no accounts.yaml
    yet, so the UI can show what a case would use before the first fill.
    """
    existing = load_req_accounts(root, jira, cfg.active_env)
    if existing:
        default, accounts = existing
        source = "requirement"
    else:
        default = cfg.env.auth_default or "default"
        accounts = cfg.env.accounts
        source = "global"
    return {
        "env": cfg.active_env,
        "base_url": cfg.env.base_url,
        "default": default,
        "source": source,
        "discover_sql": paths.qa_accounts_discover_sql(root, jira).is_file(),
        "accounts": [
            {
                "name": name,
                "username": acct.username,
                "has_password": bool(acct.password),
                "state_file": acct.state_file or default_state_file(cfg.active_env, name, jira),
            }
            for name, acct in accounts.items()
        ],
    }


def autofill(
    root: Path,
    jira: str,
    env: str,
    cfg: QaConfig,
    candidates: list[Candidate],
    force_relogin: bool = False,
) -> FillResult:
    """Write discovered candidates into the requirement's accounts.yaml.

    The global default account (and its password) is the source of truth for
    credentials: every discovered account reuses that password, so filling is
    truly one command. Existing manually-added accounts are preserved; only the
    discovered ``account_key`` entries are created or re-pointed.
    """
    global_default = cfg.env.auth_default or "default"
    gacct = cfg.env.accounts.get(global_default)
    if gacct is None or not gacct.username:
        raise TestRejected(
            "global default account is not configured; set qa.yaml "
            "envs.<env>.auth.accounts first"
        )
    if not gacct.password:
        raise TestRejected(
            f"global default account {global_default!r} has no password; "
            "auto-fill reuses it, so set one in qa.yaml"
        )

    picked = first_per_key(candidates, exclude={gacct.username})
    if not picked:
        raise TestRejected("discovery returned no candidates to fill")

    existing = load_req_accounts(root, jira, env)
    if existing:
        default, accounts = existing
        accounts = dict(accounts)
    else:
        default, accounts = global_default, {}
    if global_default not in accounts:
        # Carry the default along so the file is self-contained, re-homed under
        # the requirement namespace so two requirements never share a session.
        accounts[global_default] = QaAccount(
            name=global_default,
            username=gacct.username,
            password=gacct.password,
            state_file=default_state_file(env, global_default, jira),
        )
    if default not in accounts:
        default = global_default

    added: list[str] = []
    changed: list[str] = []
    for cand in picked:
        key = cand.key or _FALLBACK_KEY
        prev = accounts.get(key)
        if prev is None:
            added.append(key)
        elif prev.username != cand.username:
            changed.append(key)
        if prev is not None and prev.username != cand.username:
            _drop_cached_auth(root, env, jira, prev)
        accounts[key] = QaAccount(
            name=key,
            username=cand.username,
            password=gacct.password,
            state_file=default_state_file(env, key, jira),
        )

    if force_relogin:
        # Refresh means every account re-logs in, not just the re-pointed ones.
        for acct in accounts.values():
            _drop_cached_auth(root, env, jira, acct)

    path = save_req_accounts(root, jira, env, default, accounts)
    return FillResult(
        path=path,
        default=default,
        accounts={name: acct.username for name, acct in accounts.items()},
        added=added,
        changed=changed,
    )


def invalidate_all(root: Path, jira: str, env: str, cfg: QaConfig) -> list[str]:
    """Drop cached login state for every requirement account (force re-login)."""
    existing = load_req_accounts(root, jira, env)
    if not existing:
        return []
    _default, accounts = existing
    dropped: list[str] = []
    for name, acct in accounts.items():
        if _drop_cached_auth(root, env, jira, acct):
            dropped.append(name)
    return dropped


def _drop_cached_auth(root: Path, env: str, jira: str, acct: QaAccount) -> bool:
    rel = acct.state_file or default_state_file(env, acct.name, jira)
    state = root / rel
    replay = state.with_suffix(".replay.sh")
    dropped = False
    for path in (state, replay):
        try:
            path.unlink()
            dropped = True
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return dropped
