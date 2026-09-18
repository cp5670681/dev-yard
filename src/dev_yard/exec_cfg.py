"""qa.yaml `exec` recipe: parse, dump, and the frozen config object."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlparse


def _fail(msg: str) -> NoReturn:
    from dev_yard.qa_config import TestRejected

    raise TestRejected(msg)

USES = ("local", "ssh", "jms-k8s", "docker", "raw", "delegate")
REMOTE_USES = frozenset({"ssh", "jms-k8s", "docker", "raw", "delegate"})
PAYLOADS = ("file", "bundle")
DB_EXECS = ("host", "inherit")

_COMMON = frozenset(
    {"use", "with", "payload", "parallel", "allow_cross_site", "timeout", "ping_timeout"}
)
_USE_KEYS = {
    "local": _COMMON,
    "ssh": _COMMON,
    "jms-k8s": _COMMON,
    "docker": _COMMON,
    "raw": _COMMON | {"run", "ping", "shell"},
    "delegate": _COMMON | {"command", "ping", "skill"},
}
_WITH_KEYS = {
    "local": frozenset({"runner", "timeout", "workdir", "sql_runner"}),
    "ssh": frozenset(
        {"target", "host", "user", "port", "workdir", "runner", "timeout", "sql_runner"}
    ),
    "docker": frozenset({"container", "runner", "workdir", "timeout", "sql_runner"}),
    "jms-k8s": frozenset(
        {
            "jms",
            "default_node",
            "nodes",
            "namespace",
            "container",
            "runner",
            "workdir",
            "pod",
            "timeout",
            "sql_runner",
        }
    ),
    "raw": frozenset(),
    "delegate": frozenset(),
}


def _blank(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, field: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        _fail(f"qa.yaml {field} 必须是整数")


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "yes", "1", "on"}


def _unexpected(got: dict[str, Any], allowed: frozenset[str], field: str) -> None:
    extra = sorted(str(k) for k in got if k not in allowed)
    if extra:
        _fail(f"qa.yaml {field} 含有未知键: {', '.join(extra)}")


def _own_hosts() -> set[str]:
    found = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    try:
        hn = socket.gethostname()
        found.add(hn.lower())
        fqdn = socket.getfqdn()
        if fqdn:
            found.add(fqdn.lower())
        for info in socket.getaddrinfo(hn, None):
            found.add(str(info[4][0]).lower().strip("[]"))
    except OSError:
        pass
    return found


def is_local_base_url(url: str) -> bool:
    """Loopback or this machine's own addresses. Other RFC1918 hosts stay remote."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    h = host.lower().strip("[]")
    if h in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or h.startswith("127."):
        return True
    return h in _own_hosts()


