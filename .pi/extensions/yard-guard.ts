/**
 * dev-yard guard — mechanical enforcement of AGENTS.md「命令安全」.
 *
 * The rule ("never scan `/`; never traverse WSL 9p mounts") is also written into
 * the stage skills, but prose does not stop a model that decides to hunt for a
 * gem. Without this guard a single `find /` parks the pi run in
 * `p9_client_rpc` (uninterruptible sleep) until `YARD_PI_TIMEOUT` expires, and
 * the web console shows a stage stuck on `running` for up to an hour.
 *
 * Loaded by dev-yard for every `pi` run (`--extension`), so it also covers the
 * built-in `find`/`grep` tools, which have no timeout of their own.
 */
import path from "node:path";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

/** Pseudo filesystems: a recursive walk is useless and can block on device I/O. */
const PSEUDO_ROOTS = ["/proc", "/sys", "/dev", "/run"];
/** WSL 9p mounts: traversal blocks in `p9_client_rpc` and ignores signals. */
const NINE_P_ROOTS = ["/mnt", "/usr/lib/wsl"];
const GUARDED_ROOTS = [...NINE_P_ROOTS, ...PSEUDO_ROOTS];
/**
 * Image files pi's `read` attaches as base64. The omniroute gateway counts that
 * base64 as text tokens (one 2598x1386 screenshot ~1MB ~= 780k tokens), so a
 * single `read` of a screenshot blows the model's 500k window and kills the
 * stage with `input_too_large`. Screenshots are background, not source of truth.
 */
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);

/** Commands that always recurse into their path operands. */
const ALWAYS_RECURSIVE = new Set(["find", "fd", "du", "tree", "rg", "ag", "ack"]);
/** Commands whose first non-flag operand is the search pattern, not a path. */
const PATTERN_COMMANDS = new Set(["grep", "rg", "ag", "ack"]);
/** Every command this guard inspects. */
const SEARCH_COMMANDS = new Set([...ALWAYS_RECURSIVE, "grep", "ls"]);
/** `sh -c "<script>"` wrappers whose script must be parsed in turn. */
const SHELLS = new Set(["sh", "bash", "zsh", "dash", "ksh"]);
/** Prefixes that may precede the real command (`sudo find /`). */
const COMMAND_PREFIXES = new Set([
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
]);
/** Flags whose next token is a value, per command (only what changes a root). */
const VALUE_FLAGS: Record<string, Set<string>> = {
  find: new Set([
    "-name", "-iname", "-path", "-ipath", "-type", "-maxdepth", "-mindepth",
    "-size", "-user", "-group", "-perm", "-regex", "-iregex", "-mtime",
    "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-links", "-printf",
    "-newer", "-newermt", "-fstype",
  ]),
  grep: new Set([
    "-e", "-f", "-m", "-A", "-B", "-C", "-d", "--regexp", "--file",
    "--max-count", "--after-context", "--before-context", "--context",
    "--include", "--exclude", "--exclude-dir", "--include-dir",
  ]),
  rg: new Set([
    "-e", "-f", "-m", "-A", "-B", "-C", "-g", "-t", "-T", "-j", "-M",
    "--regexp", "--file", "--max-count", "--max-depth", "--glob", "--type",
    "--type-not", "--threads", "--context", "--after-context",
    "--before-context", "--replace",
  ]),
  ag: new Set(["-G", "-m", "-A", "-B", "-C", "-g", "--ignore", "--file-search-regex"]),
  ack: new Set(["-G", "-m", "-A", "-B", "-C", "-g", "--ignore", "--file-search-regex"]),
  du: new Set(["-d", "-B", "--max-depth", "--block-size", "--exclude", "--time-style"]),
  tree: new Set(["-L", "-P", "-I", "--max-depth", "--filelimit"]),
  ls: new Set([
    "-I", "-w", "-T", "--ignore", "--block-size", "--time-style", "--width",
    "--tabsize", "--format", "--sort", "--quoting-style", "--color",
  ]),
};
/** Flags that themselves supply the pattern, so the next operand is a path. */
const PATTERN_FLAGS = new Set(["-e", "--regexp", "-f", "--file"]);
/**
 * Default bash timeout (seconds) for a pure search command, so a slow scan fails
 * loudly instead of pinning the stage. Matches the ≥300s floor AGENTS.md sets
 * for long-running commands; `bash` tool timeouts are optional and unbounded.
 */
