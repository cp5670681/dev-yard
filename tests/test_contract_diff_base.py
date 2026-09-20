from pathlib import Path

from dev_yard import gitops
from dev_yard import status as st
from dev_yard.bug_tickets import spawn_fix_tickets
from dev_yard.runners import Runner, RunResult
from dev_yard.service import init_yard, repo_add, req_freeze, req_open, review
from dev_yard.tickets import parse_tickets


class CapturingRunner(Runner):
    def __init__(self, ok: bool = True, summary: str = "ok") -> None:
        self.ok = ok
        self.summary = summary
        self.calls: list[tuple[str, Path, list[Path]]] = []

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.calls.append((prompt, cwd, list(extra_read_paths)))
        return RunResult(ok=self.ok, summary=self.summary)


def _freeze_one_repo(tmp_path: Path, git_src: Path, key: str) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, key)
    return yard


def _commit(path: Path, name: str, text: str, message: str) -> None:
    (path / name).write_text(text, encoding="utf-8")
    gitops.run(["git", "add", "."], cwd=path)
    gitops.run(["git", "commit", "-q", "-m", message], cwd=path)


def test_freeze_records_base_sha(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _freeze_one_repo(tmp_path, git_src, "AB-70")
    data = st.load(yard, "AB-70")
    recorded = data["base_shas"]["backend"]
    assert recorded == gitops.run(["git", "rev-parse", "main"], cwd=git_src)


def test_contract_diff_ignores_upstream_drift(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _freeze_one_repo(tmp_path, git_src, "AB-71")

    # Upstream lands on main after this requirement froze.
    _commit(git_src, "UPSTREAM", "drift", "upstream drift")

    r = CapturingRunner()
    review(yard, "AB-71", None, contract=True, runner=r)
    prompt = r.calls[0][0]
    assert "diff vs freeze point" in prompt
    assert "UPSTREAM" not in prompt
    assert "scope creep" in prompt
    assert "files: [" in prompt

    # Without a recorded sha the fork point is used, which also excludes drift.
    data = st.load(yard, "AB-71")
    data.pop("base_shas", None)
    st.save(yard, "AB-71", data)
    r2 = CapturingRunner()
    review(yard, "AB-71", None, contract=True, runner=r2)
    assert "UPSTREAM" not in r2.calls[0][0]


def test_spawn_drops_findings_not_on_requirement(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    _commit(git_src, "untouched.rb", "existing\n", "seed file before freeze")
    yard = _freeze_one_repo(tmp_path, git_src, "AB-72")

    data = st.load(yard, "AB-72")
    wt = Path(data["tickets"]["T1"]["worktree"])
    _commit(wt, "README", "x\ntouched\n", "T1 work")

    data["contract_review"] = "failed"
    data["contract_findings"] = [
        {"id": "F1", "title": "keep changed", "repo": "backend", "files": ["README"]},
        {
            "id": "F2",
            "title": "drop drift",
            "repo": "backend",
            "files": ["untouched.rb"],
        },
        {
            "id": "F3",
            "title": "keep missing",
            "repo": "backend",
            "files": ["missing_new.rb"],
        },
    ]
    st.save(yard, "AB-72", data)

    created = spawn_fix_tickets(yard, "AB-72", "contract")
    assert created == ["B1", "B2"]
    tix = {t.id: t for t in parse_tickets((yard / "reqs" / "AB-72" / "TICKETS.md").read_text())}
    titles = {t.title for t in tix.values() if t.id.startswith("B")}
    assert titles == {"keep changed", "keep missing"}
