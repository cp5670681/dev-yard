from __future__ import annotations

import subprocess
from typing import Any

from dev_yard.runners import agent_binary


def parse_list_models_table(text: str) -> list[tuple[str, str]]:
    """Parse `pi --list-models` text table into (provider, model) pairs."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.lower().startswith("provider"):
            continue
        parts = raw.split()
        if len(parts) < 2:
            continue
        provider, model = parts[0], parts[1]
        if provider.lower() == "provider" or model.lower() == "model":
            continue
        rows.append((provider, model))
    return rows


def list_pi_catalog(*, binary: str | None = None, timeout: float = 20.0) -> dict[str, Any]:
    cmd = binary or agent_binary()
    try:
        proc = subprocess.run(
            [cmd, "--list-models", "--offline"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"providers": [], "error": f"pi not found (`{cmd}`)."}
    except subprocess.TimeoutExpired:
        return {"providers": [], "error": "pi --list-models timed out."}
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    pairs = parse_list_models_table(text)
    grouped: dict[str, list[str]] = {}
    for provider, model in pairs:
        bucket = grouped.setdefault(provider, [])
        if model not in bucket:
            bucket.append(model)
    if not grouped:
        hint = text.strip().splitlines()[:8]
        return {
            "providers": [],
            "error": "pi --list-models returned no models"
            + (f": {' '.join(hint)}" if hint else "."),
        }
    return {
        "providers": [{"id": pid, "models": models} for pid, models in grouped.items()],
        "error": None,
    }


DEFAULT_ANTIGRAVITY_MODELS = [
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


def resolve_agy_binary() -> str | None:
    import os
    import shutil
    from pathlib import Path

    env_bin = os.environ.get("YARD_ANTIGRAVITY_BIN")
    if env_bin and (shutil.which(env_bin) or Path(env_bin).exists()):
        return env_bin
    which_bin = shutil.which("agy")
    if which_bin:
        return which_bin
    home_bin = Path.home() / ".gemini" / "bin" / "agy"
    if home_bin.exists():
        return str(home_bin)
    return None


def list_antigravity_catalog(*, timeout: float = 10.0) -> list[str]:
    cmd = resolve_agy_binary()
    if not cmd:
        return list(DEFAULT_ANTIGRAVITY_MODELS)
    try:
        proc = subprocess.run(
            [cmd, "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return list(DEFAULT_ANTIGRAVITY_MODELS)

    models: list[str] = []
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if not raw or "Fetching" in raw:
            continue
        parts = [p for p in raw.split() if not any(c in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" for c in p)]
        if not parts:
            continue
        candidate = parts[0]
        if candidate and candidate not in models:
            models.append(candidate)

    return models if models else list(DEFAULT_ANTIGRAVITY_MODELS)