export const DEFAULT_SEARCH_TIMEOUT_SECONDS = 300;

interface Segment {
  /** Operator that ended the previous segment (`&&`, `||`, `|`, `&`, `;`, `\n`). */
  connector: string | null;
  text: string;
}

/** Split on compound operators, ignoring any that sit inside quotes. */
function splitSegments(command: string): Segment[] {
  const out: Segment[] = [];
  let connector: string | null = null;
  let text = "";
  let quote: '"' | "'" | null = null;
  const push = (next: string | null) => {
    out.push({ connector, text });
    connector = next;
    text = "";
  };
  for (let i = 0; i < command.length; i++) {
    const ch = command[i];
    if (quote !== null) {
      text += ch;
      if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      text += ch;
      continue;
    }
    if (ch === "\\") {
      text += ch + (command[i + 1] ?? "");
      i++;
      continue;
    }
    if (ch === "\n" || ch === ";") {
      push(ch);
      continue;
    }
    if (ch === "|" || ch === "&") {
      const doubled = command[i + 1] === ch;
      if (doubled) {
        i++;
      }
      push(doubled ? ch + ch : ch);
      continue;
    }
    text += ch;
  }
  push(null);
  return out;
}

/** Split a segment into unquoted tokens; quoted whitespace stays inside a token. */
function tokenizeSegment(segment: string): string[] {
  const tokens: string[] = [];
  let value = "";
  let started = false;
  let quote: '"' | "'" | null = null;
  const flush = () => {
    if (started) {
      tokens.push(value);
      value = "";
      started = false;
    }
  };
  for (let i = 0; i < segment.length; i++) {
    const ch = segment[i];
    if (quote !== null) {
      if (ch === quote) {
        quote = null;
      } else {
        value += ch;
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
      continue;
    }
    if (ch === "\\") {
      const next = segment[i + 1];
      if (next !== undefined) {
        value += next;
        started = true;
        i++;
      }
      continue;
    }
    if (/\s/.test(ch)) {
      flush();
      continue;
    }
    value += ch;
    started = true;
  }
  flush();
  return tokens;
}

/** Drop shell decorations (`(`, `{`, `!`, `)`, `}`) so `(cd /` reads as `cd`. */
function stripDecorations(token: string): string {
  return token.replace(/^[({!]+/, "").replace(/[)}]+$/, "");
}

function isUnderRoot(root: string, target: string): boolean {
  return target === root || target.startsWith(`${root}/`);
}

/**
 * True when `target` resolves to the filesystem root, a WSL 9p mount, or a
 * pseudo filesystem — i.e. a path whose recursive walk can hang the run.
 */
export function isDangerousPath(target: string, cwd: string): boolean {
  const raw = stripDecorations(target.trim()).replace(/^@/, "");
  if (!raw) {
    return false;
  }
  // `/`, `/*`, `/**` all mean "scan everything"; `.`/`./*` stay scoped to cwd.
  const trimmedPath = raw.replace(/\/+$/, "").replace(/\*+$/, "");
  if (trimmedPath === "" || trimmedPath === "/") {
    return true;
  }
  const abs = path.isAbsolute(trimmedPath)
    ? path.normalize(trimmedPath)
    : path.resolve(cwd, trimmedPath);
  if (abs === "/") {
    return true;
  }
  return GUARDED_ROOTS.some((root) => isUnderRoot(root, abs));
}

/** True when `target` is an image pi's `read` would send as a base64 attachment. */
export function isImagePath(target: string): boolean {
  const raw = stripDecorations(target.trim()).replace(/^@/, "");
  if (!raw) {
    return false;
  }
  return IMAGE_EXTENSIONS.has(path.extname(raw).toLowerCase());
}

function isPrefixToken(token: string): boolean {
  return COMMAND_PREFIXES.has(stripDecorations(token)) || /^\w+=/.test(token);
}

/** Index of `name` when it starts a command (allowing `sudo`-style prefixes). */
function commandStartIndex(tokens: string[], name: string): number {
  for (let i = 0; i < tokens.length; i++) {
    if (path.basename(stripDecorations(tokens[i])) !== name) {
      continue;
    }
    if (i === 0 || tokens.slice(0, i).every(isPrefixToken)) {
      return i;
    }
  }
  return -1;
}

/** The search command of a segment, if any. */
function findCommand(tokens: string[]): { name: string; index: number } | null {
  for (let i = 0; i < tokens.length; i++) {
    const name = path.basename(stripDecorations(tokens[i]));
    if (!SEARCH_COMMANDS.has(name)) {
      continue;
    }
    if (i === 0 || tokens.slice(0, i).every(isPrefixToken)) {
      return { name, index: i };
    }
  }
  return null;
}

/** The script passed to `sh -c "<script>"`, so it can be parsed in turn. */
function shellScript(tokens: string[]): string | null {
  for (let i = 0; i < tokens.length; i++) {
    if (!SHELLS.has(path.basename(stripDecorations(tokens[i])))) {
      continue;
    }
    if (i !== 0 && !tokens.slice(0, i).every(isPrefixToken)) {
      continue;
    }
    const flag = tokens.indexOf("-c", i + 1);
    return flag >= 0 ? (tokens[flag + 1] ?? null) : null;
  }
  return null;
}

function isRecursive(cmd: string, args: string[]): boolean {
  if (ALWAYS_RECURSIVE.has(cmd)) {
    return true;
  }
  if (cmd === "ls") {
    return args.some((a) => a === "--recursive" || /^-[^-]*R/.test(a));
  }
  if (cmd === "grep") {
    return args.some((a) => a === "--recursive" || /^-[^-]*[rR]/.test(a));
  }
  return false;
}

/** Path operands of a search command: pattern and flag values are not roots. */
function rootOperands(cmd: string, args: string[]): string[] {
  const valueFlags = VALUE_FLAGS[cmd] ?? new Set<string>();
  const roots: string[] = [];
  let expectValue = false;
  let patternSeen = !PATTERN_COMMANDS.has(cmd);
  for (const arg of args) {
    if (expectValue) {
      expectValue = false;
      continue;
    }
    if (arg.startsWith("-")) {
      if (valueFlags.has(arg)) {
        expectValue = true;
        if (PATTERN_FLAGS.has(arg)) {
          patternSeen = true;
        }
      }
      continue;
    }
    if (!patternSeen) {
      patternSeen = true;
      continue;
    }
    roots.push(arg);
  }
  return roots;
}

/** A guarded root among the operands of a recursive search, else null. */
function dangerousOperand(cmd: string, args: string[], cwd: string): string | null {
  if (!isRecursive(cmd, args)) {
    return null;
  }
  const roots = rootOperands(cmd, args);
  if (!roots.length) {
    return isDangerousPath(".", cwd) ? cwd : null;
  }
  for (const root of roots) {
    if (isDangerousPath(root, cwd)) {
      return root;
    }
  }
  return null;
}

function segmentDanger(tokens: string[], cwd: string): string | null {
  const script = shellScript(tokens);
  if (script !== null) {
    return dangerousSearchTarget(script, cwd);
  }
  const cmd = findCommand(tokens);
  return cmd === null ? null : dangerousOperand(cmd.name, tokens.slice(cmd.index + 1), cwd);
}

/** The directory `cd`/`pushd` moves to, resolved against `cwd`; else null. */
function cdTarget(tokens: string[], cwd: string): string | null {
  const index = ["cd", "pushd"].map((name) => commandStartIndex(tokens, name)).find((i) => i >= 0);
  if (index === undefined) {
    return null;
  }
  const dest = tokens.slice(index + 1).find((t) => !t.startsWith("-"));
  if (!dest) {
    return null;
  }
  const raw = stripDecorations(dest).replace(/^@/, "");
  if (!raw) {
    return null;
  }
  return path.isAbsolute(raw) ? path.normalize(raw) : path.resolve(cwd, raw);
}

/**
 * The offending path when `command` scans a guarded root, else null.
 *
 * Handles pipelines, `&&`/`||`, `sudo`-style prefixes, `sh -c "<script>"`, and
 * `cd`/`pushd` shifting the cwd. A `cd` carries only into the same `&&`/`;`
 * list: `||`, `|`, `&`, and a closing `)` restore the original directory.
 */
export function dangerousSearchTarget(command: string, cwd: string): string | null {
  let effectiveCwd = cwd;
  for (const { connector, text } of splitSegments(command)) {
    if (connector === "||" || connector === "|" || connector === "&") {
      effectiveCwd = cwd;
    }
    const tokens = tokenizeSegment(text);
    if (!tokens.length) {
      continue;
    }
    const moved = cdTarget(tokens, effectiveCwd);
    if (moved !== null) {
      effectiveCwd = moved;
    }
    const hit = segmentDanger(tokens, effectiveCwd);
    if (hit) {
      return hit;
    }
    if (text.includes(")")) {
      effectiveCwd = cwd;
    }
  }
  return null;
}

/**
 * True when every segment is a search command. Only then is the default timeout
 * injected: a mixed command like `rg foo && bundle exec rspec` must keep the
 * ≥300s headroom AGENTS.md grants slow suites, not inherit a search budget.
 */
export function isPureSearchCommand(command: string): boolean {
  const parsed = splitSegments(command)
    .map((segment) => tokenizeSegment(segment.text))
    .filter((tokens) => tokens.length > 0);
  if (!parsed.length) {
    return false;
  }
  return parsed.every(
    (tokens) => shellScript(tokens) === null && findCommand(tokens) !== null,
  );
}

const REASON =
  "dev-yard guard: 拒绝全盘/9p 扫描（AGENTS.md「命令安全」）。" +
  "WSL 下 /mnt/*、/usr/lib/wsl/* 会阻塞在 p9_client_rpc 且不可中断，" +
  "根目录扫描同样会拖死整个阶段。请把搜索限定在当前 worktree，优先用 rg。";

const IMAGE_REASON =
  "dev-yard guard: 不要用 read 打开图片（png/jpg/jpeg/gif/webp/bmp）。" +
  "read 拿到的图片会进 function_call_output，grok-cli 网关把它当 base64 文本计 token，" +
  "一张截图就能顶爆窗口让整轮失败（input_too_large）。" +
  "需求截图会在阶段 prompt 里以 @ 附件挂进用户消息，直接看那些图。";

export default function yardGuard(pi: ExtensionAPI): void {
  pi.on("tool_call", (event, ctx) => {
    if (isToolCallEventType("bash", event)) {
      const command = event.input.command;
      const offending = dangerousSearchTarget(command, ctx.cwd);
      if (offending) {
        return { block: true, reason: `${REASON}（命中：${offending}）` };
      }
      if (event.input.timeout === undefined && isPureSearchCommand(command)) {
        event.input.timeout = DEFAULT_SEARCH_TIMEOUT_SECONDS;
      }
      return;
    }
    if (isToolCallEventType("read", event)) {
      if (isImagePath(event.input.path ?? "")) {
        return { block: true, reason: `${IMAGE_REASON}（命中：${event.input.path}）` };
      }
      return;
    }
    if (isToolCallEventType("find", event) || isToolCallEventType("grep", event)) {
      const target = event.input.path ?? ".";
      if (isDangerousPath(target, ctx.cwd)) {
        return { block: true, reason: `${REASON}（命中：${target}）` };
      }
    }
  });
}
