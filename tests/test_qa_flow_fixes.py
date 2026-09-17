from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

from dev_yard.qa import (
    _claim_run_dir,
    _run_lock,
    _tree_state,
    _write_skipped_result,
    discover_cases,
    find_incomplete_run,
    write_context_md,
)
from dev_yard.qa_config import (
    MASK,
    QaAccount,
    TestRejected,
    load_qa_config,
    redact_qa_yaml,
    save_qa_config,
    save_req_accounts,
)
from dev_yard.qa_report import map_qa_result
from dev_yard.qa_schedule import CaseJob, PoolSlot, run_schedule
from dev_yard.service import init_yard


def _case_file(qa: Path, name: str, cid: str, body: str = "body\n") -> Path:
    p = qa / "cases" / "mod" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nid: {cid}\ntitle: t\nrepo: backend\n---\n\n{body}", encoding="utf-8"
    )
    return p


# --- #1 case id validation ------------------------------------------------


def test_duplicate_case_id_rejected(tmp_path: Path):
    qa = tmp_path / "qa"
    _case_file(qa, "case-01.md", "dup")
    _case_file(qa, "case-02.md", "dup")
    with pytest.raises(TestRejected, match="duplicate case id"):
        discover_cases(qa)


def test_case_id_traversal_rejected(tmp_path: Path):
    qa = tmp_path / "qa"
    _case_file(qa, "case-01.md", "../../escape")
    with pytest.raises(TestRejected, match="invalid case id"):
        discover_cases(qa)


# --- #5 same-account serialization ----------------------------------------


def test_same_account_cases_overlap_by_default():
    cases = [
        CaseJob(id="c1", title="a", repo="be", priority="P0", account="acct"),
        CaseJob(id="c2", title="b", repo="be", priority="P0", account="acct"),
    ]
    pools = [PoolSlot(id="p", provider="rcc", model="m", concurrency=2, priority=1)]
    guard = threading.Lock()
    live = {"acct": 0}
    peak = {"acct": 0}

    def run(job, slot):
        with guard:
            live["acct"] += 1
            peak["acct"] = max(peak["acct"], live["acct"])
        time.sleep(0.05)
        with guard:
            live["acct"] -= 1
        return {"status": "passed"}

    run_schedule(cases, pools, run)
    assert peak["acct"] == 2
    assert all(c.state == "passed" for c in cases)


def test_same_account_cases_serialize_when_enabled():
    cases = [
        CaseJob(id="c1", title="a", repo="be", priority="P0", account="acct"),
        CaseJob(id="c2", title="b", repo="be", priority="P0", account="acct"),
    ]
    pools = [PoolSlot(id="p", provider="rcc", model="m", concurrency=2, priority=1)]
    guard = threading.Lock()
    live = {"acct": 0}
    peak = {"acct": 0}

    def run(job, slot):
        with guard:
            live["acct"] += 1
            peak["acct"] = max(peak["acct"], live["acct"])
        time.sleep(0.05)
        with guard:
            live["acct"] -= 1
        return {"status": "passed"}

    run_schedule(cases, pools, run, serialize_accounts=True)
    assert peak["acct"] == 1
    assert all(c.state == "passed" for c in cases)


# --- #1 requirement-scoped login state ------------------------------------


def test_req_accounts_state_file_is_namespaced_by_jira(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "qa.yaml").write_text(
        "active_env: local\nenvs:\n  local:\n    base_url: http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    save_req_accounts(
        tmp_path,
        "J-1",
        "local",
        "admin",
        {"admin": QaAccount("admin", username="u", password="p")},
    )
    save_req_accounts(
        tmp_path,
        "J-2",
        "local",
        "admin",
        {"admin": QaAccount("admin", username="u", password="p")},
    )
    a = load_qa_config(tmp_path, jira="J-1").env.accounts["admin"].state_file
    b = load_qa_config(tmp_path, jira="J-2").env.accounts["admin"].state_file
    assert a == ".yard-qa/requirements/J-1/auth-local-admin.json"
    assert b == ".yard-qa/requirements/J-2/auth-local-admin.json"
    assert a != b


# --- #3 resume only matches the right env / case set ----------------------


def _run_with_progress(tmp_path: Path, env: str, ids: list[str]) -> Path:
    run = tmp_path / "qa" / "evidence" / "2026-01-01-000000"
    run.mkdir(parents=True)
    cases = "".join(f"  - {{id: {i}, state: running}}\n" for i in ids)
    (run / "progress.yaml").write_text(
        f"run_id: 2026-01-01-000000\nenv: {env}\ncases:\n{cases}", encoding="utf-8"
    )
    return run


