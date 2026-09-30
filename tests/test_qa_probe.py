from __future__ import annotations

from pathlib import Path

from dev_yard.qa_config import TestRejected
from dev_yard.qa_probe import run_probes


def test_run_probes_writes_results(tmp_path: Path, monkeypatch):
    qa = tmp_path / "qa"
    (qa / "probes").mkdir(parents=True)
    (qa / "probes" / "dept.sql").write_text(
        "-- probe: main\nSELECT id FROM departments WHERE id = 22\n",
        encoding="utf-8",
    )

    class _Env:
        db_default = "main"

    class _Cfg:
        env = _Env()

    monkeypatch.setattr(
        "dev_yard.qa_probe.run_sql_lines",
        lambda cfg, sql, on_log=None, catalog=None: [],
    )
    path = run_probes(qa, _Cfg())  # type: ignore[arg-type]
    text = path.read_text(encoding="utf-8")
    assert "dept.sql" in text
    assert "status: ok" in text
    assert "catalog: main" in text


def test_run_probes_rejects_write_sql(tmp_path: Path):
    qa = tmp_path / "qa"
    (qa / "probes").mkdir(parents=True)
    (qa / "probes" / "bad.sql").write_text("DELETE FROM widgets\n", encoding="utf-8")

    class _Env:
        db_default = "main"

    class _Cfg:
        env = _Env()

    path = run_probes(qa, _Cfg())  # type: ignore[arg-type]
    assert "failed" in path.read_text(encoding="utf-8")


def test_run_probes_records_query_error(tmp_path: Path, monkeypatch):
    qa = tmp_path / "qa"
    (qa / "probes").mkdir(parents=True)
    (qa / "probes" / "x.sql").write_text("SELECT 1\n", encoding="utf-8")

    class _Env:
        db_default = "main"

    class _Cfg:
        env = _Env()

    def boom(*_a, **_k):
        raise TestRejected("ORA-00933")

    monkeypatch.setattr("dev_yard.qa_probe.run_sql_lines", boom)
    path = run_probes(qa, _Cfg())  # type: ignore[arg-type]
    assert "ORA-00933" in path.read_text(encoding="utf-8")
