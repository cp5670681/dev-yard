from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class PiRpcError(RuntimeError):
    pass


class PiRpc:
    """JSONL client for `pi --mode rpc`."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self._lock = threading.Lock()
        self._req = 0
        self._pending: dict[str, tuple[threading.Event, dict[str, Any] | None]] = {}
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._closed = False
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    @classmethod
    def start(cls, argv: list[str], cwd: Path) -> PiRpc:
        binary = argv[0]
        if not shutil.which(binary) and not Path(binary).exists():
            raise PiRpcError(
                f"pi not found (`{binary}`). Install pi or set YARD_PI to its path."
            )
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        return cls(proc)

    def _read(self) -> None:
        assert self.proc.stdout is not None
        buf = b""
        try:
            while True:
                chunk = self.proc.stdout.read(256)
                if not chunk:
                    break
                buf += chunk
                while True:
                    i = buf.find(b"\n")
                    if i < 0:
                        break
                    line = buf[:i]
                    buf = buf[i + 1 :]
                    if line.endswith(b"\r"):
                        line = line[:-1]
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("type") == "response":
                        req_id = obj.get("id")
                        if isinstance(req_id, str):
                            with self._lock:
                                pending = self._pending.get(req_id)
                            if pending is not None:
                                ev, _ = pending
                                with self._lock:
                                    self._pending[req_id] = (ev, obj)
                                ev.set()
                        continue
                    self._events.put(obj)
        finally:
            self._events.put(None)

    def command(self, kind: str, timeout: float = 30, **payload: Any) -> dict[str, Any]:
        if self.proc.poll() is not None:
            raise PiRpcError("pi rpc process exited")
        assert self.proc.stdin is not None
        with self._lock:
            self._req += 1
            req_id = str(self._req)
            ev = threading.Event()
            self._pending[req_id] = (ev, None)
        body = {"id": req_id, "type": kind, **payload}
        raw = json.dumps(body, ensure_ascii=False) + "\n"
        with self._lock:
            self.proc.stdin.write(raw.encode("utf-8"))
            self.proc.stdin.flush()
        try:
            if not ev.wait(timeout):
                raise PiRpcError(f"rpc {kind} timed out")
            with self._lock:
                _, resp = self._pending.get(req_id, (ev, None))
        finally:
            with self._lock:
                self._pending.pop(req_id, None)
        if not resp:
            raise PiRpcError(f"rpc {kind} returned no response")
        if not resp.get("success"):
            err = resp.get("error") or resp.get("message") or resp
            raise PiRpcError(f"rpc {kind} failed: {err}")
        return resp

    def prompt(self, message: str, streaming_behavior: str = "followUp") -> None:
        try:
            self.command("prompt", message=message)
        except PiRpcError as e:
            if "streaming" in str(e).lower():
                self.command(
                    "prompt",
                    message=message,
                    streamingBehavior=streaming_behavior,
                )
                return
            raise

    def abort(self) -> None:
        try:
            self.command("abort", timeout=10)
        except PiRpcError:
            pass

    def iter_until_settled(self, timeout: float = 600) -> Iterator[dict[str, Any]]:
        while True:
            try:
                event = self._events.get(timeout=timeout)
            except queue.Empty as e:
                raise PiRpcError("timed out waiting for pi") from e
            if event is None:
                code = self.proc.poll()
                raise PiRpcError(f"pi rpc exited (code={code})")
            yield event
            if event.get("type") == "agent_settled":
                return

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
