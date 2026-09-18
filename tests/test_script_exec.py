from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_yard.exec_cfg import parse_exec
from dev_yard.qa_config import TestRejected, load_qa_config, save_qa_config
from dev_yard.script_exec import (
    ExecErrorClass,
    ExecResult,
    ExecUnreachable,
    HELLO,
    JmsK8sExecutor,
    check_env,
    classify_error,
    resolve_executor,
)
from dev_yard.service import init_yard


def _yard(tmp_path: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n",
        encoding="utf-8",
    )
    return yard


def _write_env(root: Path, env: dict, *, name: str = "local", extra: str = "") -> None:
    doc = {
        "active_env": name,
        "browser": {"channel": "chrome", "headed": False},
        "workers": [{"id": "a", "provider": "rcc", "model": "grok-4", "concurrency": 1}],
        "envs": {name: env},
    }
    (root / "qa.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_script_runner_sugar_is_local(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://127.0.0.1:8080",
            "script": {"runner": "bin/rails runner"},
        },
    )
    cfg = load_qa_config(root)
    assert cfg.env.exec_cfg.use == "local"
    assert cfg.env.exec_cfg.runner == "bin/rails runner"
    assert cfg.env.exec_cfg.site == "local"


def test_remote_url_rejects_local_exec(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://research.dev1.example.com",
            "script": {"runner": "bin/rails runner"},
        },
        name="test",
    )
    cfg = load_qa_config(root)
    with pytest.raises(TestRejected, match="远程 base_url"):
        resolve_executor(cfg.env, base_url=cfg.env.base_url)


def test_allow_cross_site_permits_mismatch(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://research.dev1.example.com",
            "script": {"runner": "bin/rails runner"},
            "exec": {"use": "local", "allow_cross_site": True},
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url, worktree=tmp_path)
    assert ex.use == "local"


def test_raw_rejects_script_placeholder():
    with pytest.raises(TestRejected, match=r"\{script\}"):
        parse_exec(
            "test",
            {
                "exec": {
                    "use": "raw",
                    "run": ["sh", "-c", "cat {script}"],
                    "ping": ["echo", "ok"],
                }
            },
            script_runner="",
        )


def test_raw_rejects_redirect_in_shell():
    with pytest.raises(TestRejected, match="重定向"):
        parse_exec(
            "test",
            {
                "exec": {
                    "use": "raw",
                    "shell": True,
                    "run": "cli exec < foo.rb",
                    "ping": "echo ok",
                }
            },
            script_runner="",
        )


def test_raw_missing_env_var(tmp_path: Path, monkeypatch):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://example.com",
            "exec": {
                "use": "raw",
                "run": ["echo", "ok"],
                "ping": ["echo", "{env:YARD_MISSING}"],
            },
        },
        name="test",
    )
    monkeypatch.delenv("YARD_MISSING", raising=False)
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    with pytest.raises(TestRejected, match="YARD_MISSING"):
        ex.ping()


def test_inconsistent_runner_is_config_error():
    with pytest.raises(TestRejected, match="不一致"):
        parse_exec(
            "local",
            {
                "script": {"runner": "bin/rails runner"},
                "exec": {"use": "local", "with": {"runner": "python"}},
            },
            script_runner="bin/rails runner",
        )


def test_delegate_requires_xor():
    with pytest.raises(TestRejected, match="只配 command 或只配 skill"):
        parse_exec(
            "t",
            {"exec": {"use": "delegate", "command": ["x"], "skill": "k8s"}},
            script_runner="",
        )


