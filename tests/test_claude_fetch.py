from pathlib import Path

from dev_yard.claude_fetch import claude_argv, fetch_prompt


def test_claude_argv_uses_print_and_mcp_friendly_flags(tmp_path: Path):
    dest = tmp_path / "PG-1"
    dest.mkdir()
    argv = claude_argv(dest, binary="claude")
    assert argv[0] == "claude"
    assert argv[-1] == "-p"
    assert "--add-dir" in argv
    assert str(dest) in argv
    assert "bypassPermissions" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert fetch_prompt("PG-1", dest) not in argv


def test_prompt_forbids_historical_crawl():
    p = fetch_prompt("PG-9", Path("/tmp/x"))
    assert "Do **not** dump parent/historical wiki" in p
    assert "jira_get_issue" in p
