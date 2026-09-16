from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_yard.qa_config import (
    QaConfigUnreadable,
    TestRejected,
    default_qa_payload,
    load_qa_config,
    qa_payload,
    save_qa_config,
)
from dev_yard.service import init_yard


def _yard(tmp_path: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    return yard


def _configure_pi(root: Path, provider: str = "rcc", model: str = "grok-4") -> None:
    """A workspace whose qa-run fallback resolves — required to omit workers."""
    (root / "repos.yaml").write_text(
        f"repos: {{}}\npi:\n  provider: {provider}\n  model: {model}\n",
        encoding="utf-8",
    )


def _payload(root: Path) -> dict:
    return {
        "active_env": "local",
        "browser": {"channel": "chrome", "headed": False},
        "workers": [
            {
                "id": "a",
                "provider": "rcc",
                "model": "grok-4",
                "concurrency": 2,
                "priority": 1,
            }
        ],
        "envs": {
            "local": {
                "base_url": "http://127.0.0.1:8080",
                "auth": {
                    "default": "default",
                    "accounts": {
                        "default": {
                            "username_env": "YARD_QA_USER",
                            "password_env": "YARD_QA_PASSWORD",
                            "state_file": ".yard-qa/auth-local-default.json",
                        }
                    },
                },
                "db": {"url_env": "YARD_QA_DB_URL"},
                "script": {"runner": "bin/rails runner"},
                "notes": ["先起前端", "别用生产库"],
            }
        },
    }


def test_default_payload_is_a_minimal_editable_form(tmp_path: Path):
    root = _yard(tmp_path)
    payload = default_qa_payload(root)
    assert payload["active_env"] == "local"
    assert payload["browser"] == {"channel": "chrome", "headed": False}
    assert len(payload["workers"]) == 1
    assert payload["envs"]["local"]["base_url"] == ""
    assert payload["envs"]["local"]["notes"] == []


def test_payload_prefills_missing_worker_from_qa_run_fallback(tmp_path: Path):
    """A worker row with no model shows the pair it would fall back to."""
    root = _yard(tmp_path)
    _configure_pi(root, "rcc", "MiniMax-M3")
    assert qa_payload(root)["workers"][0]["model"] == "MiniMax-M3"
    (root / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "workers:\n  - id: a\n    concurrency: 3\n",
        encoding="utf-8",
    )
    row = qa_payload(root)["workers"][0]
    assert row["provider"] == "rcc"
    assert row["model"] == "MiniMax-M3"
    assert row["id"] == "a"
    assert row["concurrency"] == 3


def test_save_then_load_roundtrip(tmp_path: Path):
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    cfg = load_qa_config(root)
    assert cfg.active_env == "local"
    assert cfg.env.base_url == "http://127.0.0.1:8080"
    assert cfg.env.db_url_env == "YARD_QA_DB_URL"
    assert cfg.env.script_runner == "bin/rails runner"
    assert cfg.env.notes == ("先起前端", "别用生产库")
    assert cfg.browser.channel == "chrome"
    assert [(w.id, w.provider, w.model, w.concurrency, w.priority) for w in cfg.workers] == [
        ("a", "rcc", "grok-4", 2, 1)
    ]


def test_save_then_payload_roundtrip_keeps_accounts(tmp_path: Path):
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    again = qa_payload(root)
    assert again["envs"]["local"]["auth"]["accounts"]["default"]["username_env"] == "YARD_QA_USER"
    assert again["envs"]["local"]["auth"]["accounts"]["default"]["state_file"] == (
        ".yard-qa/auth-local-default.json"
    )


def test_payload_reports_missing_file_as_defaults(tmp_path: Path):
    root = _yard(tmp_path)
    assert qa_payload(root)["envs"]["local"]["base_url"] == ""


def test_payload_opens_on_a_config_that_fails_validation(tmp_path: Path):
    """The form exists to repair bad config, so it must open on one."""
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: ''\n", encoding="utf-8"
    )
    with pytest.raises(TestRejected):
        load_qa_config(root)
    assert qa_payload(root)["envs"]["local"]["base_url"] == ""


