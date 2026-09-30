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
