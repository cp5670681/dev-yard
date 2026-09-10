from pathlib import Path

from dev_yard.runners import DryRunRunner, RunResult
from dev_yard.service import init_yard, launch_skill, req_open


class HijackTickets(DryRunRunner):
    def __init__(self, req: Path) -> None:
        super().__init__()
        self.req = req

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path], repo=None) -> RunResult:
        (self.req / "TICKETS.md").write_text("# hijacked\n")
        (self.req / "SPEC.md").write_text("# hijacked spec\n")
        return super().start(prompt, cwd, extra_read_paths)


def test_grill_restores_tickets_and_spec(tmp_path: Path, monkeypatch):
    for k in ("JIRA_BASE_URL", "JIRA_URL", "JIRA_USERNAME", "JIRA_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    init_yard(tmp_path)
    d, _ = req_open(tmp_path, "AB-1", source="none")
    tickets_before = (d / "TICKETS.md").read_text()
    spec_before = (d / "SPEC.md").read_text()
    result = launch_skill(tmp_path, "grill", "AB-1", dry_run=False, runner=HijackTickets(d))
    assert (d / "TICKETS.md").read_text() == tickets_before
    assert (d / "SPEC.md").read_text() == spec_before
    assert "TICKETS.md" in result.summary
    assert "SPEC.md" in result.summary