def _as_argv(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        if not all(isinstance(x, (str, int, float)) for x in value):
            _fail(f"qa.yaml {field} 必须是字符串列表")
        return tuple(str(x) for x in value)
    _fail(f"qa.yaml {field} 必须是 argv 列表（shell: true 时才用字符串）")


def _as_str_or_argv(value: Any, field: str, *, shell: bool) -> tuple[tuple[str, ...], str]:
    if value is None:
        return (), ""
    if shell:
        if not isinstance(value, str) or not value.strip():
            _fail(f"qa.yaml {field} 在 shell: true 时必须是非空字符串")
        return (), value
    return _as_argv(value, field), ""


@dataclass(frozen=True)
class QaExec:
    use: str = "local"
    site: str = "local"
    payload: str = "file"
    parallel: bool = False
    allow_cross_site: bool = False
    timeout: int = 300
    ping_timeout: int = 30
    db_exec: str = "host"
    runner: str = ""
    workdir: str = ""
    sql_runner: str = ""
    ssh_target: str = ""
    ssh_port: int = 22
    container: str = ""
    jms_host: str = ""
    jms_port: int = 22222
    jms_user: str = ""
    default_node: str = ""
    nodes: tuple[tuple[str, str], ...] = ()
    namespace: str = ""
    k8s_container: str = ""
    pod_selector: str = ""
    pod_pattern: str = ""
    raw_run: tuple[str, ...] = ()
    raw_ping: tuple[str, ...] = ()
    raw_run_shell: str = ""
    raw_ping_shell: str = ""
    shell: bool = False
    command: tuple[str, ...] = ()
    ping_command: tuple[str, ...] = ()
    skill: str = ""

    @property
    def node_ip(self) -> str:
        return dict(self.nodes).get(self.default_node, "")

    def jms_identity(self) -> str:
        ip = self.node_ip
        return f"{self.jms_user}@{ip}@{self.jms_host}"


def default_exec(*, runner: str = "", db_exec: str = "host") -> QaExec:
    return QaExec(use="local", site="local", runner=runner, db_exec=db_exec)


def parse_exec(env_name: str, raw_env: dict[str, Any], *, script_runner: str) -> QaExec:
    """Build QaExec from one env mapping. Missing exec + script.runner = local."""
    field = f"envs.{env_name}"
    db = raw_env.get("db") if isinstance(raw_env.get("db"), dict) else {}
    db_exec = _blank(db.get("exec")) or "host"
    if db_exec not in DB_EXECS:
        _fail(f"qa.yaml {field}.db.exec 只能是 host 或 inherit")
    raw = raw_env.get("exec")
    if raw is None:
        return default_exec(runner=script_runner, db_exec=db_exec)
    if not isinstance(raw, dict):
        _fail(f"qa.yaml {field}.exec 必须是映射")
    use = _blank(raw.get("use"))
    if not use:
        _fail(f"qa.yaml {field}.exec.use 必填")
    if use not in USES:
        _fail(f"qa.yaml {field}.exec.use 必须是 {', '.join(USES)} 之一")
    _unexpected(raw, _USE_KEYS[use], f"{field}.exec")
    payload = _blank(raw.get("payload")) or "file"
    if payload not in PAYLOADS:
        _fail(f"qa.yaml {field}.exec.payload 只能是 file 或 bundle")
    with_raw = raw.get("with")
    if with_raw is None:
        with_raw = {}
    if not isinstance(with_raw, dict):
        _fail(f"qa.yaml {field}.exec.with 必须是映射")
    _unexpected(with_raw, _WITH_KEYS[use], f"{field}.exec.with")
    timeout = _int(with_raw.get("timeout", raw.get("timeout")), f"{field}.exec.timeout", 300)
    ping_timeout = _int(raw.get("ping_timeout"), f"{field}.exec.ping_timeout", 30)
    runner_with = _blank(with_raw.get("runner"))
    if runner_with and script_runner and runner_with != script_runner:
        _fail(
            f"qa.yaml {field}: exec.with.runner 与 script.runner 不一致 "
            f"({runner_with!r} vs {script_runner!r})"
        )
    runner = runner_with or script_runner
    sql_runner = _blank(with_raw.get("sql_runner"))
    workdir = _blank(with_raw.get("workdir"))
    site = "local" if use == "local" else "remote"
    kw: dict[str, Any] = dict(
        use=use,
        site=site,
        payload=payload,
        parallel=_bool(raw.get("parallel"), False),
        allow_cross_site=_bool(raw.get("allow_cross_site"), False),
        timeout=timeout,
        ping_timeout=ping_timeout,
        db_exec=db_exec,
        runner=runner,
        workdir=workdir,
        sql_runner=sql_runner,
    )
    if use == "ssh":
        target = _blank(with_raw.get("target"))
        host = _blank(with_raw.get("host"))
        user = _blank(with_raw.get("user"))
        if not target:
            if host and user:
                target = f"{user}@{host}"
            elif host:
                target = host
        if not target:
            _fail(f"qa.yaml {field}.exec.with 需要 target（或 host/user）")
        if not runner:
            _fail(f"qa.yaml {field}.exec.with.runner 必填")
        kw["ssh_target"] = target
        kw["ssh_port"] = _int(with_raw.get("port"), f"{field}.exec.with.port", 22)
    elif use == "docker":
        container = _blank(with_raw.get("container"))
        if not container:
            _fail(f"qa.yaml {field}.exec.with.container 必填")
        if not runner:
            _fail(f"qa.yaml {field}.exec.with.runner 必填")
        kw["container"] = container
    elif use == "jms-k8s":
        jms = with_raw.get("jms") if isinstance(with_raw.get("jms"), dict) else None
        if jms is None:
            _fail(f"qa.yaml {field}.exec.with.jms 必填（host/port/user）")
        _unexpected(jms, frozenset({"host", "port", "user"}), f"{field}.exec.with.jms")
        jms_host = _blank(jms.get("host"))
        jms_user = _blank(jms.get("user"))
        if not jms_host or not jms_user:
            _fail(f"qa.yaml {field}.exec.with.jms 需要 host 与 user")
        if "@" not in jms_user:
            _fail(
                f"qa.yaml {field}.exec.with.jms.user 应是前两段（如 alice@root）"
            )
        nodes_raw = with_raw.get("nodes")
        if not isinstance(nodes_raw, dict) or not nodes_raw:
            _fail(f"qa.yaml {field}.exec.with.nodes 必须是 节点名→IP 映射")
        nodes = tuple((str(k), str(v).strip()) for k, v in nodes_raw.items())
        default_node = _blank(with_raw.get("default_node"))
        if not default_node:
            _fail(f"qa.yaml {field}.exec.with.default_node 必填")
        if default_node not in dict(nodes):
            _fail(
                f"qa.yaml {field}.exec.with.default_node {default_node!r} 不在 nodes 里"
            )
        ns = _blank(with_raw.get("namespace"))
        k8s_container = _blank(with_raw.get("container"))
        if not ns or not k8s_container:
            _fail(
                f"qa.yaml {field}.exec.with 需要 namespace 与 container"
            )
        if not runner:
            _fail(f"qa.yaml {field}.exec.with.runner 必填")
        pod = with_raw.get("pod") if isinstance(with_raw.get("pod"), dict) else {}
        _unexpected(pod, frozenset({"selector", "pattern"}), f"{field}.exec.with.pod")
        selector = _blank(pod.get("selector"))
        pattern = _blank(pod.get("pattern"))
        if not selector and not pattern:
            _fail(
                f"qa.yaml {field}.exec.with.pod 需要 selector 或 pattern"
            )
        kw.update(
            jms_host=jms_host,
            jms_port=_int(jms.get("port"), f"{field}.exec.with.jms.port", 22222),
            jms_user=jms_user,
            default_node=default_node,
            nodes=nodes,
            namespace=ns,
            k8s_container=k8s_container,
            pod_selector=selector,
            pod_pattern=pattern,
        )
    elif use == "raw":
        shell = _bool(raw.get("shell"), False)
        run_argv, run_shell = _as_str_or_argv(raw.get("run"), f"{field}.exec.run", shell=shell)
        ping_argv, ping_shell = _as_str_or_argv(
            raw.get("ping"), f"{field}.exec.ping", shell=shell
        )
        if shell:
            if not run_shell or not ping_shell:
                _fail(f"qa.yaml {field}.exec 在 shell: true 时必须同时配 run 与 ping")
            for label, text in (("run", run_shell), ("ping", ping_shell)):
                _reject_raw_text(text, f"{field}.exec.{label}")
        else:
            if not run_argv or not ping_argv:
                _fail(f"qa.yaml {field}.exec 必须同时配 run 与 ping（argv 列表）")
            for label, argv in (("run", run_argv), ("ping", ping_argv)):
                _reject_raw_argv(argv, f"{field}.exec.{label}")
        kw.update(
            shell=shell,
            raw_run=run_argv,
            raw_ping=ping_argv,
            raw_run_shell=run_shell,
            raw_ping_shell=ping_shell,
        )
    elif use == "delegate":
        command = _as_argv(raw.get("command"), f"{field}.exec.command") if raw.get("command") is not None else ()
        skill = _blank(raw.get("skill"))
        if bool(command) == bool(skill):
            _fail(
                f"qa.yaml {field}.exec 的 delegate 必须只配 command 或只配 skill"
            )
        ping_cmd = ()
        if command:
            if raw.get("ping") is None:
                _fail(f"qa.yaml {field}.exec.ping 在 command 模式下必填")
            ping_cmd = _as_argv(raw.get("ping"), f"{field}.exec.ping")
            if not ping_cmd:
                _fail(f"qa.yaml {field}.exec.ping 不能为空")
        kw.update(command=command, ping_command=ping_cmd, skill=skill)
    return QaExec(**kw)


def _reject_raw_argv(argv: tuple[str, ...], field: str) -> None:
    for item in argv:
        if "{script}" in item:
            _fail(f"qa.yaml {field} 不得包含 {{script}}，stdin 由宿主提供")
        if item in {"<", ">", ">>"} or item.startswith("<") or item.startswith(">"):
            _fail(f"qa.yaml {field} 不得含重定向，stdin 由宿主提供")


def _reject_raw_text(text: str, field: str) -> None:
    if "{script}" in text:
        _fail(f"qa.yaml {field} 不得包含 {{script}}，stdin 由宿主提供")
    if "<" in text or ">" in text:
        _fail(f"qa.yaml {field} 不得含重定向符号，stdin 由宿主提供")


def exec_to_raw(ex: QaExec, *, include_runner_sugar: bool = False) -> dict[str, Any] | None:
    """YAML mapping for `exec:`. local+defaults can be omitted."""
    if ex.use == "local" and not ex.allow_cross_site and ex.payload == "file" and not ex.parallel:
        if include_runner_sugar:
            return None
        return {"use": "local", **({"with": {"runner": ex.runner}} if ex.runner else {})}
    out: dict[str, Any] = {"use": ex.use}
    if ex.payload != "file":
        out["payload"] = ex.payload
    if ex.parallel:
        out["parallel"] = True
    if ex.allow_cross_site:
        out["allow_cross_site"] = True
    if ex.timeout != 300:
        out["timeout"] = ex.timeout
    if ex.ping_timeout != 30:
        out["ping_timeout"] = ex.ping_timeout
    with_out: dict[str, Any] = {}
    if ex.use in {"local", "ssh", "docker", "jms-k8s"} and ex.runner:
        with_out["runner"] = ex.runner
    if ex.workdir:
        with_out["workdir"] = ex.workdir
    if ex.sql_runner:
        with_out["sql_runner"] = ex.sql_runner
    if ex.use == "ssh":
        with_out["target"] = ex.ssh_target
        if ex.ssh_port != 22:
            with_out["port"] = ex.ssh_port
    elif ex.use == "docker":
        with_out["container"] = ex.container
    elif ex.use == "jms-k8s":
        with_out["jms"] = {
            "host": ex.jms_host,
            "port": ex.jms_port,
            "user": ex.jms_user,
        }
        with_out["default_node"] = ex.default_node
        with_out["nodes"] = {k: v for k, v in ex.nodes}
        with_out["namespace"] = ex.namespace
        with_out["container"] = ex.k8s_container
        pod: dict[str, str] = {}
        if ex.pod_selector:
            pod["selector"] = ex.pod_selector
        if ex.pod_pattern:
            pod["pattern"] = ex.pod_pattern
        if pod:
            with_out["pod"] = pod
    if with_out:
        out["with"] = with_out
    if ex.use == "raw":
        if ex.shell:
            out["shell"] = True
            out["run"] = ex.raw_run_shell
            out["ping"] = ex.raw_ping_shell
        else:
            out["run"] = list(ex.raw_run)
            out["ping"] = list(ex.raw_ping)
    if ex.use == "delegate":
        if ex.command:
            out["command"] = list(ex.command)
            out["ping"] = list(ex.ping_command)
        if ex.skill:
            out["skill"] = ex.skill
    return out


def exec_payload(raw_env: Any) -> dict[str, Any]:
    """Form projection: always a complete object, even when yaml has no exec."""
    env = raw_env if isinstance(raw_env, dict) else {}
    script = env.get("script") if isinstance(env.get("script"), dict) else {}
    db = env.get("db") if isinstance(env.get("db"), dict) else {}
    from dev_yard.qa_config import TestRejected

    parse_error = ""
    try:
        ex = parse_exec("form", env, script_runner=_blank(script.get("runner")))
    except TestRejected as e:
        ex = default_exec(runner=_blank(script.get("runner")))
        parse_error = str(e)
    nodes_text = "\n".join(f"{k}: {v}" for k, v in ex.nodes)
    run_text = (
        ex.raw_run_shell
        if ex.shell
        else "\n".join(ex.raw_run)
        if ex.raw_run
        else " ".join(ex.command)
    )
    ping_text = (
        ex.raw_ping_shell
        if ex.shell
        else "\n".join(ex.raw_ping)
        if ex.raw_ping
        else "\n".join(ex.ping_command)
    )
    return {
        "use": ex.use,
        "payload": ex.payload,
        "parallel": ex.parallel,
        "allow_cross_site": ex.allow_cross_site,
        "timeout": ex.timeout,
        "runner": ex.runner,
        "workdir": ex.workdir,
        "sql_runner": ex.sql_runner,
        "target": ex.ssh_target,
        "port": ex.ssh_port if ex.use == "ssh" else ex.jms_port,
        "container": ex.container or ex.k8s_container,
        "jms_host": ex.jms_host,
        "jms_port": ex.jms_port,
        "jms_user": ex.jms_user,
        "default_node": ex.default_node,
        "nodes_text": nodes_text,
        "namespace": ex.namespace,
        "pod_selector": ex.pod_selector,
        "pod_pattern": ex.pod_pattern,
        "shell": ex.shell,
        "run_text": run_text if ex.use in {"raw", "delegate"} else "",
        "ping_text": ping_text if ex.use in {"raw", "delegate"} else "",
        "skill": ex.skill,
        "db_exec": _blank(db.get("exec")) or "host",
        "parse_error": parse_error,
    }


def exec_from_form(raw: Any, field: str) -> dict[str, Any] | None:
    """Form exec object → yaml `exec` mapping (or None to omit)."""
    if not isinstance(raw, dict):
        return None
    use = _blank(raw.get("use")) or "local"
    if use not in USES:
        _fail(f"{field}.use 必须是 {', '.join(USES)} 之一")
    out: dict[str, Any] = {"use": use}
    payload = _blank(raw.get("payload")) or "file"
    if payload != "file":
        out["payload"] = payload
    if _bool(raw.get("parallel")):
        out["parallel"] = True
    if _bool(raw.get("allow_cross_site")):
        out["allow_cross_site"] = True
    timeout = _int(raw.get("timeout"), f"{field}.timeout", 300)
    if timeout != 300:
        out["timeout"] = timeout
    with_out: dict[str, Any] = {}
    runner = _blank(raw.get("runner"))
    workdir = _blank(raw.get("workdir"))
    sql_runner = _blank(raw.get("sql_runner"))
    if use in {"local", "ssh", "docker", "jms-k8s"}:
        if runner:
            with_out["runner"] = runner
        if workdir:
            with_out["workdir"] = workdir
        if sql_runner:
            with_out["sql_runner"] = sql_runner
    if use == "ssh":
        target = _blank(raw.get("target"))
        if target:
            with_out["target"] = target
        port = _int(raw.get("port"), f"{field}.port", 22)
        if port != 22:
            with_out["port"] = port
    elif use == "docker":
        container = _blank(raw.get("container"))
        if container:
            with_out["container"] = container
    elif use == "jms-k8s":
        jms = {
            "host": _blank(raw.get("jms_host")),
            "port": _int(raw.get("jms_port"), f"{field}.jms_port", 22222),
            "user": _blank(raw.get("jms_user")),
        }
        if any(jms.values()):
            with_out["jms"] = jms
        node = _blank(raw.get("default_node"))
        if node:
            with_out["default_node"] = node
        nodes = _parse_nodes_text(_blank(raw.get("nodes_text")))
        if nodes:
            with_out["nodes"] = nodes
        ns = _blank(raw.get("namespace"))
        if ns:
            with_out["namespace"] = ns
        container = _blank(raw.get("container"))
        if container:
            with_out["container"] = container
        pod: dict[str, str] = {}
        if _blank(raw.get("pod_selector")):
            pod["selector"] = _blank(raw.get("pod_selector"))
        if _blank(raw.get("pod_pattern")):
            pod["pattern"] = _blank(raw.get("pod_pattern"))
        if pod:
            with_out["pod"] = pod
    if with_out:
        out["with"] = with_out
    if use == "raw":
        shell = _bool(raw.get("shell"))
        if shell:
            out["shell"] = True
        run_text = _blank(raw.get("run_text"))
        ping_text = _blank(raw.get("ping_text"))
        out["run"] = run_text if shell else _lines_argv(run_text)
        out["ping"] = ping_text if shell else _lines_argv(ping_text)
    if use == "delegate":
        skill = _blank(raw.get("skill"))
        if skill:
            out["skill"] = skill
        else:
            out["command"] = _lines_argv(_blank(raw.get("run_text")))
            out["ping"] = _lines_argv(_blank(raw.get("ping_text")))
    if use == "local" and list(out.keys()) == ["use"]:
        return {"use": "local"}
    return out


def _lines_argv(text: str) -> list[str]:
    if not text:
        return []
    if "\n" in text:
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    import shlex

    return shlex.split(text)


def _parse_nodes_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        line = ln.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            k, v = parts[0], parts[1]
        out[k.strip()] = v.strip()
    return out