def test_save_preserves_other_envs(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "envs:\n"
        "  local:\n    base_url: http://127.0.0.1:8080\n"
        "  test:\n    base_url: https://test.example.com\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    assert payload["other_envs"] == ["test"]
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["envs"]["test"]["base_url"] == "https://test.example.com"


def test_payload_keeps_browser_and_workers_when_envs_is_missing(tmp_path: Path):
    """Repairing a file that has no envs must not silently drop its other keys."""
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text(
        "browser:\n  channel: firefox\n  headed: true\n"
        "workers:\n  - id: slow\n    provider: rcc\n    model: MiniMax-M3\n"
        "    concurrency: 3\n    priority: 1\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    assert payload["browser"] == {"channel": "firefox", "headed": True}
    assert payload["workers"][0]["id"] == "slow"
    payload["envs"]["local"]["base_url"] = "http://127.0.0.1:8080"
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["browser"]["channel"] == "firefox"
    assert data["browser"]["headed"] is True
    assert data["workers"][0]["id"] == "slow"


def test_payload_opens_with_local_when_only_other_envs_exist(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "envs:\n"
        "  test:\n    base_url: https://test.example.com\n"
        "  k8s:\n    base_url: https://k8s.example.com\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    assert payload["active_env"] == "local"
    assert payload["envs"]["local"]["base_url"] == ""
    assert payload["other_envs"] == ["k8s", "test"]
    payload["envs"]["local"]["base_url"] = "http://127.0.0.1:8080"
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["envs"]["local"]["base_url"] == "http://127.0.0.1:8080"
    assert data["envs"]["test"]["base_url"] == "https://test.example.com"
    assert data["envs"]["k8s"]["base_url"] == "https://k8s.example.com"


def test_save_keeps_unknown_keys_in_the_edited_env(tmp_path: Path):
    """The form only owns some keys; the rest of the env must survive a save."""
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text(
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    future_key: keepme\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    payload["envs"]["local"]["notes"] = ["改过了"]
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["envs"]["local"]["future_key"] == "keepme"
    assert data["envs"]["local"]["notes"] == ["改过了"]


def test_save_keeps_a_cleared_managed_key_cleared(tmp_path: Path):
    """Overlaying unknown keys must not resurrect a managed key the user emptied."""
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    db:\n      url_env: YARD_QA_DB_URL\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    payload["envs"]["local"]["db"]["url_env"] = ""
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert "db" not in data["envs"]["local"]
    assert load_qa_config(root).env.db_url_env == ""


def test_payload_reports_non_mapping_envs_as_unreadable(tmp_path: Path):
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text("envs:\n  - local\n", encoding="utf-8")
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        qa_payload(root)
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        save_qa_config(root, _payload(root))
    assert (root / "qa.yaml").read_text(encoding="utf-8") == "envs:\n  - local\n"


def test_payload_reports_non_utf8_as_unreadable(tmp_path: Path):
    root = _yard(tmp_path)
    (root / "qa.yaml").write_bytes(
        "envs:\n  local:\n    base_url: http://x\n# 中文注释\n".encode("gbk")
    )
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        qa_payload(root)
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        save_qa_config(root, _payload(root))


def test_save_leaves_the_old_file_intact_when_the_write_fails(tmp_path: Path, monkeypatch):
    """A failed write must not leave a truncated qa.yaml behind."""
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    before = (root / "qa.yaml").read_text(encoding="utf-8")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr("dev_yard.qa_config.os.replace", boom)
    changed = _payload(root)
    changed["envs"]["local"]["base_url"] = "http://127.0.0.1:9999"
    with pytest.raises(OSError):
        save_qa_config(root, changed)
    assert (root / "qa.yaml").read_text(encoding="utf-8") == before


def test_save_rejects_missing_base_url(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["envs"]["local"]["base_url"] = ""
    with pytest.raises(TestRejected, match="base_url"):
        save_qa_config(root, payload)
    assert not (root / "qa.yaml").exists()


def test_save_rejects_unpaired_worker_model(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["workers"][0]["model"] = ""
    with pytest.raises(TestRejected, match="provider and model"):
        save_qa_config(root, payload)


def test_save_rejects_worker_concurrency_over_eight(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["workers"][0]["concurrency"] = 9
    with pytest.raises(TestRejected, match="<= 8"):
        save_qa_config(root, payload)


def test_save_rejects_total_concurrency_over_eight(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["workers"] = [
        {"id": "a", "provider": "rcc", "model": "grok-4", "concurrency": 4, "priority": 1},
        {"id": "b", "provider": "rcc", "model": "MiniMax-M3", "concurrency": 5, "priority": 2},
    ]
    with pytest.raises(TestRejected, match="max is 8"):
        save_qa_config(root, payload)


def test_save_rejects_duplicate_worker_id(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["workers"] = [
        {"id": "a", "provider": "rcc", "model": "grok-4", "concurrency": 1, "priority": 1},
        {"id": "a", "provider": "rcc", "model": "MiniMax-M3", "concurrency": 1, "priority": 2},
    ]
    with pytest.raises(TestRejected, match="not unique"):
        save_qa_config(root, payload)


def test_payload_raises_on_corrupt_yaml(tmp_path: Path):
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text("envs: [unclosed\n", encoding="utf-8")
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        qa_payload(root)


def test_save_refuses_to_clobber_a_corrupt_file(tmp_path: Path):
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text("envs: [unclosed\n", encoding="utf-8")
    with pytest.raises(QaConfigUnreadable, match="qa.yaml"):
        save_qa_config(root, _payload(root))
    assert (root / "qa.yaml").read_text(encoding="utf-8") == "envs: [unclosed\n"