def test_jms_k8s_argv_selects_pod_with_jsonpath(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://research.dev1.example.com",
            "exec": {
                "use": "jms-k8s",
                "with": {
                    "jms": {"host": "jms.example.com", "port": 22222, "user": "alice@root"},
                    "default_node": "k8s-1",
                    "nodes": {"k8s-1": "10.0.1.5"},
                    "namespace": "research",
                    "container": "web",
                    "runner": "bin/rails runner",
                    "pod": {"selector": "app=research-web"},
                },
            },
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    assert isinstance(ex, JmsK8sExecutor)
    remotes: list[str] = []

    def fake_ssh(self, remote, *, stdin=None, timeout=30, on_log=None):
        remotes.append(remote)
        if "jsonpath" in remote:
            assert "--field-selector=status.phase=Running" in remote
            assert "-l" in remote
            assert "grep" not in remote
            return ExecResult(0, "research-abc", "")
        return ExecResult(0, "ok\n", "")

    ex._ssh = fake_ssh.__get__(ex, JmsK8sExecutor)  # type: ignore[method-assign]
    ex.ping()
    assert any("kubectl exec" in r and "echo ok" in r for r in remotes)
    assert any("-c" in r and "web" in r for r in remotes)
    ident = "alice@root@10.0.1.5@jms.example.com"
    assert ident in ex._mux.identity
    script = tmp_path / "seed.rb"
    script.write_text("puts 1\n", encoding="utf-8")
    remotes.clear()
    captured: dict[str, bytes | None] = {}

    def fake_run(self, remote, *, stdin=None, timeout=30, on_log=None):
        remotes.append(remote)
        captured["stdin"] = stdin
        return ExecResult(0, "1\n", "")

    ex._ssh = fake_run.__get__(ex, JmsK8sExecutor)  # type: ignore[method-assign]
    result = ex.run(
        script,
        env_extra={
            "QA_ENV": "test",
            "QA_JIRA": "PG-1",
            "QA_CASE_ID": "case-01",
            "QA_SCRIPT_KIND": "setup",
        },
    )
    assert result.code == 0
    assert captured["stdin"] == b"puts 1\n"
    exec_line = [r for r in remotes if "kubectl exec -i" in r][0]
    assert "-c" in exec_line
    assert "bin/rails" in exec_line
    assert "grep" not in exec_line
    assert "--env=QA_CASE_ID=case-01" in exec_line or "QA_CASE_ID=case-01" in exec_line
    assert "QA_JIRA=PG-1" in exec_line
    ex.close()


def test_docker_loopback_is_same_site(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://127.0.0.1:3000",
            "exec": {
                "use": "docker",
                "with": {"container": "app", "runner": "python3"},
            },
        },
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    assert ex.use == "docker"
    assert ex.cross_site_warning == ""
    captured: list[list[str]] = []

    def fake_proc(argv, **kwargs):
        captured.append(list(argv))
        return ExecResult(0, "ok\n", "")

    import dev_yard.script_exec as se

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(se, "_run_proc", lambda argv, **kwargs: fake_proc(argv, **kwargs))
    script = tmp_path / "s.py"
    script.write_text("print(1)\n", encoding="utf-8")
    ex.run(
        script,
        env_extra={"QA_ENV": "local", "QA_JIRA": "J", "QA_CASE_ID": "c1", "QA_SCRIPT_KIND": "setup"},
    )
    monkeypatch.undo()
    run_argv = captured[-1]
    assert run_argv[:3] == ["docker", "exec", "-i"]
    assert "-e" in run_argv
    assert "QA_CASE_ID=c1" in run_argv
    ex.close()


def test_allow_cross_site_warns(tmp_path: Path):
    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://research.dev1.example.com",
            "script": {"runner": "bin/rails runner"},
            "exec": {"use": "local", "allow_cross_site": True},
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url, worktree=tmp_path)
    assert "allow_cross_site" in ex.cross_site_warning
    assert "research.dev1.example.com" in ex.cross_site_warning
    ex.close()


