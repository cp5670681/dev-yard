"""req import: external requirement doc + code branch(es) straight into testing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.service import (
    init_yard,
    repo_add,
    repo_set_pi,
    req_freeze,
    req_import,
    req_open,
)


@pytest.fixture(autouse=True)
def _no_jira_env(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _local_repo_with_external_branch(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo whose `feature/x` forks from an older `main`.

    Returns (repo, feature_sha, fork_sha) where fork_sha is the merge-base and is
    strictly behind the current `main` tip.
    """
    repo = tmp_path / "src"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    fork_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "feature.txt").write_text("feature\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feature work")
    feature_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    (repo / "main2.txt").write_text("main advanced\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "main advances")
    return repo, feature_sha, fork_sha


def _remote_repo_with_external_branch(
    tmp_path: Path, test_branch: str = "PG-test"
) -> tuple[Path, Path, str, str]:
    """Bare origin + a work clone with `origin/feature/x` and a `test_branch`."""
    remote = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)])
    work = tmp_path / "work"
    subprocess.check_call(["git", "clone", "-q", str(remote), str(work)])
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "base.txt").write_text("base\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-q", "-u", "origin", "main")
    fork_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "-b", "feature/x")
    (work / "feature.txt").write_text("feature\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "feature work")
    feature_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-q", "-u", "origin", "feature/x")
    _git(work, "checkout", "-q", "main")
    _git(work, "checkout", "-q", "-b", test_branch)
    (work / "test-only.txt").write_text("test branch only\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "test branch commit")
    _git(work, "push", "-q", "-u", "origin", test_branch)
    _git(work, "checkout", "-q", "main")
    return remote, work, feature_sha, fork_sha


def test_req_import_freezes_external_branch_and_marks_done(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    repo, feature_sha, fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))

    data = req_import(
        yard,
        "AB-1",
        source="text",
        payload="# AB-1\n\n外部需求正文\n",
        branches={"backend": "feature/x"},
        submit=False,
    )

    assert data["phase"] == "frozen"
    assert data["contract_review"] == "passed"
    assert data["branch"] == "req/AB-1"
    assert data["tickets"]["T1"]["state"] == "done"
    assert data["imported"]["branches"] == {"backend": "feature/x"}
    # Base is the fork point, not the advanced main tip.
    assert data["base_shas"]["backend"] == fork_sha

    wt = paths.req_worktree(yard, "AB-1", "backend")
    assert (wt / ".git").exists()
    assert gitops.rev_parse(wt, "HEAD") == feature_sha
    assert (wt / "feature.txt").exists()
    assert not (wt / "main2.txt").exists()

    tickets_md = (paths.req_dir(yard, "AB-1") / "TICKETS.md").read_text(encoding="utf-8")
    assert "source: import" in tickets_md
    assert "# AB-1" in (paths.req_dir(yard, "AB-1") / "REQUIREMENT.md").read_text(
        encoding="utf-8"
    )


def test_req_import_base_override_is_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    main_tip = _git(repo, "rev-parse", "main")

    data = req_import(
        yard,
        "AB-2",
        source="text",
        payload="# AB-2\n",
        branches={"backend": "feature/x"},
        bases={"backend": "main"},
        submit=False,
    )
    assert data["base_shas"]["backend"] == main_tip


def test_req_import_rejects_unknown_alias(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _f, _c = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    with pytest.raises(ValueError, match="unknown repo alias"):
        req_import(
            yard,
            "AB-3",
            source="text",
            payload="# AB-3\n",
            branches={"frontend": "feature/x"},
            submit=False,
        )


def test_req_import_rejects_missing_ref(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _f, _c = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    with pytest.raises(ValueError, match="not found"):
        req_import(
            yard,
            "AB-4",
            source="text",
            payload="# AB-4\n",
            branches={"backend": "feature/nope"},
            submit=False,
        )


def test_req_import_existing_requires_force(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _f, _c = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    req_import(
        yard,
        "AB-5",
        source="text",
        payload="# AB-5\n",
        branches={"backend": "feature/x"},
        submit=False,
    )
    with pytest.raises(ValueError, match="already phase=frozen"):
        req_import(
            yard,
            "AB-5",
            source="text",
            payload="# AB-5\n",
            branches={"backend": "feature/x"},
            submit=False,
        )
    data = req_import(
        yard,
        "AB-5",
        source="text",
        payload="# AB-5\n",
        branches={"backend": "feature/x"},
        submit=False,
        force=True,
    )
    assert data["phase"] == "frozen"
    assert data["tickets"]["T1"]["state"] == "done"


def test_req_import_auto_submits_to_test_branch(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote, work, feature_sha, _fork_sha = _remote_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(remote), "main", "be", str(work))
    repo_set_pi(yard, "backend", None, None, test_branch="PG-test")

    data = req_import(
        yard,
        "AB-6",
        source="text",
        payload="# AB-6\n",
        branches={"backend": "origin/feature/x"},
    )

    assert data["phase"] == "testing"
    assert data["tickets"]["T1"]["state"] == "done"
    # The freeze branch was merged into the shared test branch on the remote.
    test_tip = subprocess.check_output(
        ["git", f"--git-dir={remote}", "rev-parse", "PG-test"], text=True
    ).strip()
    ancestor = subprocess.run(
        ["git", f"--git-dir={remote}", "merge-base", "--is-ancestor", feature_sha, test_tip],
        check=False,
    )
    assert ancestor.returncode == 0, "feature commit must be in the test branch"


def test_normal_freeze_still_records_origin_base_tip(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    d, _ = req_open(yard, "AB-7", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-7")
    data = st.load(yard, "AB-7")
    # No start_refs: unchanged behaviour, records the configured base tip.
    assert data["base_shas"]["backend"] == _git(repo, "rev-parse", "main")


def test_context_md_reports_fork_diff_base(tmp_path: Path, monkeypatch):
    from dev_yard.qa import write_context_md
    from dev_yard.qa_config import load_qa_config

    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    req_import(
        yard,
        "AB-8",
        source="text",
        payload="# AB-8\n",
        branches={"backend": "feature/x"},
        submit=False,
    )
    cfg = load_qa_config(yard, "local")
    text = write_context_md(yard, "AB-8", cfg).read_text(encoding="utf-8")
    assert f"diff_base {fork_sha}" in text


def test_cli_req_import(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from dev_yard.cli import app

    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    monkeypatch.chdir(yard)

    res = CliRunner().invoke(
        app,
        [
            "req",
            "import",
            "AB-9",
            "--text",
            "# AB-9\n",
            "--branch",
            "backend:feature/x",
            "--no-submit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "AB-9 phase=frozen" in res.stdout
    assert st.load(yard, "AB-9")["contract_review"] == "passed"


def test_req_import_into_existing_open_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    # `req open` then `req import` is the natural path and must not need --force.
    req_open(yard, "AB-10", source="text", payload="# old\n")

    data = req_import(
        yard,
        "AB-10",
        source="text",
        payload="# AB-10\n",
        branches={"backend": "feature/x"},
        submit=False,
    )
    assert data["phase"] == "frozen"
    assert data["tickets"]["T1"]["state"] == "done"


def test_req_import_bad_ref_leaves_tickets_untouched(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    d, _ = req_open(yard, "AB-11", source="text", payload="# AB-11\n")
    before = (d / "TICKETS.md").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not found"):
        req_import(
            yard,
            "AB-11",
            source="text",
            payload="# AB-11\n",
            branches={"backend": "feature/nope"},
            submit=False,
        )

    assert (d / "TICKETS.md").read_text(encoding="utf-8") == before
    assert not st.load(yard, "AB-11")["tickets"]


def test_req_import_rejects_leading_dash_ref(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    with pytest.raises(ValueError, match="must not start"):
        req_import(
            yard,
            "AB-12",
            source="text",
            payload="# AB-12\n",
            branches={"backend": "--upload-pack=evil"},
            submit=False,
        )


def test_req_import_without_test_branch_stays_frozen(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    logs: list[str] = []
    # No repo has a test_branch: nothing can be integrated, so stay frozen.
    data = req_import(
        yard,
        "AB-13",
        source="text",
        payload="# AB-13\n",
        branches={"backend": "feature/x"},
        on_progress=logs.append,
    )
    assert data["phase"] == "frozen"
    assert any("test_branch" in line for line in logs)


def test_req_import_multi_repo(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    be, _be_feat, be_fork = _local_repo_with_external_branch(tmp_path / "be")
    fe, _fe_feat, _fe_fork = _local_repo_with_external_branch(tmp_path / "fe")
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(be), "main", "be", str(be))
    repo_add(yard, "frontend", str(fe), "main", "fe", str(fe))

    data = req_import(
        yard,
        "AB-14",
        source="text",
        payload="# AB-14\n",
        branches={"backend": "feature/x", "frontend": "feature/x"},
        bases={"frontend": "main"},
        submit=False,
    )
    assert data["phase"] == "frozen"
    assert set(data["tickets"]) == {"T1", "T2"}
    assert all(s["state"] == "done" for s in data["tickets"].values())
    # backend uses the fork point; frontend pins the explicit base.
    assert data["base_shas"]["backend"] == be_fork
    assert data["base_shas"]["frontend"] == _git(fe, "rev-parse", "main")
    assert (paths.req_worktree(yard, "AB-14", "backend") / ".git").exists()
    assert (paths.req_worktree(yard, "AB-14", "frontend") / ".git").exists()


def test_req_import_force_warns_and_clears_contract_findings(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    repo, _feature_sha, _fork_sha = _local_repo_with_external_branch(tmp_path)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(repo), "main", "be", str(repo))
    req_import(
        yard,
        "AB-15",
        source="text",
        payload="# AB-15\n",
        branches={"backend": "feature/x"},
        submit=False,
    )
    cases = paths.req_dir(yard, "AB-15") / "qa" / "cases"
    cases.mkdir(parents=True)
    (cases / "case-01.md").write_text("---\ntitle: t\n---\nbody\n", encoding="utf-8")
    data = st.load(yard, "AB-15")
    data["contract_findings"] = [{"id": "F1", "title": "stale"}]
    st.save(yard, "AB-15", data)

    logs: list[str] = []
    out = req_import(
        yard,
        "AB-15",
        source="text",
        payload="# AB-15\n",
        branches={"backend": "feature/x"},
        submit=False,
        force=True,
        on_progress=logs.append,
    )
    assert any("已有 QA 用例" in line for line in logs)
    assert "contract_findings" not in out
