from pathlib import Path

import pytest

from dev_yard.qa_config import TestRejected, load_qa_config
from dev_yard.qa_deploy import (
    branch_new_columns,
    frozen_models,
    missing_on_site,
    parse_migration_text,
    requirement_migrations,
    undeployed_columns,
)
from dev_yard.qa_ready import assert_ready, assess_ready
from dev_yard.qa_schedule import CaseJob


def test_parse_migration_add_and_create():
    added, removed = parse_migration_text(
        """
        class AddIsExistingContact < ActiveRecord::Migration
          def self.up
            add_column :firm_tasks, :is_existing_contact, :boolean
            create_table :widgets do |t|
              t.string :name
              t.timestamps
            end
            remove_column :firm_tasks, :legacy_flag
          end
        end
        """
    )
    assert "is_existing_contact" in added["firm_tasks"]
    assert "name" in added["widgets"]
    assert "created_at" not in added.get("widgets", set())
    assert "legacy_flag" in removed["firm_tasks"]


def test_branch_new_columns_replays_in_filename_order(tmp_path: Path):
    a = tmp_path / "20260101000000_create.rb"
    b = tmp_path / "20260102000000_drop.rb"
    a.write_text("add_column :firm_tasks, :is_existing_contact, :boolean\n", encoding="utf-8")
    b.write_text("remove_column :firm_tasks, :is_existing_contact\n", encoding="utf-8")
    assert branch_new_columns([b, a]) == {}
    assert branch_new_columns([a]) == {"firm_tasks": {"is_existing_contact"}}


def test_frozen_models_scans_include(tmp_path: Path):
    wt = tmp_path / "reqs" / "J-1" / "worktrees" / "reach"
    model = wt / "app" / "models"
    model.mkdir(parents=True)
    (model / "firm_task.rb").write_text(
        "class FirmTask < ActiveRecord::Base\n  include FreezeModelConcern\nend\n",
        encoding="utf-8",
    )
    (model / "freeze_model_concern.rb").write_text(
        "module FreezeModelConcern\nend\n", encoding="utf-8"
    )
    names = frozen_models(tmp_path, "J-1", "reach")
    assert names == {"FirmTask"}


def test_frozen_models_reads_non_utf8_ruby(tmp_path: Path):
    wt = tmp_path / "reqs" / "J-1" / "worktrees" / "legacy"
    model = wt / "app" / "models"
    other = wt / "lib"
    model.mkdir(parents=True)
    other.mkdir(parents=True)
    (model / "widget.rb").write_text(
        "class Widget < ActiveRecord::Base\n  include FreezeModelConcern\nend\n",
        encoding="utf-8",
    )
    other.joinpath("note.rb").write_bytes(b"# " + bytes([0xd7, 0xd4]) + b"\n")
    names = frozen_models(tmp_path, "J-1", "legacy")
    assert names == {"Widget"}


def test_branch_new_columns_reads_non_utf8_migration(tmp_path: Path):
    path = tmp_path / "20200101000000_add.rb"
    path.write_bytes(
        b"# " + bytes([0xd7, 0xd4]) + b"\n"
        b"add_column :widgets, :flag, :boolean\n"
    )
    assert branch_new_columns([path]) == {"widgets": {"flag"}}


def _git_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)


