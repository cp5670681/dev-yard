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
