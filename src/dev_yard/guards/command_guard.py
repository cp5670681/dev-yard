from __future__ import annotations

import os
import re
from pathlib import Path

PSEUDO_ROOTS = ["/proc", "/sys", "/dev", "/run"]
NINE_P_ROOTS = ["/mnt", "/usr/lib/wsl"]
GUARDED_ROOTS = tuple(NINE_P_ROOTS + PSEUDO_ROOTS)
IMAGE_EXTENSIONS = frozenset([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"])

ALWAYS_RECURSIVE = frozenset(["find", "fd", "du", "tree", "rg", "ag", "ack"])
PATTERN_COMMANDS = frozenset(["grep", "rg", "ag", "ack"])
SEARCH_COMMANDS = frozenset(list(ALWAYS_RECURSIVE) + ["grep", "ls"])
SHELLS = frozenset(["sh", "bash", "zsh", "dash", "ksh"])
COMMAND_PREFIXES = frozenset([
    "sudo",
    "command",
    "builtin",
    "exec",
    "time",
    "nice",
    "nohup",
    "env",
    "then",
    "do",
])

VALUE_FLAGS: dict[str, frozenset[str]] = {
    "find": frozenset([
        "-name", "-iname", "-path", "-ipath", "-type", "-maxdepth", "-mindepth",
        "-size", "-user", "-group", "-perm", "-regex", "-iregex", "-mtime",
        "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-links", "-printf",
        "-newer", "-newermt", "-fstype",
    ]),
    "grep": frozenset([
        "-e", "-f", "-m", "-A", "-B", "-C", "-d", "--regexp", "--file",
        "--max-count", "--after-context", "--before-context", "--context",
        "--include", "--exclude", "--exclude-dir", "--include-dir",
    ]),
    "rg": frozenset([
        "-e", "-f", "-m", "-A", "-B", "-C", "-g", "-t", "-T", "-j", "-M",
        "--regexp", "--file", "--max-count", "--max-depth", "--glob", "--type",
        "--type-not", "--threads", "--context", "--after-context",
        "--before-context", "--replace",
    ]),
    "ag": frozenset(["-G", "-m", "-A", "-B", "-C", "-g", "--ignore", "--file-search-regex"]),
    "ack": frozenset(["-G", "-m", "-A", "-B", "-C", "-g", "--ignore", "--file-search-regex"]),
    "du": frozenset(["-d", "-B", "--max-depth", "--block-size", "--exclude", "--time-style"]),
    "tree": frozenset(["-L", "-P", "-I", "--max-depth", "--filelimit"]),
    "ls": frozenset([
        "-I", "-w", "-T", "--ignore", "--block-size", "--time-style", "--width",
        "--tabsize", "--format", "--sort", "--quoting-style", "--color",
    ]),
}
PATTERN_FLAGS = frozenset(["-e", "--regexp", "-f", "--file"])
DEFAULT_SEARCH_TIMEOUT_SECONDS = 300


class Segment:
    def __init__(self, connector: str | None, text: str) -> None:
        self.connector = connector
        self.text = text


def split_segments(command: str) -> list[Segment]:
    out: list[Segment] = []
    connector: str | None = None
    text = ""
    quote: str | None = None
    i = 0
    n = len(command)

    def push(next_conn: str | None) -> None:
        nonlocal text, connector
        out.append(Segment(connector, text))
        connector = next_conn
        text = ""

    while i < n:
        ch = command[i]
        if quote is not None:
            text += ch
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            text += ch
            i += 1
            continue

        if ch == "\\":
            text += ch
            if i + 1 < n:
                text += command[i + 1]
                i += 1
            i += 1
            continue

        if ch in ("\n", ";"):
            push(ch)
            i += 1
            continue

        if ch in ("|", "&"):
            doubled = (i + 1 < n and command[i + 1] == ch)
            if doubled:
                i += 1
            push(ch * 2 if doubled else ch)
            i += 1
            continue

        text += ch
        i += 1

    push(None)
    return out


def tokenize_segment(segment: str) -> list[str]:
    tokens: list[str] = []
    value = ""
    started = False
    quote: str | None = None
    i = 0
    n = len(segment)

    def flush() -> None:
        nonlocal value, started
        if started:
            tokens.append(value)
            value = ""
            started = False

    while i < n:
        ch = segment[i]
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                value += ch
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            started = True
            i += 1
            continue

        if ch == "\\":
            if i + 1 < n:
                value += segment[i + 1]
                started = True
                i += 1
            i += 1
            continue

        if ch.isspace():
            flush()
            i += 1
            continue

        value += ch
        started = True
        i += 1

    flush()
    return tokens


def strip_decorations(token: str) -> str:
    return re.sub(r"^[({!]+", "", re.sub(r"[)}]+$", "", token))


def is_under_root(root: str, target: str) -> bool:
    return target == root or target.startswith(f"{root}/")


def is_dangerous_path(target: str, cwd: str) -> bool:
    raw = strip_decorations(target.strip()).removeprefix("@")
    if not raw:
        return False
    trimmed = re.sub(r"/+$", "", re.sub(r"\*+$", "", raw))
    if trimmed == "" or trimmed == "/":
        return True
    try:
        if os.path.isabs(trimmed):
            abs_path = os.path.normpath(trimmed)
        else:
            abs_path = str((Path(cwd) / trimmed).resolve())
    except (ValueError, OSError):
        abs_path = trimmed
    if abs_path == "/":
        return True
    return any(is_under_root(root, abs_path) for root in GUARDED_ROOTS)


def is_image_path(target: str) -> bool:
    raw = strip_decorations(target.strip()).removeprefix("@")
    if not raw:
        return False
    _, ext = os.path.splitext(raw)
    return ext.lower() in IMAGE_EXTENSIONS


def is_prefix_token(token: str) -> bool:
    return strip_decorations(token) in COMMAND_PREFIXES or bool(re.match(r"^\w+=", token))


def command_start_index(tokens: list[str], name: str) -> int:
    for i, tok in enumerate(tokens):
        if os.path.basename(strip_decorations(tok)) != name:
            continue
        if i == 0 or all(is_prefix_token(t) for t in tokens[:i]):
            return i
    return -1


def find_command(tokens: list[str]) -> tuple[str, int] | None:
    for i, tok in enumerate(tokens):
        name = os.path.basename(strip_decorations(tok))
        if name not in SEARCH_COMMANDS:
            continue
        if i == 0 or all(is_prefix_token(t) for t in tokens[:i]):
            return name, i
    return None


def shell_script(tokens: list[str]) -> str | None:
    for i, tok in enumerate(tokens):
        if os.path.basename(strip_decorations(tok)) not in SHELLS:
            continue
        if i != 0 and not all(is_prefix_token(t) for t in tokens[:i]):
            continue
        try:
            flag = tokens.index("-c", i + 1)
            return tokens[flag + 1] if flag + 1 < len(tokens) else None
        except ValueError:
            return None
    return None


def is_recursive(cmd: str, args: list[str]) -> bool:
    if cmd in ALWAYS_RECURSIVE:
        return True
    if cmd == "ls":
        return any(a == "--recursive" or bool(re.match(r"^-[^-]*R", a)) for a in args)
    if cmd == "grep":
        return any(a == "--recursive" or bool(re.match(r"^-[^-]*[rR]", a)) for a in args)
    return False


def root_operands(cmd: str, args: list[str]) -> list[str]:
    value_flags = VALUE_FLAGS.get(cmd, frozenset())
    roots: list[str] = []
    expect_value = False
    pattern_seen = (cmd not in PATTERN_COMMANDS)
    for arg in args:
        if expect_value:
            expect_value = False
            continue
        if arg.startswith("-"):
            if arg in value_flags:
                expect_value = True
                if arg in PATTERN_FLAGS:
                    pattern_seen = True
            continue
        if not pattern_seen:
            pattern_seen = True
            continue
        roots.append(arg)
    return roots


def dangerous_operand(cmd: str, args: list[str], cwd: str) -> str | None:
    if not is_recursive(cmd, args):
        return None
    roots = root_operands(cmd, args)
    if not roots:
        return cwd if is_dangerous_path(".", cwd) else None
    for root in roots:
        if is_dangerous_path(root, cwd):
            return root
    return None


def segment_danger(tokens: list[str], cwd: str) -> str | None:
    script = shell_script(tokens)
    if script is not None:
        return dangerous_search_target(script, cwd)
    found = find_command(tokens)
    if found is None:
        return None
    cmd_name, cmd_idx = found
    return dangerous_operand(cmd_name, tokens[cmd_idx + 1:], cwd)


def cd_target(tokens: list[str], cwd: str) -> str | None:
    index: int | None = None
    for name in ("cd", "pushd"):
        idx = command_start_index(tokens, name)
        if idx >= 0:
            index = idx
            break
    if index is None:
        return None
    dest: str | None = None
    for t in tokens[index + 1:]:
        if not t.startswith("-"):
            dest = t
            break
    if not dest:
        return None
    raw = strip_decorations(dest).removeprefix("@")
    if not raw:
        return None
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return str((Path(cwd) / raw).resolve())


def dangerous_search_target(command: str, cwd: str) -> str | None:
    effective_cwd = cwd
    for seg in split_segments(command):
        if seg.connector in ("||", "|", "&"):
            effective_cwd = cwd
        tokens = tokenize_segment(seg.text)
        if not tokens:
            continue
        moved = cd_target(tokens, effective_cwd)
        if moved is not None:
            effective_cwd = moved
        hit = segment_danger(tokens, effective_cwd)
        if hit:
            return hit
        if ")" in seg.text:
            effective_cwd = cwd
    return None


def is_pure_search_command(command: str) -> bool:
    parsed = [
        tokenize_segment(seg.text)
        for seg in split_segments(command)
        if tokenize_segment(seg.text)
    ]
    if not parsed:
        return False
    return all(shell_script(tokens) is None and find_command(tokens) is not None for tokens in parsed)


REASON = (
    "dev-yard guard: 拒绝全盘/9p 扫描（AGENTS.md「命令安全」）。"
    "WSL 下 /mnt/*、/usr/lib/wsl/* 会阻塞在 p9_client_rpc 且不可中断，"
    "根目录扫描同样会拖死整个阶段。请把搜索限定在当前 worktree，优先用 rg。"
)

IMAGE_REASON = (
    "dev-yard guard: 不要用 read 打开图片（png/jpg/jpeg/gif/webp/bmp）。"
    "read 拿到的图片会进 function_call_output，grok-cli 网关把它当 base64 文本计 token，"
    "一张截图就能顶爆窗口让整轮失败（input_too_large）。"
    "需求截图会在阶段 prompt 里以 @ 附件挂进用户消息，直接看那些图。"
)


def validate_tool_call(tool_name: str, arguments: dict, cwd: str) -> tuple[bool, str | None]:
    """Validate a tool call against dev-yard security constraints.

    Returns:
        (is_allowed, error_reason)
    """
    if tool_name == "bash":
        cmd = str(arguments.get("command") or "")
        offending = dangerous_search_target(cmd, cwd)
        if offending:
            return False, f"{REASON}（命中：{offending}）"
        if "timeout" not in arguments and is_pure_search_command(cmd):
            arguments["timeout"] = DEFAULT_SEARCH_TIMEOUT_SECONDS
        return True, None

    if tool_name == "read":
        path = str(arguments.get("path") or arguments.get("file_path") or "")
        if is_image_path(path):
            return False, f"{IMAGE_REASON}（命中：{path}）"
        return True, None

    if tool_name in ("find", "grep"):
        target = str(arguments.get("path") or ".")
        if is_dangerous_path(target, cwd):
            return False, f"{REASON}（命中：{target}）"
        return True, None

    return True, None

