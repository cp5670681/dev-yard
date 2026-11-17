from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from dev_yard.jira import (
    create_dev_subtasks,
    extract_ticket_bodies,
    jira_creds,
    preview_dev_subtasks,
)


def test_jira_creds_from_env(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "http://jira.example.com")
    monkeypatch.setenv("JIRA_USER", "test.user")
    monkeypatch.setenv("JIRA_PASSWORD", "secret123")

    creds = jira_creds()
    assert creds == ("http://jira.example.com", "test.user", "secret123")


def test_extract_ticket_bodies():
    md = """# Tickets — PROJ-101

## T1: 第一张票
- repo: backend
- depends_on:

### 做什么
实现后端模型和 API。

### 验收
1. 测试通过。

## T2: 第二张票
- repo: frontend
- depends_on: T1

前端页面渲染。
"""
    bodies = extract_ticket_bodies(md)
    assert "T1" in bodies
    assert "### 做什么" in bodies["T1"]
    assert "### 验收" in bodies["T1"]
    assert "T2" in bodies
    assert bodies["T2"] == "前端页面渲染。"


def test_preview_and_create_subtasks(tmp_path, monkeypatch):
    root = tmp_path
    req_dir = root / "reqs" / "DEMO-1"
    req_dir.mkdir(parents=True)

    # TICKETS.md
    (req_dir / "TICKETS.md").write_text(
        """# Tickets — DEMO-1

## T1: 后端接口扩展
- repo: research
- depends_on:

### 做什么
开发核心功能。

## T2: 前端页面展示
- repo: research-front
- depends_on: T1

前端页面展示。
""",
        encoding="utf-8",
    )

    # STATUS.yaml
    (req_dir / "STATUS.yaml").write_text(
        """jira: DEMO-1
phase: frozen
tickets:
  T1:
    state: pending
    repo: research
  T2:
    state: pending
    repo: research-front
""",
        encoding="utf-8",
    )

    # Mock repos.yaml
    (root / "repos.yaml").write_text(
        """repos:
  research:
    url: git@example.com:research.git
    note: 后端
  research-front:
    url: git@example.com:research-front.git
    note: 前端
""",
        encoding="utf-8",
    )

    # Mock jira credentials
    monkeypatch.setenv("JIRA_BASE_URL", "http://jira.example.com")
    monkeypatch.setenv("JIRA_USER", "tester")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    # Mock fetch_parent_issue
    def mock_fetch_parent(key, creds=None, root=None):
        return {
            "key": "DEMO-1",
            "summary": "父需求测试",
            "project_key": "DEMO",
            "assignee_name": "tom.cheng",
            "assignee_display": "Tom Cheng",
        }

    monkeypatch.setattr("dev_yard.jira.fetch_parent_issue", mock_fetch_parent)

    # 1. Preview
    preview = preview_dev_subtasks(root, "DEMO-1")
    assert preview["jira"] == "DEMO-1"
    assert preview["project_key"] == "DEMO"
    assert len(preview["items"]) == 2
    t1 = preview["items"][0]
    assert t1["id"] == "T1"
    assert t1["summary"] == "【后端】接口扩展"
    assert t1["story_point"] == 0
    assert not t1["created"]

    t2 = preview["items"][1]
    assert t2["id"] == "T2"
    assert t2["summary"] == "【前端】页面展示"

    # 2. Create subtasks
    created_calls = []

    def mock_jira_request(path, method="GET", payload=None, creds=None, root=None, timeout=30):
        if method == "POST" and path == "rest/api/2/issue":
            created_calls.append(payload)
            key = f"DEMO-{100 + len(created_calls)}"
            return {"key": key, "id": str(1000 + len(created_calls))}
        if method == "POST" and "/comment" in path:
            return {"id": "1"}
        return {}

    monkeypatch.setattr("dev_yard.jira._jira_request", mock_jira_request)

    res = create_dev_subtasks(root, "DEMO-1")
    assert len(res) == 2
    assert res[0]["id"] == "T1"
    assert res[0]["jira_key"] == "DEMO-101"
    assert res[1]["id"] == "T2"
    assert res[1]["jira_key"] == "DEMO-102"

    # Verify STATUS.yaml was updated with jira_key
    from dev_yard import status as st

    saved_status = st.load(root, "DEMO-1")
    assert saved_status["tickets"]["T1"]["jira_key"] == "DEMO-101"
    assert saved_status["tickets"]["T1"]["jira_url"] == "http://jira.example.com/browse/DEMO-101"
    assert saved_status["tickets"]["T2"]["jira_key"] == "DEMO-102"

    # Preview again should show created=True
    preview2 = preview_dev_subtasks(root, "DEMO-1")
    assert preview2["items"][0]["created"] is True
    assert preview2["items"][0]["jira_key"] == "DEMO-101"

    # Re-call create_dev_subtasks should be idempotent (skip already created)
    res_repeat = create_dev_subtasks(root, "DEMO-1")
    assert len(res_repeat) == 0

    # 3. Test API
    from fastapi.testclient import TestClient
    from dev_yard.web.app import create_app

    client = TestClient(create_app(root))
    r = client.get("/api/requirements/DEMO-1/jira-subtasks/preview")
    assert r.status_code == 200
    data = r.json()
    assert data["jira"] == "DEMO-1"
    assert len(data["items"]) == 2
    assert all(it["created"] for it in data["items"])

    # POST to create already created ticket without force -> skipped
    r2 = client.post(
        "/api/requirements/DEMO-1/jira-subtasks",
        json={"items": [{"id": "T1", "summary": "自定义标题", "story_point": 0}]},
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert r2.json()["created"] == []


def test_aggregate_subtasks_by_repo(tmp_path, monkeypatch):
    root = tmp_path
    req_dir = root / "reqs" / "DEMO-2"
    req_dir.mkdir(parents=True)

    (req_dir / "TICKETS.md").write_text(
        """# Tickets — DEMO-2

## T1: 后端表设计
- repo: backend
- depends_on:

表结构设计与迁移。

## T2: 后端接口实现
- repo: backend
- depends_on: T1

接口编写。

## T3: 前端页面展示
- repo: frontend
- depends_on: T2

前端页面。
""",
        encoding="utf-8",
    )

    (req_dir / "STATUS.yaml").write_text(
        """jira: DEMO-2
phase: frozen
tickets:
  T1:
    state: pending
    repo: backend
  T2:
    state: pending
    repo: backend
  T3:
    state: pending
    repo: frontend
""",
        encoding="utf-8",
    )

    (root / "repos.yaml").write_text(
        """repos:
  backend:
    url: git@example.com:backend.git
    note: 后端
  frontend:
    url: git@example.com:frontend.git
    note: 前端
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("JIRA_BASE_URL", "http://jira.example.com")
    monkeypatch.setenv("JIRA_USER", "tester")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    def mock_fetch_parent(key, creds=None, root=None):
        return {
            "key": "DEMO-2",
            "summary": "父需求聚合测试",
            "project_key": "DEMO",
            "assignee_name": "developer",
            "assignee_display": "Developer",
        }

    monkeypatch.setattr("dev_yard.jira.fetch_parent_issue", mock_fetch_parent)

    created_issues = []
    comments_sent = []

    def mock_jira_request(path, method="GET", payload=None, creds=None, root=None, timeout=30):
        if method == "POST" and path == "rest/api/2/issue":
            created_issues.append(payload)
            key = f"DEMO-{200 + len(created_issues)}"
            return {"key": key, "id": str(2000 + len(created_issues))}
        if method == "POST" and "/comment" in path:
            comments_sent.append((path, payload))
            return {"id": "comment-1"}
        return {}

    monkeypatch.setattr("dev_yard.jira._jira_request", mock_jira_request)

    # 聚合创建：T1+T2 合并为一个后端子任务，T3 单独一个前端子任务
    items_to_create = [
        {
            "id": "repo:backend",
            "ticket_ids": ["T1", "T2"],
            "summary": "【后端】DEMO-2 后端开发",
            "story_point": 3,
            "repo": "backend",
        },
        {
            "id": "repo:frontend",
            "ticket_ids": ["T3"],
            "summary": "【前端】DEMO-2 前端开发",
            "story_point": 2,
            "repo": "frontend",
        },
    ]

    res = create_dev_subtasks(root, "DEMO-2", items=items_to_create)
    assert len(res) == 2
    assert res[0]["ticket_ids"] == ["T1", "T2"]
    assert res[0]["jira_key"] == "DEMO-201"
    assert res[1]["ticket_ids"] == ["T3"]
    assert res[1]["jira_key"] == "DEMO-202"

    from dev_yard import status as st

    saved_status = st.load(root, "DEMO-2")
    # T1 和 T2 应该都绑定同一个 jira_key DEMO-201
    assert saved_status["tickets"]["T1"]["jira_key"] == "DEMO-201"
    assert saved_status["tickets"]["T1"]["jira_url"] == "http://jira.example.com/browse/DEMO-201"
    assert saved_status["tickets"]["T2"]["jira_key"] == "DEMO-201"
    assert saved_status["tickets"]["T2"]["jira_url"] == "http://jira.example.com/browse/DEMO-201"
    # T3 绑定 DEMO-202
    assert saved_status["tickets"]["T3"]["jira_key"] == "DEMO-202"

    # 检查评论中包含了 T1 和 T2
    assert any("包含 2 张研发票：T1, T2" in c[1]["body"] for c in comments_sent)


