from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_yard.qa_config import (
    MASK,
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
    """A workspace whose global pi pair resolves — required to omit workers."""
    (root / "repos.yaml").write_text(
        f"repos: {{}}\npi:\n  provider: {provider}\n  model: {model}\n",
        encoding="utf-8",
    )


def _payload(root: Path) -> dict:
    return {
        "active_env": "local",
        "browser": {"channel": "chrome", "headed": False},
        "design": {"provider": "rcc", "model": "glm-5.3"},
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
                            "username": "admin",
                            "password": "s3cret",
                            "state_file": ".yard-qa/auth-local-default.json",
                        }
                    },
                },
                "db": {"url": "postgres://localhost:5432/app"},
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
    assert payload["design"] == {"provider": "", "model": ""}
    assert len(payload["workers"]) == 1
    assert payload["envs"]["local"]["base_url"] == ""
    assert payload["envs"]["local"]["notes"] == []


def test_payload_prefills_missing_worker_from_pi_global(tmp_path: Path):
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
    assert cfg.env.db_url == "postgres://localhost:5432/app"
    assert cfg.env.script_runner == "bin/rails runner"
    assert cfg.env.notes == ("先起前端", "别用生产库")
    assert cfg.browser.channel == "chrome"
    assert (cfg.design_provider, cfg.design_model) == ("rcc", "glm-5.3")
    assert [(w.id, w.provider, w.model, w.concurrency, w.priority) for w in cfg.workers] == [
        ("a", "rcc", "grok-4", 2, 1)
    ]


def test_save_then_payload_roundtrip_keeps_accounts(tmp_path: Path):
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    again = qa_payload(root)
    assert again["envs"]["local"]["auth"]["accounts"]["default"]["username"] == "admin"
    assert again["envs"]["local"]["auth"]["accounts"]["default"]["password"] == MASK
    assert again["envs"]["local"]["auth"]["accounts"]["default"]["state_file"] == (
        ".yard-qa/auth-local-default.json"
    )


def test_payload_masks_secrets_and_save_keeps_them(tmp_path: Path):
    """The form never receives real secrets; returning the mask keeps them."""
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    payload = qa_payload(root)
    assert payload["envs"]["local"]["db"]["url"] == MASK
    payload["envs"]["local"]["notes"] = ["改过"]
    save_qa_config(root, payload)
    cfg = load_qa_config(root)
    assert cfg.env.db_url == "postgres://localhost:5432/app"
    assert cfg.env.accounts["default"].password == "s3cret"


def test_save_can_replace_a_masked_password(tmp_path: Path):
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    payload = qa_payload(root)
    payload["envs"]["local"]["auth"]["accounts"]["default"]["password"] = "new-pass"
    payload["envs"]["local"]["db"]["url"] = "postgres://new"
    save_qa_config(root, payload)
    cfg = load_qa_config(root)
    assert cfg.env.accounts["default"].password == "new-pass"
    assert cfg.env.db_url == "postgres://new"


def test_redact_qa_yaml_masks_passwords_and_urls():
    from dev_yard.qa_config import redact_qa_yaml

    text = (
        "envs:\n"
        "  local:\n"
        "    auth:\n"
        "      accounts:\n"
        "        admin:\n"
        "          username: admin\n"
        "          password: s3cret\n"
        "    db:\n      url: postgres://user:pw@host/db\n"
    )
    out = redact_qa_yaml(text)
    assert "s3cret" not in out
    assert "user:pw@host" not in out
    assert "username: admin" in out
    assert out.count(MASK) == 2


def test_redact_qa_yaml_covers_inline_and_unterminated_values():
    from dev_yard.qa_config import redact_qa_yaml

    inline = "envs: {local: {auth: {accounts: {d: {password: s3cret}}}},\n"
    inline += "  db: {url: \"postgres://u:s3cret@h/db\"}}\n"
    out = redact_qa_yaml(inline)
    assert "s3cret" not in out
    assert out.count(MASK) == 2

    # A YAML parse error can echo an unterminated value with no closing quote.
    err = "while parsing: url: \"postgres://u:SUPERSECRET@h/db"
    assert "SUPERSECRET" not in redact_qa_yaml(err)


def test_redact_qa_yaml_masks_values_with_spaces():
    from dev_yard.qa_config import redact_qa_yaml

    block = "          password: s3 cr et\n"
    flow = 'db: {url: "postgres://u:s3 cr et@h/db"}\n'
    out = redact_qa_yaml(block + flow)
    assert "s3 cr et" not in out
    assert out.count(MASK) == 2


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


