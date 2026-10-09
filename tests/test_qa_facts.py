from __future__ import annotations

from pathlib import Path

from dev_yard.qa_facts import lint_facts_blob, scan_constants


def test_scan_constants_reads_class_and_literal(tmp_path: Path, monkeypatch):
    from dev_yard import paths

    jira = "J-1"
    alias = "legacy"
    wt = tmp_path / "reqs" / jira / "worktrees" / alias / "app" / "models"
    wt.mkdir(parents=True)
    (wt / "widget.rb").write_text(
        "class Widget\n  ACTIVE = 1\nend\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "req_worktree", lambda *_a, **_k: wt.parents[1])
    names = scan_constants(tmp_path, jira, alias)
    assert "Widget" in names
    assert "ACTIVE" in names
    assert "Widget::ACTIVE" in names


def test_scan_department_constants_keeps_integer_ids(tmp_path: Path, monkeypatch):
    from dev_yard import paths
    from dev_yard.qa_facts import scan_department_constants

    jira = "J-1"
    alias = "legacy"
    wt = tmp_path / "reqs" / jira / "worktrees" / alias / "app" / "models"
    wt.mkdir(parents=True)
    (wt / "dept.rb").write_text(
        "class PointSalesSetting\n"
        "  CLIENT_DEPARTMENT_ID = 22\n"
        "  LABEL = 'x'\n"
        "end\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "req_worktree", lambda *_a, **_k: wt.parents[1])
    rows = scan_department_constants(tmp_path, jira, alias)
    assert rows == [{"name": "PointSalesSetting::CLIENT_DEPARTMENT_ID", "value": 22}]


def test_probe_department_ids_marks_missing(monkeypatch):
    from dev_yard.qa_facts import probe_department_ids

    monkeypatch.setattr(
        "dev_yard.qa_facts.run_sql_lines",
        lambda cfg, sql, catalog=None: ["3", "4"],
    )
    rows = probe_department_ids(
        None,
        "reach",
        [
            {"name": "Department::SALES", "value": 3},
            {"name": "PointSalesSetting::CLIENT_DEPARTMENT_ID", "value": 22},
        ],
    )
    by_name = {row["name"]: row["exists"] for row in rows}
    assert by_name["Department::SALES"] is True
    assert by_name["PointSalesSetting::CLIENT_DEPARTMENT_ID"] is False


def test_department_lookups_land_on_the_repo_catalog(tmp_path: Path, monkeypatch):
    from dev_yard.config import Repo
    from dev_yard.qa_facts import _attach_department_lookups

    seen: list[str | None] = []

    def fake_probe(cfg, catalog, constants):
        seen.append(catalog)
        return [
            {"name": row["name"], "value": row["value"], "table": "departments", "exists": False}
            for row in constants
        ]

    monkeypatch.setattr("dev_yard.qa_facts.probe_department_ids", fake_probe)
    monkeypatch.setattr(
        "dev_yard.qa_facts.scan_department_constants",
        lambda root, jira, alias: [{"name": "Department::SALES", "value": 3}]
        if alias == "reach"
        else [],
    )

    class Env:
        db_catalogs = (object(),)
        db_default = "main"

    class Cfg:
        env = Env()

    catalogs = {"reach": {"dialect": "oracle"}, "main": {"dialect": "postgres"}}
    repos = {"reach": Repo(alias="reach", url="x", databases=("reach",))}
    _attach_department_lookups(tmp_path, "J-1", Cfg(), ["reach", "web"], repos, catalogs)
    assert seen == ["reach"]
    assert catalogs["reach"]["id_lookups"][0]["exists"] is False
    assert "id_lookups" not in catalogs["main"]


def test_facts_prompt_block_inlines_yaml(tmp_path: Path):
    from dev_yard.qa_facts import facts_prompt_block

    qa = tmp_path / "qa"
    qa.mkdir()
    (qa / "facts.yaml").write_text(
        "catalogs:\n  main: {dialect: postgres, version: '16'}\n",
        encoding="utf-8",
    )
    text = facts_prompt_block(qa)
    assert "qa/facts.yaml" in text
    assert "postgres" in text
    assert "ROWNUM" in text


def test_lint_facts_blob_unknown_const_and_skip_freeze():
    msgs = lint_facts_blob(
        "EmployeesGroup::MAX\nWidget.new\n",
        ["Widget", "Widget::ACTIVE"],
        {"Widget"},
    )
    assert any("EmployeesGroup::MAX" in m for m in msgs)
    assert any("skip_freeze" in m for m in msgs)
    msgs2 = lint_facts_blob(
        "Widget::ACTIVE\nt = Widget.new\nt.skip_freeze = true\n",
        ["Widget", "Widget::ACTIVE"],
        {"Widget"},
    )
    assert msgs2 == []