def test_find_incomplete_run_checks_env(tmp_path: Path):
    qa = tmp_path / "qa"
    _run_with_progress(tmp_path, "test", ["c1"])
    assert find_incomplete_run(qa, {"c1"}, "local") is None
    assert find_incomplete_run(qa, {"c1"}, "test") is not None


def test_find_incomplete_run_checks_case_set(tmp_path: Path):
    qa = tmp_path / "qa"
    _run_with_progress(tmp_path, "local", ["c1"])
    assert find_incomplete_run(qa, {"c1", "c2"}, "local") is None
    assert find_incomplete_run(qa, {"c1"}, "local") is not None


def test_find_incomplete_run_none_when_all_terminal(tmp_path: Path):
    qa = tmp_path / "qa"
    run = _run_with_progress(tmp_path, "local", ["c1"])
    (run / "progress.yaml").write_text(
        "run_id: 2026-01-01-000000\nenv: local\n"
        "cases:\n  - {id: c1, state: passed}\n",
        encoding="utf-8",
    )
    (run / "result.yaml").write_text(
        "run_id: 2026-01-01-000000\nsummary: {total: 1, passed: 1}\n",
        encoding="utf-8",
    )
    assert find_incomplete_run(qa, {"c1"}, "local") is None


# --- #5 script revert must not delete someone else's file -----------------


def test_revert_new_paths_skips_files_outside_script_window(tmp_path: Path):
    from dev_yard.qa_exec import _created_in_window

    p = tmp_path / "old.txt"
    p.write_text("x", encoding="utf-8")
    now = time.time()
    assert _created_in_window(p, (now - 100, now - 50)) is False
    assert _created_in_window(p, (now - 100, now)) is True


# --- #8 run lock / atomic run dir -----------------------------------------


def test_claim_run_dir_is_unique(tmp_path: Path):
    evidence = tmp_path / "evidence"
    a = _claim_run_dir(evidence)
    b = _claim_run_dir(evidence)
    assert a[0] != b[0]
    assert a[1].is_dir() and b[1].is_dir()


def test_run_lock_rejects_second_holder(tmp_path: Path):
    with _run_lock(tmp_path, "J-1"):
        with pytest.raises(TestRejected, match="in progress"):
            with _run_lock(tmp_path, "J-1"):
                pass


def test_run_lock_reclaims_stale(tmp_path: Path):
    lock_dir = tmp_path / ".yard-qa" / "locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "J-1.run.lock").write_text("999999", encoding="utf-8")
    with _run_lock(tmp_path, "J-1"):
        pass  # stale pid reclaimed, no raise


# --- #7 mutation snapshot sees edits to already-dirty files ---------------