def test_local_runner_requires_db_url(tmp_path: Path):
    root = _yard(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    _write_env(
        root,
        {"base_url": "http://127.0.0.1:9", "script": {"runner": "python3"}},
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url, worktree=wt)
    script = tmp_path / "hello.py"
    script.write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(TestRejected, match="db.url"):
        ex.run(script)
    ex.close()


def test_ssh_ping_includes_stderr(tmp_path: Path):
    from dev_yard.script_exec import SshExecutor

    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://example.com",
            "exec": {"use": "ssh", "with": {"target": "me@host", "runner": "python3"}},
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    assert isinstance(ex, SshExecutor)

    def fake_remote(self, remote, *, stdin=None, timeout=30, on_log=None):
        return ExecResult(1, "", "Connection refused")

    ex._remote = fake_remote.__get__(ex, SshExecutor)  # type: ignore[method-assign]
    with pytest.raises(ExecUnreachable, match="Connection refused"):
        ex.ping()
    ex.close()


def test_classify_timeout_and_auth():
    assert classify_error("", timeout=True) == ExecErrorClass.TIMEOUT
    assert classify_error("Permission denied (publickey)") == ExecErrorClass.AUTH
    assert classify_error("abort: no seed", code=1) == ExecErrorClass.SCRIPT


def test_local_stdin_hello(tmp_path: Path):
    root = _yard(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    _write_env(
        root,
        {
            "base_url": "http://127.0.0.1:9",
            "script": {"runner": "python3"},
            "db": {"url": "postgres://localhost/app"},
        },
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url, worktree=wt)
    script = tmp_path / "hello.py"
    script.write_text(f'print("{HELLO}")\n', encoding="utf-8")
    result = ex.run(script)
    assert result.code == 0
    assert HELLO in result.stdout
    ex.close()


def test_check_env_local(tmp_path: Path):
    root = _yard(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    _write_env(
        root,
        {
            "base_url": "http://127.0.0.1:9",
            "script": {"runner": "python3"},
            "db": {"url": "postgres://localhost/app"},
        },
    )
    out = check_env(root, worktree=wt)
    assert out["ok"] is True
    assert out["use"] == "local"
    assert any(s["step"] == "hello" and s["status"] == "ok" for s in out["steps"])


def test_delegate_command_reads_result_file(tmp_path: Path, monkeypatch):
    root = _yard(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "company-qa-exec"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if '--ping' in args:\n"
        "    sys.exit(0)\n"
        "result = args[args.index('--result') + 1]\n"
        "Path(result).write_text(json.dumps("
        '{"exit_code": 0, "stdout": "seed-1", "stderr": ""}'
        "), encoding='utf-8')\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")
    _write_env(
        root,
        {
            "base_url": "http://example.com",
            "exec": {
                "use": "delegate",
                "command": [str(stub)],
                "ping": [str(stub), "--ping"],
            },
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    ex.ping()
    script = tmp_path / "s.rb"
    script.write_text("puts 1\n", encoding="utf-8")
    result = ex.run(script)
    assert result.code == 0
    assert result.stdout == "seed-1"
    ex.close()


def test_delegate_missing_result_file(tmp_path: Path, monkeypatch):
    root = _yard(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "bad-exec"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    _write_env(
        root,
        {
            "base_url": "http://example.com",
            "exec": {
                "use": "delegate",
                "command": [str(stub)],
                "ping": [str(stub)],
            },
        },
        name="test",
    )
    cfg = load_qa_config(root)
    ex = resolve_executor(cfg.env, base_url=cfg.env.base_url)
    script = tmp_path / "s.rb"
    script.write_text("puts 1\n", encoding="utf-8")
    result = ex.run(script)
    assert result.code != 0
    assert "exec-result.json" in result.stderr
    ex.close()


def test_cli_check_env(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from dev_yard.cli import app

    root = _yard(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    _write_env(
        root,
        {"base_url": "http://127.0.0.1:9", "script": {"runner": "python3"}},
    )
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "dev_yard.script_exec.check_env",
        lambda *a, **k: {
            "ok": True,
            "env": "local",
            "use": "local",
            "site": "local",
            "steps": [{"step": "ping", "status": "ok", "detail": ""}],
        },
    )
    out = CliRunner().invoke(app, ["qa", "check-env", "--env", "local"])
    assert out.exit_code == 0, out.output
    assert "ok env=local" in out.output


def test_payload_roundtrip_keeps_exec(tmp_path: Path):
    from dev_yard.qa_config import qa_payload

    root = _yard(tmp_path)
    _write_env(
        root,
        {
            "base_url": "http://example.com",
            "exec": {
                "use": "ssh",
                "with": {"target": "me@host", "runner": "python3"},
            },
        },
        name="test",
    )
    payload = qa_payload(root)
    assert payload["envs"]["test"]["exec"]["use"] == "ssh"
    assert payload["envs"]["test"]["exec"]["target"] == "me@host"
    save_qa_config(root, payload)
    cfg = load_qa_config(root)
    assert cfg.env.exec_cfg.use == "ssh"
    assert cfg.env.exec_cfg.ssh_target == "me@host"
