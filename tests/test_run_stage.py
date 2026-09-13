from pathlib import Path

import pytest
import yaml

from dev_yard import service, stages
from dev_yard.runners import RunResult, Runner


class FakeRunner(Runner):
    def __init__(self, ok: bool = True, summary: str = "did the thing") -> None:
        self.ok = ok
        self.summary = summary
        self.calls: list[tuple[str, Path, list[Path]]] = []

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.calls.append((prompt, cwd, list(extra_read_paths)))
        return RunResult(ok=self.ok, summary=self.summary)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    return tmp_path


def _req(root: Path, jira: str, phase: str = "open") -> Path:
    d = root / "reqs" / jira
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    (d / "STATUS.yaml").write_text(f"phase: {phase}\ntickets: {{}}\n", encoding="utf-8")
    return d


def _status(root: Path, jira: str) -> dict:
    return yaml.safe_load(
        (root / "reqs" / jira / "STATUS.yaml").read_text(encoding="utf-8")
    )


def test_phase_gate_blocks(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="open")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen",
    )
    with pytest.raises(ValueError, match="frozen"):
        service.run_stage(root, spec, "J-1", runner=FakeRunner())


def test_phase_gate_allows_and_records_run(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen", sets_phase="deployed",
    )
    r = FakeRunner()
    result = service.run_stage(root, spec, "J-1", runner=r)
    assert result.ok
    assert r.calls[0][1] == root  # cwd = yard root
    data = _status(root, "J-1")
    assert data["phase"] == "deployed"
    assert data["stage_runs"]["deploy"]["ok"] is True
    assert data["stage_runs"]["deploy"]["summary"] == "did the thing"
    assert data["stage_runs"]["deploy"]["at"]


def test_failure_records_run_keeps_phase(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen", sets_phase="deployed",
    )
    service.run_stage(root, spec, "J-1", runner=FakeRunner(ok=False, summary="boom"))
    data = _status(root, "J-1")
    assert data["phase"] == "frozen"
    assert data["stage_runs"]["deploy"]["ok"] is False
    assert data["stage_runs"]["deploy"]["summary"] == "boom"


def test_no_phase_gate_when_unset(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="open")
    spec = stages.StageSpec(name="scan", skill="scan", bundles=(), tools=("read",))
    result = service.run_stage(root, spec, "J-1", runner=FakeRunner())
    assert result.ok
    assert _status(root, "J-1")["phase"] == "open"


def test_protects_snapshot_restore(tmp_path):
    root = _workspace(tmp_path)
    d = _req(root, "J-1")
    (d / "SPEC.md").write_text("before\n", encoding="utf-8")

    class MutatingRunner(FakeRunner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (d / "SPEC.md").write_text("clobbered\n", encoding="utf-8")
            return super().start(prompt, cwd, extra_read_paths, repo)

    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        protects=("SPEC.md",),
    )
    result = service.run_stage(root, spec, "J-1", runner=MutatingRunner())
    assert "restored" in result.summary
    assert (d / "SPEC.md").read_text(encoding="utf-8") == "before\n"


def test_dry_run_no_status_write(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        sets_phase="deployed",
    )
    service.run_stage(root, spec, "J-1", dry_run=True)
    data = _status(root, "J-1")
    assert "stage_runs" not in data
    assert data["phase"] == "frozen"


def test_prompt_contains_guidance_and_req_dir(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1")
    spec = stages.StageSpec(
        name="scan", skill="scan", bundles=(), tools=("read",),
        guidance="只输出结论，不改文件。",
    )
    r = FakeRunner()
    service.run_stage(root, spec, "J-1", runner=r)
    prompt = r.calls[0][0]
    assert "只输出结论，不改文件。" in prompt
    assert "J-1" in prompt


def test_accepts_stage_name_lookup(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1")
    result = service.run_stage(root, "spec", "J-1", dry_run=True)
    assert result.ok


def test_missing_req_dir_raises(tmp_path):
    root = _workspace(tmp_path)
    spec = stages.StageSpec(name="scan", skill="scan", bundles=(), tools=("read",))
    with pytest.raises(FileNotFoundError, match="req open"):
        service.run_stage(root, spec, "J-9", runner=FakeRunner())