def test_tree_state_changes_when_dirty_file_edited(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=x@y", "-c", "user.name=x", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    (root / "a.txt").write_text("dirty\n", encoding="utf-8")  # already dirty
    before = _tree_state(root)
    (root / "a.txt").write_text("dirty-and-changed\n", encoding="utf-8")
    after = _tree_state(root)
    assert before is not None and after is not None
    assert before["a.txt"] != after["a.txt"]


# --- #9 / #10 redaction ---------------------------------------------------


def test_redact_masks_base_url_userinfo():
    out = redact_qa_yaml("    base_url: http://user:pass@host:3000\n")
    assert "user:pass" not in out
    assert out.count(MASK) == 1


def test_redact_masks_token_keys():
    assert "abc" not in redact_qa_yaml("token: abc\n")


def test_redact_masks_apikey_and_keeps_bare_ssh_url():
    assert "abc" not in redact_qa_yaml("apikey: abc\n")
    assert "abc" not in redact_qa_yaml("secret_key: abc\n")
    # no password in the userinfo → leave the URL alone
    assert "ssh://git@host" in redact_qa_yaml("remote: ssh://git@host/x\n")


def test_porcelain_entry_decodes_quoted_paths():
    from dev_yard.qa import _porcelain_entry

    assert _porcelain_entry('M "a b.txt"') == ("M", "a b.txt")


def test_context_md_masks_base_url_userinfo(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: http://user:pw@127.0.0.1:8080\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(tmp_path)
    text = write_context_md(tmp_path, "J-1", cfg).read_text(encoding="utf-8")
    assert "user:pw" not in text
    assert "127.0.0.1:8080" in text


def test_run_schedule_propagates_assertions():
    cases = [CaseJob(id="c1", title="t", repo="be", priority="P0")]
    pools = [PoolSlot(id="p", provider="rcc", model="m", concurrency=1, priority=1)]

    def run(job, slot):
        return {
            "status": "failed",
            "assertions": [{"type": "ui", "status": "failed"}],
        }

    run_schedule(cases, pools, run)
    assert cases[0].assertions[0]["type"] == "ui"


def test_qa_yaml_parse_error_is_redacted(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "qa.yaml").write_text(
        'envs:\n  local:\n    base_url: http://x\n    db:\n      url: "pg://u:SECRET\n',
        encoding="utf-8",
    )
    with pytest.raises(TestRejected) as err:
        load_qa_config(tmp_path)
    assert "SECRET" not in str(err.value)


# --- #11 routes in context.md ---------------------------------------------


def test_context_md_lists_meta_routes(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n", encoding="utf-8"
    )
    qa = tmp_path / "reqs" / "J-1" / "qa"
    qa.mkdir(parents=True)
    (qa / "meta.yaml").write_text("routes:\n  login: /login\n", encoding="utf-8")
    cfg = load_qa_config(tmp_path)
    text = write_context_md(tmp_path, "J-1", cfg).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8080/login" in text


# --- #13 skipped-only run is not ingested ---------------------------------


def test_map_qa_result_skips_skipped_only_run():
    run = {"summary": {"passed": 0, "failed": 0, "blocked": 0, "skipped": 3, "total": 3}}
    assert map_qa_result(run, [{"case": "c1", "status": "skipped"}]) is None


# --- #8 worker timeout ----------------------------------------------------


def test_run_pi_print_kills_on_timeout(tmp_path: Path):
    import sys

    from dev_yard.runners import run_pi_print

    code, out = run_pi_print(
        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        tmp_path,
        "",
        timeout=0.3,
    )
    assert code == 124
    assert "timed out" in out


# --- #14 assertions are surfaced ------------------------------------------


def test_read_case_result_surfaces_assertions(tmp_path: Path):
    from dev_yard.qa import _read_case_result

    p = tmp_path / "result.yaml"
    p.write_text(
        "case: c1\nstatus: failed\n"
        "assertions:\n  - type: ui\n    expected: a\n    actual: b\n    status: failed\n"
        "failure: {step: 1, step_desc: boom, evidence: x.png}\n",
        encoding="utf-8",
    )
    got = _read_case_result(
        p,
        CaseJob(id="c1", title="t", repo="be"),
        PoolSlot(id="p", provider="rcc", model="m", concurrency=1, priority=1),
    )
    assert got["assertions"][0]["type"] == "ui"



# --- #15 skipped result carries model/provider ----------------------------

def test_write_skipped_result_includes_model(tmp_path: Path):
    job = CaseJob(id="c1", title="t", repo="be", state="skipped", reason="dep")
    job.model = "grok-4"
    job.provider = "rcc"
    path = tmp_path / "result.yaml"
    _write_skipped_result(path, job)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["model"] == "grok-4"
    assert data["provider"] == "rcc"


# --- #16 unknown top-level keys survive a save ----------------------------


def _payload() -> dict:
    return {
        "active_env": "local",
        "browser": {"channel": "chrome", "headed": False},
        "workers": [],
        "envs": {"local": {"base_url": "http://127.0.0.1:8080", "notes": []}},
    }


def test_save_keeps_unknown_top_level_and_browser_keys(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "qa.yaml").write_text(
        "future_top: keep\n"
        "browser:\n  channel: chrome\n  headed: false\n  future_browser: keep\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    save_qa_config(tmp_path, _payload())
    data = yaml.safe_load((tmp_path / "qa.yaml").read_text(encoding="utf-8"))
    assert data["future_top"] == "keep"
    assert data["browser"]["future_browser"] == "keep"


# --- #3 env rename keeps masked secrets -----------------------------------


def test_env_rename_keeps_masked_secret(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    payload = _payload()
    payload["envs"]["local"] = {
        "base_url": "http://127.0.0.1:8080",
        "auth": {
            "default": "admin",
            "accounts": {
                "admin": {
                    "username": "admin",
                    "password": "s3cret",
                    "state_file": ".yard-qa/a.json",
                }
            },
        },
        "db": {"url": "postgres://u:pw@h/db"},
        "script": {"runner": ""},
        "notes": [],
    }
    save_qa_config(tmp_path, payload)
    # rename local -> staging via the form, secrets come back masked
    ren = _payload()
    ren["active_env"] = "staging"
    ren["renamed"] = {"staging": "local"}
    ren["envs"] = {
        "staging": {
            "base_url": "http://127.0.0.1:8080",
            "auth": {
                "default": "admin",
                "accounts": {
                    "admin": {"username": "admin", "password": MASK, "state_file": ".yard-qa/a.json"}
                },
            },
            "db": {"url": MASK},
            "script": {"runner": ""},
            "notes": [],
        }
    }
    save_qa_config(tmp_path, ren)
    cfg = load_qa_config(tmp_path, "staging")
    assert cfg.env.accounts["admin"].password == "s3cret"
    assert cfg.env.db_url == "postgres://u:pw@h/db"
