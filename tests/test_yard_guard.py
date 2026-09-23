"""Node-side tests for the pi safety extension `.pi/extensions/yard-guard.ts`.

The guard runs inside pi, so its logic is exercised through pi's own jiti loader
with the same module alias pi uses. Skipped when node or a global pi install is
unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dev_yard.stages import resolve_extension_path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (command, cwd) -> blocked?
BASH_CASES: list[tuple[str, str, bool]] = [
    ("find / -name x", ".", True),
    ("find / -path /proc -prune -o -type d -name 'act_form-0.4.0' -print", ".", True),
    ("find /mnt/c -name x", ".", True),
    ("find /usr/lib/wsl -name x", ".", True),
    ("grep -rn foo /", ".", True),
    ("rg foo / --hidden", ".", True),
    ("du -sh /", ".", True),
    ("ls -R /", ".", True),
    ("ls -lR /mnt/c", ".", True),
    ("ls -laR /", ".", True),
    ("cd /; ls -lR", ".", True),
    ('bash -c "find / -name x"', ".", True),
    ("sudo find / -name x", ".", True),
    ("cd / && find .", ".", True),
    ("cd / && find", ".", True),
    ("cd /mnt/c && rg foo .", ".", True),
    ("(cd / && find .)", ".", True),
    ("pushd / && find .", ".", True),
    ("rg 'foo && bar' /", ".", True),
    ('grep -R "a && b" /mnt/c', ".", True),
    ("find . -name '*.rb'", ".", False),
    ("find ./ -type d -name act_form*", ".", False),
    ("rg -n 'def foo' app lib", ".", False),
    ("find /home/chengpeng/projects -name x", ".", False),
    ("grep -rn foo /etc/hosts", ".", False),
    ("ls -R", ".", False),
    ("ls -r /", ".", False),
    ("gem contents act_form", ".", False),
    ("bundle exec rspec spec/models", ".", False),
    ("cd /tmp && find . -name x", ".", False),
    ("rg foo && bundle exec rspec", ".", False),
    # Regression: a quoted one-liner with inner spaces must not blow the stack.
    ("node -e \"console.log('a b')\"", ".", False),
    ("python -c \"print('hello world')\"", ".", False),
    # Pattern-only operands and the AGENTS.md `-prune` form are not roots.
    ("rg /mnt/c", ".", False),
    ("grep -n x /dev/null", ".", False),
    ("find /tmp -name x -o -path /mnt -prune", ".", False),
    # A `cd` does not survive `||`, a closing `)`, or a subshell exit.
    ("cd /mnt/c || find .", ".", False),
    ("(cd / && true); find .", ".", False),
]

# path -> dangerous?
PATH_CASES: list[tuple[str, bool]] = [
    ("/", True),
    ("/*", True),
    ("/**", True),
    ("/mnt", True),
    ("/mnt/c/Users", True),
    ("/usr/lib/wsl/drivers", True),
    ("/proc", True),
    ("/sys/kernel", True),
    ("@/mnt/c", True),
    (".", False),
    ("./", False),
    ("app/models", False),
    ("/home/chengpeng", False),
    ("/mntx", False),
]

# command -> every segment is a search command?
PURE_CASES: list[tuple[str, bool]] = [
    ("find . -name x", True),
    ("rg foo", True),
    ("rg foo | head", False),
    ("rg foo && bundle exec rspec", False),
    ("uv run pytest", False),
    ("node -e \"console.log('a b')\"", False),
    ("", False),
]

# read path -> image (would be sent as a base64 attachment)?
IMAGE_CASES: list[tuple[str, bool]] = [
    ("reqs/PG-12937/assets/669971526/entry1-project-detail.png", True),
    ("/tmp/Shot.PNG", True),
    ("a/b/photo.jpeg", True),
    ("x.JPG", True),
    ("anim.gif", True),
    ("pic.webp", True),
    ("sprite.bmp", True),
    ("@reqs/x.png", True),
    ("reqs/prototype.svg", False),
    ("app/models/user.rb", False),
    ("REQUIREMENT.md", False),
    ("", False),
    ("/home/chengpeng", False),
]

HARNESS = """
import {{ createJiti }} from {jiti};
const jiti = createJiti(import.meta.url, {{
  alias: {{ "@earendil-works/pi-coding-agent": {entry} }},
}});
const mod = await jiti.import({guard});
const bash = {bash};
const paths = {paths};
const pure = {pure};
const images = {images};
let bad = 0;
for (const [cmd, cwd, want] of bash) {{
  const got = mod.dangerousSearchTarget(cmd, cwd) !== null;
  if (got !== want) {{ bad++; console.log(`FAIL bash want=${{want}} got=${{got}} :: ${{cmd}}`); }}
}}
for (const [p, want] of paths) {{
  const got = mod.isDangerousPath(p, "/repo");
  if (got !== want) {{ bad++; console.log(`FAIL path want=${{want}} got=${{got}} :: ${{p}}`); }}
}}
for (const [cmd, want] of pure) {{
  const got = mod.isPureSearchCommand(cmd);
  if (got !== want) {{ bad++; console.log(`FAIL pure want=${{want}} got=${{got}} :: ${{cmd}}`); }}
}}
for (const [p, want] of images) {{
  const got = mod.isImagePath(p);
  if (got !== want) {{ bad++; console.log(`FAIL image want=${{want}} got=${{got}} :: ${{p}}`); }}
}}
console.log(bad === 0 ? "ALL PASS" : `${{bad}} FAILURES`);
process.exit(bad === 0 ? 0 : 1);
"""


def _pi_package_root() -> Path | None:
    """Resolve the installed `@earendil-works/pi-coding-agent` package root."""
    binary = shutil.which(os.environ.get("YARD_PI") or "pi")
    if not binary:
        return None
    resolved = Path(binary).resolve()
    for parent in (resolved.parent, *resolved.parents):
        manifest = parent / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text())
        except ValueError:
            continue
        if data.get("name") == "@earendil-works/pi-coding-agent":
            return parent
    return None


def test_yard_guard_logic(tmp_path: Path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    pkg = _pi_package_root()
    if pkg is None:
        pytest.skip("global pi install not found")
    jiti = pkg / "node_modules" / "jiti" / "lib" / "jiti-static.mjs"
    entry = pkg / "dist" / "index.js"
    if not jiti.is_file() or not entry.is_file():
        pytest.skip("pi's jiti loader not found")

    guard = resolve_extension_path(REPO_ROOT)
    assert guard is not None and guard.is_file()

    script = HARNESS.format(
        jiti=json.dumps(str(jiti)),
        entry=json.dumps(str(entry)),
        guard=json.dumps(str(guard)),
        bash=json.dumps(BASH_CASES),
        paths=json.dumps(PATH_CASES),
        pure=json.dumps(PURE_CASES),
        images=json.dumps(IMAGE_CASES),
    )
    harness = tmp_path / "check-guard.mjs"
    harness.write_text(script)

    proc = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL PASS" in proc.stdout
