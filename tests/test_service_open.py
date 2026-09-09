from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.runners import RunResult, Runner
from dev_yard.service import REQ_SKELETON, extract_req_key, init_yard, req_open, status_text


class _OkEmpty(Runner):
    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
        return RunResult(ok=True, summary="pi exit 0", exit_code=0)


class _OkWrites(Runner):
    def __init__(self, dest: Path, jira: str) -> None:
        self.dest = dest
        self.jira = jira

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
        (self.dest / "REQUIREMENT.md").write_text(f"# {self.jira}\n\nfrom pi\n")
        return RunResult(ok=True, summary="fetched", exit_code=0)


def test_init_and_open(tmp_path: Path, monkeypatch):
    for k in (
        "JIRA_BASE_URL",
        "JIRA_URL",
        "JIRA_USERNAME",
        "JIRA_USER",
        "JIRA_EMAIL",
        "JIRA_PASSWORD",
        "JIRA_TOKEN",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)
    init_yard(tmp_path)
    assert (tmp_path / "repos.yaml").exists()
    gi = (tmp_path / ".gitignore").read_text().splitlines()
    assert "repos.yaml" in gi
    d, warning = req_open(tmp_path, "ABC-1", source="none")
    assert warning
    assert (d / "REQUIREMENT.md").exists()
    assert (d / "GRILL.md").exists()
    assert (d / "SPEC.md").exists()
    assert (d / "TICKETS.md").exists()
    assert "## T1" not in (d / "TICKETS.md").read_text()