def test_requirement_migrations_lists_added_files(tmp_path: Path, monkeypatch):
    import subprocess

    from dev_yard import qa_deploy

    wt = tmp_path / "reqs" / "J-1" / "worktrees" / "reach"
    wt.mkdir(parents=True)
    _git_repo(wt)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=wt, text=True).strip()
    mig = wt / "db" / "migrate"
    mig.mkdir(parents=True)
    (mig / "20260925120000_add_is_existing_contact_to_firm_tasks.rb").write_text(
        "add_column :firm_tasks, :is_existing_contact, :boolean\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "col"], cwd=wt, check=True, capture_output=True)
    monkeypatch.setattr(qa_deploy, "_diff_base", lambda *_a, **_k: base)
    rels = requirement_migrations(tmp_path, "J-1", "reach")
    assert rels is not None
    assert any("add_is_existing_contact" in r for r in rels)


def test_missing_on_site_none_when_probe_fails(tmp_path: Path, monkeypatch):
    from dev_yard import qa_deploy

    monkeypatch.setattr(qa_deploy, "requirement_migrations", lambda *a, **k: ["db/migrate/x.rb"])
    monkeypatch.setattr(
        qa_deploy,
        "branch_new_columns",
        lambda *_a, **_k: {"firm_tasks": {"is_existing_contact"}},
    )
    monkeypatch.setattr(qa_deploy, "catalogs_for_alias", lambda *a, **k: ["reach"])

    def boom(*_a, **_k):
        raise TestRejected("no dsn")

    monkeypatch.setattr(qa_deploy, "run_sql_lines", boom)
    (tmp_path / "reqs" / "J-1" / "worktrees" / "reach").mkdir(parents=True)
    (tmp_path / "qa.yaml").write_text(
        "workers:\n  - {id: a, provider: rcc, model: m, concurrency: 1, priority: 1}\n"
        "envs:\n  local:\n    base_url: http://x\n    db: {url: postgres://localhost/app}\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(tmp_path)
    assert missing_on_site(tmp_path, "J-1", cfg, "reach") is None


def test_undeployed_columns_lists_when_probe_undetermined(tmp_path: Path, monkeypatch):
    from dev_yard import qa_deploy

    monkeypatch.setattr(
        qa_deploy, "requirement_migrations", lambda *a, **k: ["db/migrate/x.rb"]
    )
    monkeypatch.setattr(
        qa_deploy,
        "branch_new_columns",
        lambda *_a, **_k: {"firm_tasks": {"is_existing_contact"}},
    )
    monkeypatch.setattr(qa_deploy, "missing_on_site", lambda *_a, **_k: None)
    (tmp_path / "reqs" / "J-1" / "worktrees" / "reach").mkdir(parents=True)
    (tmp_path / "qa.yaml").write_text(
        "workers:\n  - {id: a, provider: rcc, model: m, concurrency: 1, priority: 1}\n"
        "envs:\n  local:\n    base_url: http://x\n    db: {url: postgres://localhost/app}\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(tmp_path)
    rows = undeployed_columns(tmp_path, "J-1", cfg, ["reach"])
    assert ("reach", "firm_tasks.is_existing_contact", "db/migrate/x.rb") in rows


def test_assert_ready_rejects_missing_branch_column(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr(
        "dev_yard.script_exec.check_env",
        lambda *_a, **_k: {
            "ok": True,
            "steps": [
                {"step": "ping", "status": "ok"},
                {"step": "hello", "status": "ok"},
                {"step": "db", "status": "ok"},
            ],
        },
    )
    monkeypatch.setattr(
        "dev_yard.qa_ready._account_step",
        lambda *_a, **_k: {"step": "accounts", "status": "ok", "detail": "default"},
    )
    monkeypatch.setattr(
        "dev_yard.qa_deploy.missing_on_site",
        lambda *_a, **_k: (
            ["db/migrate/20260925120000_add_is_existing_contact_to_firm_tasks.rb"],
            ["firm_tasks.is_existing_contact"],
        ),
    )
    monkeypatch.setattr("dev_yard.qa._involved_aliases", lambda *_a, **_k: ["reach"])
    root = tmp_path / "yard"
    root.mkdir()
    (root / "reqs" / "QA-1").mkdir(parents=True)
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "workers:\n  - {id: a, provider: rcc, model: m, concurrency: 1, priority: 1}\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:9\n    db: {url: postgres://localhost/app}\n"
        "    auth:\n      default: default\n      accounts:\n"
        "        default: {username: ada, password: secret}\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg, [CaseJob(id="c1", title="t", repo="reach")])
    assert report["ok"] is False
    deploy = next(s for s in report["steps"] if s["step"] == "deploy")
    assert deploy["status"] == "fail"
    assert "20260925120000" in deploy["detail"]
    with pytest.raises(TestRejected, match="缺需求分支迁移"):
        assert_ready(root, "QA-1", cfg)


def test_assert_ready_warns_when_migrations_unknown(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr(
        "dev_yard.script_exec.check_env",
        lambda *_a, **_k: {
            "ok": True,
            "steps": [
                {"step": "ping", "status": "ok"},
                {"step": "hello", "status": "ok"},
                {"step": "db", "status": "ok"},
            ],
        },
    )
    monkeypatch.setattr(
        "dev_yard.qa_ready._account_step",
        lambda *_a, **_k: {"step": "accounts", "status": "ok", "detail": "default"},
    )
    monkeypatch.setattr("dev_yard.qa_deploy.missing_on_site", lambda *_a, **_k: None)
    monkeypatch.setattr("dev_yard.qa._involved_aliases", lambda *_a, **_k: ["reach"])
    root = tmp_path / "yard"
    root.mkdir()
    (root / "reqs" / "QA-1").mkdir(parents=True)
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "workers:\n  - {id: a, provider: rcc, model: m, concurrency: 1, priority: 1}\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:9\n    db: {url: postgres://localhost/app}\n"
        "    auth:\n      default: default\n      accounts:\n"
        "        default: {username: ada, password: secret}\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg)
    assert report["ok"] is True
    deploy = next(s for s in report["steps"] if s["step"] == "deploy")
    assert deploy["status"] == "warn"
    assert "未能判定" in deploy["detail"]