def test_save_roundtrips_multiple_envs(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    payload = _payload(root)
    payload["active_env"] = "test"
    payload["envs"]["test"] = {
        "base_url": "https://test.example.com",
        "auth": {"default": "default", "accounts": {}},
        "db": {"url": ""},
        "script": {"runner": ""},
        "notes": ["test 环境"],
    }
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["active_env"] == "test"
    assert data["envs"]["local"]["base_url"] == "http://127.0.0.1:8080"
    assert data["envs"]["test"]["base_url"] == "https://test.example.com"
    cfg = load_qa_config(root)
    assert cfg.active_env == "test"
    assert cfg.env.base_url == "https://test.example.com"
    assert cfg.env_names == ("local", "test")


def test_load_selects_a_named_env(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "envs:\n"
        "  local:\n    base_url: http://127.0.0.1:8080\n"
        "  test:\n    base_url: https://test.example.com\n",
        encoding="utf-8",
    )
    assert load_qa_config(root).env.base_url == "http://127.0.0.1:8080"
    assert load_qa_config(root, "test").env.base_url == "https://test.example.com"


def test_load_rejects_an_unknown_env(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    with pytest.raises(TestRejected, match="no envs.staging"):
        load_qa_config(root, "staging")


def test_save_removes_an_env_dropped_from_the_payload(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    payload = _payload(root)
    payload["envs"]["test"] = {
        "base_url": "https://test.example.com",
        "auth": {"default": "default", "accounts": {}},
        "db": {"url": ""},
        "script": {"runner": ""},
        "notes": [],
    }
    save_qa_config(root, payload)
    payload["envs"].pop("test")
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert list(data["envs"]) == ["local"]


def test_save_rejects_active_env_that_is_not_configured(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["active_env"] = "staging"
    with pytest.raises(TestRejected, match="active_env"):
        save_qa_config(root, payload)
    assert not (root / "qa.yaml").exists()


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


def test_payload_projects_every_env_as_editable(tmp_path: Path):
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "envs:\n"
        "  test:\n    base_url: https://test.example.com\n"
        "  k8s:\n    base_url: https://k8s.example.com\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    assert payload["active_env"] == "test"
    assert payload["env_names"] == ["test", "k8s"]
    assert set(payload["envs"]) == {"test", "k8s"}
    assert payload["envs"]["test"]["base_url"] == "https://test.example.com"
    assert payload["envs"]["k8s"]["base_url"] == "https://k8s.example.com"
    payload["envs"]["k8s"]["base_url"] = "https://k8s2.example.com"
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["envs"]["test"]["base_url"] == "https://test.example.com"
    assert data["envs"]["k8s"]["base_url"] == "https://k8s2.example.com"


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


def test_save_keeps_nested_unknown_keys(tmp_path: Path):
    """The form owns base_url/auth/db/script, but keys under them must survive."""
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n"
        "  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: default\n      sso: true\n"
        "      accounts:\n        default:\n          username: admin\n"
        "          future: keep\n"
        "    db:\n      url: postgres://db\n      driver: pg\n"
        "    script:\n      runner: bin/rails runner\n      shell: bash\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    payload["envs"]["local"]["notes"] = ["改过"]
    save_qa_config(root, payload)
    env = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))["envs"]["local"]
    assert env["auth"]["sso"] is True
    assert env["auth"]["accounts"]["default"]["future"] == "keep"
    assert env["db"]["driver"] == "pg"
    assert env["script"]["shell"] == "bash"
    assert env["notes"] == ["改过"]


def test_save_allows_a_placeholder_non_active_env(tmp_path: Path):
    """A second env with no base_url yet must not block saving the active one."""
    root = _yard(tmp_path)
    _configure_pi(root)
    payload = _payload(root)
    payload["envs"]["test"] = {
        "base_url": "",
        "auth": {"default": "default", "accounts": {}},
        "db": {"url": ""},
        "script": {"runner": ""},
        "notes": [],
    }
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert data["envs"]["test"]["base_url"] == ""
    assert load_qa_config(root).env.base_url == "http://127.0.0.1:8080"
    with pytest.raises(TestRejected, match="base_url"):
        load_qa_config(root, "test")


def test_save_keeps_a_cleared_managed_key_cleared(tmp_path: Path):
    """Overlaying unknown keys must not resurrect a managed key the user emptied."""
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    db:\n      url: postgres://db\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    payload["envs"]["local"]["db"]["url"] = ""
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert "db" not in data["envs"]["local"]
    assert load_qa_config(root).env.db_url == ""


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


def test_save_rejects_unpaired_design_model(tmp_path: Path):
    root = _yard(tmp_path)
    payload = _payload(root)
    payload["design"]["model"] = ""
    with pytest.raises(TestRejected, match="design"):
        save_qa_config(root, payload)


def test_clearing_design_drops_it_from_qa_yaml(tmp_path: Path):
    root = _yard(tmp_path)
    save_qa_config(root, _payload(root))
    payload = qa_payload(root)
    payload["design"] = {"provider": "", "model": ""}
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    assert "design" not in data
    assert load_qa_config(root).design_provider is None


def test_payload_projects_design_from_disk(tmp_path: Path):
    root = _yard(tmp_path)
    (root / "qa.yaml").write_text(
        "design:\n  provider: rcc\n  model: glm-5.3\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n",
        encoding="utf-8",
    )
    payload = qa_payload(root)
    assert payload["design"] == {"provider": "rcc", "model": "glm-5.3"}


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


def test_save_drops_legacy_env_var_keys(tmp_path: Path):
    """Old *_env fields are no longer read and must not survive a save."""
    root = _yard(tmp_path)
    _configure_pi(root)
    (root / "qa.yaml").write_text(
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: default\n"
        "      accounts:\n        default:\n"
        "          username_env: OLD_USER\n          password_env: OLD_PASS\n"
        "    db:\n      url_env: OLD_DB_URL\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(root)
    assert cfg.env.db_url == ""
    assert cfg.env.accounts["default"].username == ""
    payload = qa_payload(root)
    payload["envs"]["local"]["auth"]["accounts"]["default"] = {
        "username": "admin",
        "password": "s3cret",
        "state_file": ".yard-qa/auth.json",
    }
    payload["envs"]["local"]["db"]["url"] = "postgres://db"
    save_qa_config(root, payload)
    data = yaml.safe_load((root / "qa.yaml").read_text(encoding="utf-8"))
    env = data["envs"]["local"]
    assert "url_env" not in env["db"]
    assert "username_env" not in env["auth"]["accounts"]["default"]
    assert "password_env" not in env["auth"]["accounts"]["default"]
    assert load_qa_config(root).env.db_url == "postgres://db"


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