def test_open_rejects_reserved_docs(tmp_path: Path):
    init_yard(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        req_open(tmp_path, "docs", source="none")
    with pytest.raises(ValueError, match="reserved"):
        req_open(tmp_path, "DOCS", source="none")
    adr = tmp_path / "reqs" / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001.md").write_text("# adr\n")
    text = status_text(tmp_path, None)
    assert "docs" not in text
    assert text == "(no requirements)"


def test_dry_run_does_not_write(tmp_path: Path):
    init_yard(tmp_path)
    d, warning = req_open(tmp_path, "ABC-2", source="pi", dry_run=True)
    assert "pi" in warning
    assert "-p" in warning
    assert "mcp" in warning
    assert not d.exists()
    assert not (tmp_path / "reqs" / "ABC-2" / "STATUS.yaml").exists()


def test_http_dry_run_does_not_write(tmp_path: Path):
    init_yard(tmp_path)
    d, warning = req_open(tmp_path, "ABC-3", source="http", dry_run=True)
    assert "no request" in warning
    assert not d.exists()


def test_reopen_refuses_later_phase(tmp_path: Path, monkeypatch):
    for k in ("JIRA_BASE_URL", "JIRA_URL"):
        monkeypatch.delenv(k, raising=False)
    init_yard(tmp_path)
    d, _ = req_open(tmp_path, "ABC-4", source="none")
    data = st.load(tmp_path, "ABC-4")
    data["phase"] = "frozen"
    st.save(tmp_path, "ABC-4", data)
    with pytest.raises(ValueError, match="phase=frozen"):
        req_open(tmp_path, "ABC-4", source="none")
    req_open(tmp_path, "ABC-4", source="none", force=True)
    assert st.load(tmp_path, "ABC-4")["phase"] == "open"


def test_pi_ok_without_requirement_raises(tmp_path: Path):
    init_yard(tmp_path)
    with pytest.raises(RuntimeError, match="did not write REQUIREMENT.md"):
        req_open(tmp_path, "ABC-6", source="pi", runner=_OkEmpty())
    assert (tmp_path / "reqs" / "ABC-6" / "REQUIREMENT.md").exists()


def test_pi_leaving_skeleton_raises(tmp_path: Path):
    init_yard(tmp_path)
    d = tmp_path / "reqs" / "ABC-7"
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text(REQ_SKELETON.format(key="ABC-7", title="ABC-7", body=""))
    with pytest.raises(RuntimeError, match="did not write REQUIREMENT.md"):
        req_open(tmp_path, "ABC-7", source="pi", runner=_OkEmpty())


def test_pi_missing_does_not_delete_assets(tmp_path: Path, monkeypatch):
    init_yard(tmp_path)
    d = tmp_path / "reqs" / "ABC-5"
    assets = d / "assets"
    assets.mkdir(parents=True)
    (assets / "shot.png").write_text("img")
    monkeypatch.setenv("YARD_PI", "/definitely/missing-pi")
    with pytest.raises(FileNotFoundError, match="pi not found"):
        req_open(tmp_path, "ABC-5", source="pi")
    assert (assets / "shot.png").exists()


def test_injected_missing_pi_runner_does_not_delete_assets(tmp_path: Path):
    init_yard(tmp_path)
    d = tmp_path / "reqs" / "ABC-5b"
    assets = d / "assets"
    assets.mkdir(parents=True)
    (assets / "shot.png").write_text("img")

    class Missing(Runner):
        def start(self, prompt, cwd, extra_read_paths):
            return RunResult(ok=False, summary="pi not found (`/nope`).", exit_code=127)

    with pytest.raises(RuntimeError, match="pi not found"):
        req_open(tmp_path, "ABC-5b", source="pi", runner=Missing())
    assert (assets / "shot.png").read_text() == "img"


def test_pi_fetch_writes_requirement(tmp_path: Path):
    init_yard(tmp_path)
    dest = tmp_path / "reqs" / "ABC-8"
    d, warning = req_open(
        tmp_path, "ABC-8", source="pi", runner=_OkWrites(dest, "ABC-8")
    )
    assert warning == ""
    assert "from pi" in (d / "REQUIREMENT.md").read_text()
    assert st.load(tmp_path, "ABC-8")["phase"] == "open"


def test_pi_open_restores_other_docs(tmp_path: Path):
    init_yard(tmp_path)
    d, _ = req_open(tmp_path, "ABC-9", source="none")
    grill_before = (d / "GRILL.md").read_text()

    class Hijack(Runner):
        def start(self, prompt, cwd, extra_read_paths):
            (d / "GRILL.md").write_text("# hijacked\n")
            (d / "REQUIREMENT.md").write_text("# ABC-9\n\nok\n")
            return RunResult(ok=True, summary="ok", exit_code=0)

    _, warning = req_open(tmp_path, "ABC-9", source="pi", force=True, runner=Hijack())
    assert (d / "GRILL.md").read_text() == grill_before
    assert "GRILL.md" in warning


def test_extract_req_key_various_targets():
    assert extract_req_key("PG-13090") == "PG-13090"
    assert extract_req_key("https://jira.corp.com/browse/PG-13090") == "PG-13090"
    assert extract_req_key("https://jira.corp.com/browse/PG-13090?filter=123") == "PG-13090"
    assert extract_req_key("https://github.com/my-org/my-service/issues/42") == "my-service-42"
    assert extract_req_key("https://gitlab.com/group/my-be/-/issues/99") == "my-be-99"
    assert extract_req_key("https://example.feishu.cn/docx/doxcn123456789") == "FEISHU-doxcn1234567"
    assert extract_req_key("https://example.com/prd/payment-v2") == "payment-v2"
    assert extract_req_key("custom-feat_01.v2") == "custom-feat_01.v2"


def test_req_open_with_text_payload(tmp_path: Path):
    init_yard(tmp_path)
    d, warning = req_open(
        tmp_path,
        "TEXT-1",
        source="text",
        payload="# Custom Text\n\nDirect requirement content.\n",
    )
    assert warning == ""
    assert d == tmp_path / "reqs" / "TEXT-1"
    assert (d / "REQUIREMENT.md").read_text() == "# Custom Text\n\nDirect requirement content.\n"
    assert st.load(tmp_path, "TEXT-1")["phase"] == "open"


def test_req_open_with_file_payload(tmp_path: Path):
    init_yard(tmp_path)
    src_file = tmp_path / "imported_prd.md"
    src_file.write_text("# Imported PRD\n\nFrom local file.\n")
    d, warning = req_open(
        tmp_path,
        "FILE-1",
        source="file",
        payload=str(src_file),
    )
    assert warning == ""
    assert (d / "REQUIREMENT.md").read_text() == "# Imported PRD\n\nFrom local file.\n"
    assert st.load(tmp_path, "FILE-1")["phase"] == "open"


def test_req_open_with_missing_file_raises(tmp_path: Path):
    init_yard(tmp_path)
    with pytest.raises(FileNotFoundError, match="Source file not found"):
        req_open(
            tmp_path,
            "FILE-ERR",
            source="file",
            payload=str(tmp_path / "nonexistent.md"),
        )


def test_req_open_url_auto_key_with_pi(tmp_path: Path):
    init_yard(tmp_path)
    dest = tmp_path / "reqs" / "PG-13090"

    class CapturingRunner(Runner):
        def __init__(self) -> None:
            self.captured_prompt = ""

        def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
            self.captured_prompt = prompt
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "REQUIREMENT.md").write_text("# PG-13090\n\nExtracted from URL.\n")
            return RunResult(ok=True, summary="ok", exit_code=0)

    runner = CapturingRunner()
    d, warning = req_open(
        tmp_path,
        "https://jira.example.com/browse/PG-13090",
        source="pi",
        runner=runner,
    )
    assert d == dest
    assert "https://jira.example.com/browse/PG-13090" in runner.captured_prompt
    assert (d / "REQUIREMENT.md").read_text() == "# PG-13090\n\nExtracted from URL.\n"

