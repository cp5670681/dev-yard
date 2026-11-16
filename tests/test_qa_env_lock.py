"""The per-env lock: two requirements must not write one test DB at once.

Everything else that used to live in `test_qa_verify.py` (design-time data
verification, seeds.yaml, the reflow loop) is gone — the worker self-serves data
at run time, so only the lock is left to test.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dev_yard.qa_config import TestRejected
from dev_yard.qa_verify import env_lock


def test_env_lock_is_exclusive(tmp_path: Path):
    with env_lock(tmp_path, "local", what="qa run"):
        with pytest.raises(TestRejected, match="another qa run"):
            with env_lock(tmp_path, "local", what="qa run"):
                pass


def test_env_lock_waits_for_holder_when_asked(tmp_path: Path):
    """A wait_timeout lets a second run queue instead of failing."""
    held = threading.Event()
    acquired = threading.Event()

    def holder():
        with env_lock(tmp_path, "local"):
            acquired.set()
            held.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(2)
    order: list[str] = []

    def waiter():
        with env_lock(tmp_path, "local", wait_timeout=5):
            order.append("waiter")
        held.set()

    w = threading.Thread(target=waiter)
    w.start()
    w.join(timeout=8)
    assert order == ["waiter"]
    t.join(timeout=2)


def test_env_lock_reports_wait_state(tmp_path: Path):
    held = threading.Event()
    acquired = threading.Event()

    def holder():
        with env_lock(tmp_path, "local"):
            acquired.set()
            held.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(2)
    seen: list[bool] = []

    def waiter():
        with env_lock(tmp_path, "local", wait_timeout=5, on_wait=seen.append):
            seen.append(False)
            held.set()

    w = threading.Thread(target=waiter)
    w.start()
    w.join(timeout=8)
    assert seen == [True, False, False]
    t.join(timeout=2)
