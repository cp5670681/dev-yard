"""`dev-yard qa sql` / `qa exec`: the channels a worker uses to self-serve data.

The worker may not hold a DSN or an ssh recipe, so it asks the host to run one
read-only statement or one script. These tests pin that contract: read-only is
enforced, output is capped (a query result becomes prompt text), the DSN stays
host-side, and the script goes through the same exec recipe (and the same per-env
lock) as a host-run seed.
"""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from dev_yard.cli import app
from dev_yard.service import init_yard, req_open

runner = CliRunner()


def _yard(tmp_path: Path, monkeypatch) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    req_open(yard, "QA-SQL", source="none")
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    db:\n      url: postgres://u:p@127.0.0.1:5432/qa\n"
        "    script:\n      runner: ruby\n",
        encoding="utf-8",
    )
    (yard / "repos.yaml").write_text(
        "repos:\n"
        "  backend:\n"
        "    url: git@example.com:be.git\n"
        "    default_base: main\n"
        "    role: svc\n",
        encoding="utf-8",
    )
    return yard


def test_qa_sql_prints_rows(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "42|alpha\n43|beta\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr("dev_yard.qa_exec._run", fake_run)
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    out = runner.invoke(app, ["qa", "sql", "QA-SQL", "-c", "SELECT id, name FROM t"])
    assert out.exit_code == 0, out.output
    assert "42|alpha" in out.stdout
    assert "43|beta" in out.stdout
    cmd = seen["cmd"]
    assert cmd[0] == "/usr/bin/usql"
    assert "-t" in cmd and "-A" in cmd
    # The DSN is passed to usql by the host; it never reaches the worker.
    assert any("postgres://" in str(part) for part in cmd)


def test_qa_sql_refuses_a_write(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)
    called: list[object] = []
    monkeypatch.setattr("dev_yard.qa_exec._run", lambda cmd, **kw: called.append(cmd))
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    out = runner.invoke(app, ["qa", "sql", "QA-SQL", "-c", "DELETE FROM t WHERE id = 1"])
    assert out.exit_code == 1
    assert "read-only" in out.output
    assert called == []


def test_qa_sql_from_file_and_json(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path, monkeypatch)
    sql = yard / "reqs" / "QA-SQL" / "q.sql"
    sql.parent.mkdir(parents=True, exist_ok=True)
    sql.write_text("SELECT id FROM t LIMIT 1\n", encoding="utf-8")

    class _Proc:
        returncode = 0
        stdout = '[{"id": 7}]\n'
        stderr = ""

    monkeypatch.setattr("dev_yard.qa_exec._run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    out = runner.invoke(app, ["qa", "sql", "QA-SQL", "-f", str(sql), "--json"])
    assert out.exit_code == 0, out.output
    assert '"id": 7' in out.stdout


def test_qa_sql_requires_sql_or_file(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)
    out = runner.invoke(app, ["qa", "sql", "QA-SQL"])
    assert out.exit_code == 1
    assert "-c" in out.output


def test_qa_exec_runs_the_script_through_the_recipe(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path, monkeypatch)
    cases = yard / "reqs" / "QA-SQL" / "qa" / "cases" / "mod"
    cases.mkdir(parents=True, exist_ok=True)
    (cases / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
        encoding="utf-8",
    )
    script = cases / "setup.rb"
    script.write_text("puts 'QA_SEED key=a id=1'\n", encoding="utf-8")

    captured: dict[str, object] = {}

    class _Result:
        code = 0
        stdout = "QA_SEED key=a id=1\n"
        stderr = ""
        error_class = None

    class _Executor:
        site = "local"
        label = "local runner"

        def run(self, path, on_log=None, env_extra=None):
            captured["script"] = path
            captured["env"] = dict(env_extra or {})
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(
        "dev_yard.script_exec.resolve_executor", lambda *a, **k: _Executor()
    )

    out = runner.invoke(
        app,
        [
            "qa",
            "exec",
            "QA-SQL",
            "mod/setup.rb",
            "--case",
            "case-01",
            "--kind",
            "setup",
            "--repo",
            "backend",
        ],
    )
    assert out.exit_code == 0, out.output
    assert "QA_SEED key=a id=1" in out.stdout
    assert captured["script"] == script.resolve()
    env = captured["env"]
    assert env["QA_JIRA"] == "QA-SQL"
    assert env["QA_CASE_ID"] == "case-01"
    assert env["QA_SCRIPT_KIND"] == "setup"
    # A data write stays attributable: one line in qa/exec-log.md.
    log = yard / "reqs" / "QA-SQL" / "qa" / "exec-log.md"
    assert "case=case-01" in log.read_text(encoding="utf-8")
    assert "kind=setup" in log.read_text(encoding="utf-8")


def test_qa_exec_reports_a_missing_script(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", "nope.rb"])
    assert out.exit_code == 1
    assert "找不到脚本" in out.output


def test_qa_exec_reports_a_failing_script(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path, monkeypatch)
    script = yard / "reqs" / "QA-SQL" / "qa" / "cases" / "boom.rb"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("exit 1\n", encoding="utf-8")

    class _Result:
        code = 1
        stdout = ""
        stderr = "boom"
        error_class = None

    class _Executor:
        site = "local"
        label = "local runner"

        def run(self, path, on_log=None, env_extra=None):
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(
        "dev_yard.script_exec.resolve_executor", lambda *a, **k: _Executor()
    )
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", str(script), "--kind", "setup"])
    assert out.exit_code == 1
    assert "boom" in out.output


def test_qa_exec_rejects_unknown_case(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path, monkeypatch)
    script = yard / "reqs" / "QA-SQL" / "qa" / "cases" / "s.rb"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("puts 1\n", encoding="utf-8")
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", str(script), "--case", "case-99"])
    assert out.exit_code == 1
    assert "case-99" in out.output


def test_qa_exec_refuses_a_script_outside_the_requirement(tmp_path: Path, monkeypatch):
    """Only this requirement's own qa/ scripts may run (no product-repo scripts)."""
    yard = _yard(tmp_path, monkeypatch)
    outside = yard / "scripts" / "danger.rb"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("puts 1\n", encoding="utf-8")
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", str(outside)])
    assert out.exit_code == 1
    assert "qa/ 目录内" in out.output


def test_qa_exec_resolves_a_path_from_the_yard_root(tmp_path: Path, monkeypatch):
    """The worker's cwd may be a worktree; `qa/cases/...` must still resolve."""
    yard = _yard(tmp_path, monkeypatch)
    script = yard / "reqs" / "QA-SQL" / "qa" / "cases" / "seed.rb"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("puts 1\n", encoding="utf-8")

    class _Result:
        code = 0
        stdout = "ok\n"
        stderr = ""
        error_class = None

    class _Executor:
        site = "local"
        label = "local runner"

        def run(self, path, on_log=None, env_extra=None):
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(
        "dev_yard.script_exec.resolve_executor", lambda *a, **k: _Executor()
    )
    # A worker's cwd is often a freeze worktree; the yard root is two levels up.
    worktree = yard / "reqs" / "QA-SQL" / "worktrees" / "backend"
    worktree.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(worktree)
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", "qa/cases/seed.rb", "--repo", "backend"])
    assert out.exit_code == 0, out.output
    assert "ok" in out.stdout


def test_qa_sql_routes_to_the_named_catalog(tmp_path: Path, monkeypatch):
    """`--db` must reach the catalog's DSN, not the default one."""
    yard = _yard(tmp_path, monkeypatch)
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    db:\n      default: main\n      catalogs:\n"
        "        main:\n          url: postgres://u:p@127.0.0.1:5432/main\n"
        "        published:\n          url: postgres://u:p@127.0.0.1:5432/pub\n",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "1\n"
        stderr = ""

    monkeypatch.setattr(
        "dev_yard.qa_exec._run",
        lambda cmd, **kw: (seen.__setitem__("cmd", cmd), _Proc())[1],
    )
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    out = runner.invoke(
        app, ["qa", "sql", "QA-SQL", "-c", "SELECT 1", "--db", "published"]
    )
    assert out.exit_code == 0, out.output
    assert any("/pub" in str(part) for part in seen["cmd"])


def test_qa_sql_empty_result_and_limit(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)

    class _Proc:
        returncode = 0
        stdout = "1\n2\n3\n"
        stderr = ""

    monkeypatch.setattr("dev_yard.qa_exec._run", lambda cmd, **kw: _Proc())
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    capped = runner.invoke(app, ["qa", "sql", "QA-SQL", "-c", "SELECT 1", "--limit", "2"])
    assert capped.exit_code == 0, capped.output
    assert "3" not in capped.stdout.splitlines()

    class _Empty:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("dev_yard.qa_exec._run", lambda cmd, **kw: _Empty())
    empty = runner.invoke(app, ["qa", "sql", "QA-SQL", "-c", "SELECT 1"])
    assert empty.exit_code == 0, empty.output
    assert "(0 rows)" in empty.stdout


def test_qa_sql_caps_at_the_database_not_just_the_printer(tmp_path: Path, monkeypatch):
    """A whole-table dump must not reach the host or the worker's prompt."""
    _yard(tmp_path, monkeypatch)
    seen: dict[str, object] = {}
    huge = "x" * 5000

    class _Proc:
        returncode = 0
        stdout = "".join(f"{i}|{huge}\n" for i in range(300))
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr("dev_yard.qa_exec._run", fake_run)
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda _n: "/usr/bin/usql")

    out = runner.invoke(
        app, ["qa", "sql", "QA-SQL", "-c", "SELECT id, body FROM t", "--limit", "10"]
    )
    assert out.exit_code == 0, out.output
    # The LIMIT is in the statement the DB runs, not only in the printer.
    sql = str(seen["cmd"][-1])
    assert "LIMIT 10" in sql.upper()
    lines = out.stdout.splitlines()
    assert len([ln for ln in lines if "|" in ln]) == 10
    # Each line is truncated so one fat row cannot eat the window.
    assert len(max(lines, key=len)) <= 2000 + 8
    assert "capped at 10 rows" in out.output


def _hold_env_lock(yard: Path, env: str = "local") -> None:
    lock_dir = yard / ".yard-qa" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    # A live pid (this process) means the holder is not stale.
    (lock_dir / f"{env}.verify.lock").write_text(f"{os.getpid()}:test", encoding="utf-8")


def _script(yard: Path, name: str = "seed.rb") -> Path:
    script = yard / "reqs" / "QA-SQL" / "qa" / "cases" / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("puts 1\n", encoding="utf-8")
    return script


def _no_wait(yard: Path) -> None:
    qa_yaml = yard / "qa.yaml"
    qa_yaml.write_text(
        qa_yaml.read_text(encoding="utf-8") + "run:\n  env_wait_timeout: 0\n",
        encoding="utf-8",
    )


def test_qa_exec_refuses_to_write_while_another_run_holds_the_env(
    tmp_path: Path, monkeypatch
):
    """A design-phase script must not write the test DB behind a live run."""
    yard = _yard(tmp_path, monkeypatch)
    _no_wait(yard)
    script = _script(yard)
    _hold_env_lock(yard)
    ran: list[object] = []
    monkeypatch.setattr(
        "dev_yard.script_exec.resolve_executor", lambda *a, **k: ran.append(1)
    )
    monkeypatch.delenv("YARD_QA_ENV_LOCK", raising=False)

    out = runner.invoke(app, ["qa", "exec", "QA-SQL", str(script), "--kind", "setup"])
    assert out.exit_code == 1
    assert "another qa exec is running" in out.output
    assert ran == []


def test_qa_exec_skips_the_lock_inside_its_own_run(tmp_path: Path, monkeypatch):
    """The run's worker must not queue behind (or deadlock on) its parent's lock."""
    yard = _yard(tmp_path, monkeypatch)
    _no_wait(yard)
    script = _script(yard)
    _hold_env_lock(yard)
    monkeypatch.setenv("YARD_QA_ENV_LOCK", "local")

    class _Result:
        code = 0
        stdout = "ok\n"
        stderr = ""
        error_class = None

    class _Executor:
        site = "local"
        label = "local runner"

        def run(self, path, on_log=None, env_extra=None):
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(
        "dev_yard.script_exec.resolve_executor", lambda *a, **k: _Executor()
    )
    out = runner.invoke(app, ["qa", "exec", "QA-SQL", str(script), "--kind", "setup"])
    assert out.exit_code == 0, out.output
    assert "ok" in out.stdout
