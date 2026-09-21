"""Script execution recipes: transport vs runner, host owns stdin."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from dev_yard.exec_cfg import QaExec, default_exec, is_local_base_url
from dev_yard.qa_config import QaEnv, TestRejected, redact_qa_yaml

LogFn = Callable[[str], None]

HELLO = "qa-check-env-ok"
_ENV_TOKEN = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")
_ENV_IN_STR = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
FUSE_CLASSES = frozenset(
    {"unreachable", "auth", "pod_not_found", "timeout", "runner"}
)
DELEGATE_PROMPT = """# yard-exec-delegate v1
Execute the script at {script} using skill `{skill}`.
Pass the script bytes via stdin or argv; do not inline-rewrite its content.
Write a JSON object to `{result}` with keys exit_code (int), stdout (str), stderr (str).
Do not print secrets. Host trusts only that file.
QA_ENV={qa_env} QA_JIRA={jira} QA_CASE_ID={case_id} QA_SCRIPT_KIND={kind}
"""


class ExecErrorClass(StrEnum):
    UNREACHABLE = "unreachable"
    AUTH = "auth"
    POD_NOT_FOUND = "pod_not_found"
    TIMEOUT = "timeout"
    RUNNER = "runner"
    SCRIPT = "script"
    CONFIG = "config"


@dataclass
class ExecResult:
    code: int
    stdout: str
    stderr: str
    error_class: ExecErrorClass | None = None


class ExecUnreachable(TestRejected):
    def __init__(
        self, message: str, error_class: ExecErrorClass = ExecErrorClass.UNREACHABLE
    ) -> None:
        super().__init__(message, error_class=str(error_class))
        self.error_class = str(error_class)


def _qa_exec(env: QaEnv) -> QaExec:
    return env.exec_cfg or default_exec(runner=env.script_runner, db_exec=env.db_exec)


def assert_same_site(env: QaEnv, *, base_url: str) -> str | None:
    """Return a warning when cross-site is explicitly allowed; raise otherwise."""
    ex = _qa_exec(env)
    local_url = is_local_base_url(base_url)
    local_site = ex.site == "local"
    if local_url == local_site:
        return None
    # Browser on loopback and docker exec on this machine are the same world.
    if ex.use == "docker" and local_url:
        return None
    host = urlparse(base_url).hostname or base_url
    if ex.allow_cross_site:
        return (
            f"allow_cross_site: env {env.name} base_url host={host!r} exec.use={ex.use}"
        )
    if local_site and not local_url:
        raise TestRejected(
            "远程 base_url 配了 local 执行现场：浏览器打测试环境、脚本在本机 worktree 起进程"
            "（PG-13054 同类事故）。给 envs.*.exec 配远程 use，或显式 allow_cross_site: true。",
            error_class=ExecErrorClass.CONFIG,
        )
    raise TestRejected(
        "本机 base_url 配了远程执行现场。浏览器与脚本必须在同一个世界。",
        error_class=ExecErrorClass.CONFIG,
    )


class ScriptExecutor:
    label: str = "exec"
    use: str = "local"
    site: Literal["local", "remote"] = "local"

    def __init__(
        self,
        spec: QaExec,
        *,
        env: QaEnv,
        worktree: Path | None = None,
        root: Path | None = None,
    ):
        self.spec = spec
        self.env = env
        self.worktree = worktree
        self.root = root
        self.use = spec.use
        self.site = "local" if spec.site == "local" else "remote"
        self.label = spec.use
        self.cross_site_warning = ""

    def ping(self, *, timeout: int | None = None) -> None:
        raise NotImplementedError

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        raise NotImplementedError

    def logs(
        self,
        needle: str,
        *,
        tail: int = 2000,
        timeout: int | None = None,
        on_log: LogFn | None = None,
    ) -> str:
        raise TestRejected(
            f"exec.use={self.use} 不支持日志查询；5xx 诊断需要 jms-k8s（test 环境）",
            error_class=ExecErrorClass.CONFIG,
        )

    def close(self) -> None:
        return None


def resolve_executor(
    env: QaEnv,
    *,
    base_url: str | None = None,
    worktree: Path | None = None,
    root: Path | None = None,
) -> ScriptExecutor:
    url = base_url if base_url is not None else env.base_url
    warning = assert_same_site(env, base_url=url)
    spec = _qa_exec(env)
    cls = {
        "local": LocalExecutor,
        "ssh": SshExecutor,
        "docker": DockerExecutor,
        "jms-k8s": JmsK8sExecutor,
        "raw": RawExecutor,
        "delegate": DelegateExecutor,
    }[spec.use]
    ex = cls(spec, env=env, worktree=worktree, root=root)
    ex.cross_site_warning = warning or ""
    return ex


def _tokens(command: str) -> list[str]:
    return shlex.split(command) if command else []


def _subst_argv(argv: list[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    out: list[str] = []
    names: list[str] = []
    for item in argv:
        m = _ENV_TOKEN.fullmatch(item)
        if m:
            key = m.group(1)
            if key not in os.environ:
                raise TestRejected(
                    f"qa.yaml exec 引用了环境变量 {key}，但未设置",
                    error_class=ExecErrorClass.CONFIG,
                )
            out.append(os.environ[key])
            names.append(key)
            continue
        if "{env:" in item:
            raise TestRejected(
                "{env:NAME} 只能替换整个 argv 元素",
                error_class=ExecErrorClass.CONFIG,
            )
        if "{script}" in item:
            raise TestRejected(
                "exec 命令不得包含 {script}，stdin 由宿主提供",
                error_class=ExecErrorClass.CONFIG,
            )
        out.append(item)
    return out, names


def _subst_shell(text: str) -> tuple[str, list[str]]:
    names: list[str] = []

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in os.environ:
            raise TestRejected(
                f"qa.yaml exec 引用了环境变量 {key}，但未设置",
                error_class=ExecErrorClass.CONFIG,
            )
        names.append(key)
        # Quote the substituted value: in a shell recipe it is a single word,
        # never an injection point for `;`/`$(...)`/backticks.
        return shlex.quote(os.environ[key])

    return _ENV_IN_STR.sub(repl, text), names


def classify_error(
    stderr: str,
    *,
    timeout: bool = False,
    ping: bool = False,
    code: int = 1,
) -> ExecErrorClass:
    if timeout:
        return ExecErrorClass.TIMEOUT
    text = (stderr or "").lower()
    if any(
        s in text
        for s in (
            "permission denied",
            "authentication failed",
            "auth fail",
            "too many authentication",
            "connection closed by authenticating",
        )
    ):
        return ExecErrorClass.AUTH
    if (
        "no pods" in text
        or "pod not found" in text
        or ("not found" in text and "pod" in text)
    ):
        return ExecErrorClass.POD_NOT_FOUND
    if any(
        s in text
        for s in (
            "no route to host",
            "network is unreachable",
            "connection refused",
            "could not resolve",
            "name or service not known",
            "connection timed out",
            "operation timed out",
        )
    ):
        return ExecErrorClass.UNREACHABLE
    if ping:
        return ExecErrorClass.UNREACHABLE
    if any(
        s in text
        for s in ("loaderror", "boot", "database.yml", "could not connect to server")
    ):
        return ExecErrorClass.RUNNER
    if code != 0:
        return ExecErrorClass.SCRIPT
    return ExecErrorClass.SCRIPT


def _log_cmd(on_log: LogFn | None, argv: list[str], *, names: list[str] | None = None) -> None:
    if on_log is None:
        return
    shown = redact_qa_yaml(" ".join(shlex.quote(a) for a in argv))
    extra = f" env={','.join(names)}" if names else ""
    on_log(f"$ {shown}{extra}")


def _run_proc(
    argv: list[str],
    *,
    stdin: bytes | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 300,
    label: str,
    on_log: LogFn | None = None,
    log_names: list[str] | None = None,
    shell: bool = False,
) -> ExecResult:
    _log_cmd(on_log, argv if not shell else argv, names=log_names)
    try:
        proc = subprocess.run(
            argv if not shell else argv[0] if len(argv) == 1 else argv,
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            shell=shell,
        )
    except subprocess.TimeoutExpired as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace") if e.stderr else ""
        return ExecResult(-1, "", err or f"{label} timed out after {timeout}s", ExecErrorClass.TIMEOUT)
    except OSError as e:
        return ExecResult(
            127,
            "",
            f"cannot run {label}: {e}",
            ExecErrorClass.UNREACHABLE,
        )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    code = proc.returncode
    err_cls = None if code == 0 else classify_error(stderr or stdout, code=code)
    return ExecResult(code, stdout, stderr, err_cls)


def _script_bytes(script: Path) -> bytes:
    return script.read_bytes()


def _qa_env(extra: dict[str, str] | None, spec_env: dict[str, str]) -> dict[str, str]:
    out = os.environ.copy()
    out.update(spec_env)
    if extra:
        out.update(extra)
    return out


_QA_KEYS = ("QA_ENV", "QA_JIRA", "QA_CASE_ID", "QA_SCRIPT_KIND")


def _qa_assigns(env_extra: dict[str, str] | None) -> list[tuple[str, str]]:
    if not env_extra:
        return []
    return [(k, str(env_extra[k])) for k in _QA_KEYS if k in env_extra]


def _remote_shell(
    workdir: str, argv: list[str], env_extra: dict[str, str] | None
) -> str:
    bits: list[str] = []
    for key, value in _qa_assigns(env_extra):
        bits.append(f"export {key}={shlex.quote(value)}")
    if workdir:
        bits.append(f"cd {shlex.quote(workdir)}")
    bits.append(" ".join(shlex.quote(a) for a in argv))
    return " && ".join(bits)


def _clip_diag(result: ExecResult, limit: int = 240) -> str:
    text = redact_qa_yaml((result.stderr or result.stdout or "").strip())
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


_QA_EXIT_SENTINEL = "__QA_EXIT__="


def _restore_remote_exit(result: ExecResult) -> ExecResult:
    """Recover the real exit code JMS/kubectl swallowed.

    A failing remote command still yields ssh exit 0 (the real code only shows
    up as `command terminated with exit code N` text). Callers append an
    in-band sentinel; strip it from stdout and restore `code`/`error_class`.
    """
    out = result.stdout or ""
    if _QA_EXIT_SENTINEL not in out:
        return result
    kept: list[str] = []
    code: int | None = None
    for line in out.splitlines():
        if line.startswith(_QA_EXIT_SENTINEL):
            try:
                code = int(line[len(_QA_EXIT_SENTINEL):].strip())
            except ValueError:
                kept.append(line)
            continue
        kept.append(line)
    if code is None:
        return result
    text = "\n".join(kept)
    if out.endswith("\n"):
        text += "\n"
    result.stdout = text
    result.code = code
    result.error_class = (
        None if code == 0 else classify_error(result.stderr or text, code=code)
    )
    return result


def _remote_runner(spec: QaExec, script: Path) -> list[str]:
    runner = spec.runner
    if not runner:
        raise TestRejected(
            f"non-sql script {script.name} needs exec.with.runner 或 script.runner",
            error_class=ExecErrorClass.CONFIG,
        )
    return [*_tokens(runner), "-"]


def _cd_wrap(workdir: str, argv: list[str]) -> list[str]:
    if not workdir:
        return argv
    inner = " ".join(shlex.quote(a) for a in argv)
    return ["sh", "-c", f"cd {shlex.quote(workdir)} && {inner}"]


class LocalExecutor(ScriptExecutor):
    def ping(self, *, timeout: int | None = None) -> None:
        wt = self.worktree
        if wt is None or not wt.is_dir():
            raise ExecUnreachable("local exec 需要 freeze worktree")
        if not self.spec.runner:
            return
        first = _tokens(self.spec.runner)[0]
        path = first if os.path.isabs(first) else wt / first
        if not Path(path).exists() and shutil.which(first) is None:
            raise ExecUnreachable(f"local runner 找不到 {first}")

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        wt = self.worktree
        if wt is None or not wt.is_dir():
            raise TestRejected(
                f"cannot run {script.name}: freeze worktree missing for this case",
                error_class=ExecErrorClass.CONFIG,
            )
        if not self.env.db_url:
            raise TestRejected(
                "script.runner needs qa.yaml db.url so it talks to the same DB as the browser, "
                "not the worktree's local database.yml",
                error_class=ExecErrorClass.CONFIG,
            )
        if self.spec.payload == "bundle":
            cmd = [*_tokens(self.spec.runner), script.name]
            cwd = script.parent
            stdin = None
        else:
            cmd = _remote_runner(self.spec, script)
            cwd = wt
            stdin = _script_bytes(script)
        extra_env: dict[str, str] = {}
        if self.env.db_url:
            extra_env["DATABASE_URL"] = self.env.db_url
        return _run_proc(
            cmd,
            stdin=stdin,
            cwd=cwd,
            env=_qa_env(env_extra, extra_env),
            timeout=timeout or self.spec.timeout,
            label=f"{self.spec.runner} {script.name}",
            on_log=on_log,
        )


class SshMux:
    def __init__(self, port: int, identity: str):
        self.port = port
        self.identity = identity
        self._dir = Path(tempfile.mkdtemp(prefix="yard-ssh-"))
        self.path = str(self._dir / "cm.sock")

    def prefix(self) -> list[str]:
        return [
            "ssh",
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={self.path}",
            "-o",
            "ControlPersist=60",
            self.identity,
        ]

    def close(self) -> None:
        try:
            subprocess.run(
                [
                    "ssh",
                    "-O",
                    "exit",
                    "-o",
                    f"ControlPath={self.path}",
                    self.identity,
                ],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        shutil.rmtree(self._dir, ignore_errors=True)


class SshExecutor(ScriptExecutor):
    def __init__(self, spec: QaExec, *, env: QaEnv, worktree: Path | None = None, root: Path | None = None):
        super().__init__(spec, env=env, worktree=worktree, root=root)
        self._mux = SshMux(spec.ssh_port, spec.ssh_target)

    def close(self) -> None:
        self._mux.close()

    def _remote(self, remote: str, *, stdin: bytes | None, timeout: int, on_log: LogFn | None) -> ExecResult:
        argv = [*self._mux.prefix(), remote]
        return _run_proc(
            argv,
            stdin=stdin,
            timeout=timeout,
            label=f"ssh {self.spec.use}",
            on_log=on_log,
        )

    def ping(self, *, timeout: int | None = None) -> None:
        r = self._remote(
            "echo ok",
            stdin=None,
            timeout=timeout or self.spec.ping_timeout,
            on_log=None,
        )
        if r.code != 0:
            cls = classify_error(r.stderr or r.stdout, ping=True, code=r.code)
            if cls == ExecErrorClass.TIMEOUT:
                cls = ExecErrorClass.TIMEOUT
            raise ExecUnreachable(
                f"ssh ping 失败: {_clip_diag(r) or 'no output'}",
                error_class=cls if cls != ExecErrorClass.SCRIPT else ExecErrorClass.UNREACHABLE,
            )

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        if self.spec.payload == "bundle":
            return self._run_bundle(script, env_extra=env_extra, on_log=on_log, timeout=timeout)
        remote = _remote_shell(
            self.spec.workdir, _remote_runner(self.spec, script), env_extra
        )
        return self._remote(
            remote,
            stdin=_script_bytes(script),
            timeout=timeout or self.spec.timeout,
            on_log=on_log,
        )

    def _run_bundle(
        self,
        script: Path,
        *,
        env_extra: dict[str, str] | None,
        on_log: LogFn | None,
        timeout: int | None,
    ) -> ExecResult:
        tar_bytes = _tar_dir(script.parent)
        dest = f"/tmp/qa-exec-{os.getpid()}"
        exports = " && ".join(
            f"export {k}={shlex.quote(v)}" for k, v in _qa_assigns(env_extra)
        )
        prefix = f"{exports} && " if exports else ""
        inner = (
            f"{prefix}mkdir -p {shlex.quote(dest)} && tar -x -C {shlex.quote(dest)} && "
            f"cd {shlex.quote(dest)} && "
            + " ".join(shlex.quote(a) for a in [*_tokens(self.spec.runner), script.name])
            + f"; e=$?; rm -rf {shlex.quote(dest)}; exit $e"
        )
        return self._remote(
            inner,
            stdin=tar_bytes,
            timeout=timeout or self.spec.timeout,
            on_log=on_log,
        )


def _tar_dir(directory: Path) -> bytes:
    buf = tempfile.SpooledTemporaryFile()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(directory, arcname=".")
    buf.seek(0)
    return buf.read()


class DockerExecutor(ScriptExecutor):
    def __init__(self, spec: QaExec, *, env: QaEnv, worktree: Path | None = None, root: Path | None = None):
        super().__init__(spec, env=env, worktree=worktree, root=root)

    def ping(self, *, timeout: int | None = None) -> None:
        r = _run_proc(
            ["docker", "exec", self.spec.container, "echo", "ok"],
            timeout=timeout or self.spec.ping_timeout,
            label="docker ping",
        )
        if r.code != 0:
            raise ExecUnreachable(
                f"docker ping 失败: {_clip_diag(r) or 'no output'}",
                error_class=classify_error(r.stderr or r.stdout, ping=True, code=r.code),
            )

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        argv = ["docker", "exec", "-i"]
        for key, value in _qa_assigns(env_extra):
            argv.extend(["-e", f"{key}={value}"])
        argv.append(self.spec.container)
        inner = _cd_wrap(self.spec.workdir, _remote_runner(self.spec, script))
        argv.extend(inner)
        return _run_proc(
            argv,
            stdin=_script_bytes(script),
            timeout=timeout or self.spec.timeout,
            label=f"docker {script.name}",
            on_log=on_log,
        )


class JmsK8sExecutor(ScriptExecutor):
    def __init__(self, spec: QaExec, *, env: QaEnv, worktree: Path | None = None, root: Path | None = None):
        super().__init__(spec, env=env, worktree=worktree, root=root)
        self._mux = SshMux(spec.jms_port, spec.jms_identity())

    def close(self) -> None:
        self._mux.close()

    def _ssh(self, remote: str, *, stdin: bytes | None = None, timeout: int, on_log: LogFn | None = None) -> ExecResult:
        argv = [*self._mux.prefix(), remote]
        return _run_proc(
            argv,
            stdin=stdin,
            timeout=timeout,
            label="jms-k8s",
            on_log=on_log,
        )

    def _auth_fail(self, result: ExecResult) -> None:
        cls = classify_error(result.stderr or result.stdout, ping=True, code=result.code)
        if cls == ExecErrorClass.AUTH:
            raise ExecUnreachable(
                "JMS 认证失败，请核对四段身份（用户@系统用户@节点IP@堡垒机），不要连续重试",
                error_class=ExecErrorClass.AUTH,
            )
        if cls == ExecErrorClass.TIMEOUT:
            raise ExecUnreachable(
                f"jms-k8s 超时: {_clip_diag(result) or 'no output'}",
                error_class=ExecErrorClass.TIMEOUT,
            )
        raise ExecUnreachable(
            f"jms-k8s 通道不通: {_clip_diag(result) or 'no output'}",
            error_class=cls,
        )

    def _pick_pod(self, *, timeout: int) -> str:
        ns = self.spec.namespace
        parts = [
            "kubectl",
            "get",
            "pods",
            "-n",
            ns,
            "--field-selector=status.phase=Running",
        ]
        if self.spec.pod_selector:
            parts.extend(["-l", self.spec.pod_selector])
        parts.extend(["-o", "jsonpath={.items[*].metadata.name}"])
        remote = " ".join(shlex.quote(p) for p in parts)
        r = self._ssh(remote, timeout=timeout)
        if r.code != 0:
            self._auth_fail(r)
        names = [n for n in (r.stdout or "").split() if n]
        if self.spec.pod_pattern:
            try:
                rx = re.compile(self.spec.pod_pattern)
            except re.error as e:
                raise TestRejected(
                    f"pod.pattern 不是合法正则: {e}",
                    error_class=ExecErrorClass.CONFIG,
                ) from e
            names = [n for n in names if rx.search(n)]
        if not names:
            raise ExecUnreachable(
                "没有匹配的 Running pod",
                error_class=ExecErrorClass.POD_NOT_FOUND,
            )
        return names[0]

    def ping(self, *, timeout: int | None = None) -> None:
        t = timeout or self.spec.ping_timeout
        pod = self._pick_pod(timeout=t)
        remote = (
            f"kubectl exec -n {shlex.quote(self.spec.namespace)} {shlex.quote(pod)} "
            f"-c {shlex.quote(self.spec.k8s_container)} -- echo ok"
        )
        r = self._ssh(remote, timeout=t)
        if r.code != 0:
            self._auth_fail(r)

    def logs(
        self,
        needle: str,
        *,
        tail: int = 2000,
        timeout: int | None = None,
        on_log: LogFn | None = None,
    ) -> str:
        """Read-only `kubectl logs` lookup over the JMS channel, filtered locally.

        Filtering on the host (not via a remote `grep`) keeps a failed
        `kubectl logs` from being masked by grep's exit code.
        """
        t = timeout or self.spec.ping_timeout
        pod = self._pick_pod(timeout=t)
        parts = [
            "kubectl",
            "logs",
            "-n",
            self.spec.namespace,
            pod,
            "-c",
            self.spec.k8s_container,
            f"--tail={max(1, int(tail))}",
        ]
        remote = " ".join(shlex.quote(p) for p in parts)
        r = self._ssh(remote, timeout=t, on_log=on_log)
        if r.code != 0:
            self._auth_fail(r)
        text = r.stdout or ""
        if needle:
            low = needle.lower()
            text = "\n".join(ln for ln in text.splitlines() if low in ln.lower())
        return text

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        t = timeout or self.spec.timeout
        pod = self._pick_pod(timeout=min(t, self.spec.ping_timeout))
        runner = _remote_runner(self.spec, script)
        assigns = [f"{k}={v}" for k, v in _qa_assigns(env_extra)]
        cmd = ["env", *assigns, *runner] if assigns else runner
        inner = _cd_wrap(self.spec.workdir, cmd)
        remote_cmd = " ".join(shlex.quote(a) for a in inner)
        # JMS/kubectl 把远端退出码吞成 0（失败也返回 0），用哨兵带回来再还原。
        # 前导 \n 保证哨兵独占一行（否则上一个命令的输出不以换行收尾时会漏读）。
        remote_cmd += (
            f"; __qa_code=$?; printf '\\n{_QA_EXIT_SENTINEL}%s\\n' \"$__qa_code\""
        )
        remote = (
            f"kubectl exec -i -n {shlex.quote(self.spec.namespace)} {shlex.quote(pod)} "
            f"-c {shlex.quote(self.spec.k8s_container)} -- {remote_cmd}"
        )
        r = self._ssh(
            remote,
            stdin=_script_bytes(script),
            timeout=t,
            on_log=on_log,
        )
        r = _restore_remote_exit(r)
        if r.code != 0 and classify_error(r.stderr or r.stdout, code=r.code) == ExecErrorClass.AUTH:
            self._auth_fail(r)
        return r


class RawExecutor(ScriptExecutor):
    def __init__(self, spec: QaExec, *, env: QaEnv, worktree: Path | None = None, root: Path | None = None):
        super().__init__(spec, env=env, worktree=worktree, root=root)

    def _cmd(self, kind: str) -> tuple[list[str], list[str], bool]:
        if self.spec.shell:
            text = self.spec.raw_ping_shell if kind == "ping" else self.spec.raw_run_shell
            rendered, names = _subst_shell(text)
            return [rendered], names, True
        argv = self.spec.raw_ping if kind == "ping" else self.spec.raw_run
        rendered, names = _subst_argv(argv)
        return rendered, names, False

    def ping(self, *, timeout: int | None = None) -> None:
        argv, names, shell = self._cmd("ping")
        r = _run_proc(
            argv,
            timeout=timeout or self.spec.ping_timeout,
            label="raw ping",
            log_names=names,
            shell=shell,
        )
        if r.code != 0:
            raise ExecUnreachable(
                "raw ping 失败",
                error_class=classify_error(r.stderr or r.stdout, ping=True, code=r.code),
            )

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        argv, names, shell = self._cmd("run")
        return _run_proc(
            argv,
            stdin=_script_bytes(script),
            env=_qa_env(env_extra, {}),
            timeout=timeout or self.spec.timeout,
            label=f"raw {script.name}",
            on_log=on_log,
            log_names=names,
            shell=shell,
        )


class DelegateExecutor(ScriptExecutor):
    def __init__(self, spec: QaExec, *, env: QaEnv, worktree: Path | None = None, root: Path | None = None):
        super().__init__(spec, env=env, worktree=worktree, root=root)

    def ping(self, *, timeout: int | None = None) -> None:
        if self.spec.skill:
            hello = Path(tempfile.mkdtemp(prefix="yard-exec-")) / "hello.rb"
            hello.write_text(f'puts "{HELLO}"\n', encoding="utf-8")
            r = self.run(hello, timeout=timeout or self.spec.timeout)
            shutil.rmtree(hello.parent, ignore_errors=True)
            if r.code != 0 or HELLO not in (r.stdout or ""):
                raise ExecUnreachable(
                    "delegate skill ping 失败",
                    error_class=r.error_class or ExecErrorClass.UNREACHABLE,
                )
            return
        argv, names = _subst_argv(self.spec.ping_command)
        r = _run_proc(
            argv,
            timeout=timeout or self.spec.ping_timeout,
            label="delegate ping",
            log_names=names,
        )
        if r.code != 0:
            raise ExecUnreachable(
                "delegate ping 失败",
                error_class=classify_error(r.stderr or r.stdout, ping=True, code=r.code),
            )

    def run(
        self,
        script: Path,
        *,
        on_log: LogFn | None = None,
        timeout: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        tmp = Path(tempfile.mkdtemp(prefix="yard-exec-"))
        result_path = tmp / "exec-result.json"
        try:
            if self.spec.skill:
                return self._run_skill(script, result_path, on_log, timeout, env_extra)
            argv, names = _subst_argv(
                [*self.spec.command, "--script", str(script), "--result", str(result_path)]
            )
            proc = _run_proc(
                argv,
                stdin=_script_bytes(script),
                env=_qa_env(env_extra, {"QA_RESULT_PATH": str(result_path)}),
                timeout=timeout or self.spec.timeout,
                label=f"delegate {script.name}",
                on_log=on_log,
                log_names=names,
            )
            return _read_result_file(result_path, fallback=proc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _run_skill(
        self,
        script: Path,
        result_path: Path,
        on_log: LogFn | None,
        timeout: int | None,
        env_extra: dict[str, str] | None,
    ) -> ExecResult:
        from dev_yard.runners import pi_argv, run_pi_print
        from dev_yard.stages import load_registry, resolve_skill_dir

        root = self.root
        if root is None:
            raise TestRejected("delegate skill 需要工作区 root", error_class=ExecErrorClass.CONFIG)
        skill_dir = resolve_skill_dir(root, self.spec.skill)
        if skill_dir is None:
            raise TestRejected(
                f"找不到 skill {self.spec.skill!r}",
                error_class=ExecErrorClass.CONFIG,
            )
        extra = env_extra or {}
        prompt = DELEGATE_PROMPT.format(
            script=script,
            skill=self.spec.skill,
            result=result_path,
            qa_env=extra.get("QA_ENV", self.env.name),
            jira=extra.get("QA_JIRA", ""),
            case_id=extra.get("QA_CASE_ID", ""),
            kind=extra.get("QA_SCRIPT_KIND", ""),
        )
        spec = load_registry(root).get(self.spec.skill)
        argv = pi_argv(
            root=root,
            bundle=self.spec.skill,
            prompt=None,
            print_mode=True,
            spec=spec,
        )
        if on_log:
            on_log(f"$ pi -p skill={self.spec.skill}")
        code, raw = run_pi_print(argv, root, prompt)
        dummy = ExecResult(code, raw, "", None if code == 0 else ExecErrorClass.RUNNER)
        return _read_result_file(result_path, fallback=dummy)


def _read_result_file(path: Path, *, fallback: ExecResult) -> ExecResult:
    if not path.is_file():
        return ExecResult(
            fallback.code or 1,
            fallback.stdout,
            fallback.stderr or "missing exec-result.json",
            fallback.error_class or ExecErrorClass.RUNNER,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ExecResult(1, "", "exec-result.json 不是合法 JSON", ExecErrorClass.RUNNER)
    if not isinstance(data, dict) or "exit_code" not in data:
        return ExecResult(1, "", "exec-result.json 缺 exit_code", ExecErrorClass.RUNNER)
    try:
        code = int(data.get("exit_code"))
    except (TypeError, ValueError):
        return ExecResult(1, "", "exec-result.json exit_code 不是整数", ExecErrorClass.RUNNER)
    stdout = str(data.get("stdout") or "")
    stderr = str(data.get("stderr") or "")
    err = None if code == 0 else classify_error(stderr, code=code)
    return ExecResult(code, stdout, stderr, err)


def _first_worktree(root: Path, jira: str | None) -> Path | None:
    from dev_yard import paths

    candidates: list[Path] = []
    if jira:
        wt_root = paths.req_dir(root, jira) / "worktrees"
        if wt_root.is_dir():
            candidates.extend(sorted(p for p in wt_root.iterdir() if p.is_dir()))
    if not candidates:
        for req in paths.iter_req_dirs(root):
            wt_root = req / "worktrees"
            if wt_root.is_dir():
                candidates.extend(sorted(p for p in wt_root.iterdir() if p.is_dir()))
            if candidates:
                break
    return candidates[0] if candidates else None


def hello_source(runner: str) -> tuple[str, str]:
    text = runner.lower()
    if "python" in text:
        return "hello.py", f'print("{HELLO}")\n'
    if "node" in text:
        return "hello.js", f'console.log("{HELLO}");\n'
    return "hello.rb", f'puts "{HELLO}"\n'


def fetch_logs(
    root: Path,
    *,
    env_name: str | None,
    jira: str | None,
    request_id: str = "",
    grep: str = "",
    tail: int = 2000,
    timeout: int = 60,
    on_log: LogFn | None = None,
) -> tuple[str, str]:
    """Host-side, read-only 5xx log lookup. Returns (exec.use, redacted text)."""
    from dev_yard.qa_config import load_qa_config

    needle = request_id.strip() or grep.strip()
    if not needle:
        raise TestRejected("需要 --request-id 或 --grep 指定要查的日志关键字")
    cfg = load_qa_config(root, env_name, jira)
    executor = resolve_executor(cfg.env, base_url=cfg.env.base_url, root=root)
    try:
        text = executor.logs(needle, tail=tail, timeout=timeout, on_log=on_log)
        return executor.use, redact_qa_yaml(text)
    finally:
        executor.close()


def check_env(
    root: Path,
    *,
    env_name: str | None = None,
    jira: str | None = None,
    worktree: Path | None = None,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """resolve → ping → hello roundtrip. Raises TestRejected/ExecUnreachable on red."""
    from dev_yard.qa_config import load_qa_config

    cfg = load_qa_config(root, env_name, jira)
    steps: list[dict[str, str]] = []
    if worktree is None:
        worktree = _first_worktree(root, jira)
    executor = resolve_executor(
        cfg.env, base_url=cfg.env.base_url, worktree=worktree, root=root
    )
    if executor.cross_site_warning:
        steps.append(
            {"step": "cross_site", "status": "warn", "detail": executor.cross_site_warning}
        )
        if on_log is not None:
            on_log(executor.cross_site_warning)
    steps.append({"step": "resolve", "status": "ok", "detail": executor.use})
    try:
        executor.ping()
        steps.append({"step": "ping", "status": "ok", "detail": ""})
        name, body = hello_source(_qa_exec(cfg.env).runner or "ruby")
        tmp = Path(tempfile.mkdtemp(prefix="yard-check-"))
        script = tmp / name
        script.write_text(body, encoding="utf-8")
        result = executor.run(
            script,
            on_log=on_log,
            env_extra={
                "QA_ENV": cfg.active_env,
                "QA_JIRA": jira or "",
                "QA_CASE_ID": "check-env",
                "QA_SCRIPT_KIND": "setup",
            },
        )
        shutil.rmtree(tmp, ignore_errors=True)
        if result.code != 0 or HELLO not in (result.stdout or ""):
            raise TestRejected(
                f"hello 回显失败: exit={result.code} stdout={result.stdout!r}",
                error_class=result.error_class or ExecErrorClass.SCRIPT,
            )
        steps.append({"step": "hello", "status": "ok", "detail": HELLO})
        if cfg.env.db_url:
            usql = shutil.which("usql")
            if usql:
                try:
                    r = subprocess.run(
                        [usql, cfg.env.db_url, "-c", "select 1"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if r.returncode != 0:
                        steps.append(
                            {
                                "step": "db",
                                "status": "warn",
                                "detail": "db.url 从本机不通；.sql 造数需宿主能直连该库",
                            }
                        )
                    else:
                        steps.append({"step": "db", "status": "ok", "detail": "usql select 1"})
                except (subprocess.TimeoutExpired, OSError) as e:
                    steps.append(
                        {
                            "step": "db",
                            "status": "warn",
                            "detail": f"db.url 从本机超时/不通（{e}）；.sql 造数需宿主能直连该库",
                        }
                    )
        return {
            "ok": True,
            "env": cfg.active_env,
            "use": executor.use,
            "site": executor.site,
            "steps": steps,
        }
    finally:
        executor.close()
