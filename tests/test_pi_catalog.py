from pathlib import Path

from dev_yard.pi_catalog import list_pi_catalog, parse_list_models_table


SAMPLE = """
provider   model                              context  max-out  thinking  images
anthropic  claude-sonnet-4-5                  1M       64K      yes       yes
rcc        glm-5.3                            524.3K   16.4K    yes       no
rcc        deepseek-v4-pro                    1.0M     16.4K    yes       no
omniroute  antigravity/gemini-3.7-flash-high  1.0M     16.4K    yes       yes
"""


def test_parse_list_models_table():
    rows = parse_list_models_table(SAMPLE)
    assert rows[0] == ("anthropic", "claude-sonnet-4-5")
    assert ("rcc", "glm-5.3") in rows
    assert ("omniroute", "antigravity/gemini-3.7-flash-high") in rows


def test_list_pi_catalog_from_binary(tmp_path: Path, monkeypatch):
    script = tmp_path / "fake-pi"
    script.write_text("#!/bin/sh\ncat <<'EOF'\n" + SAMPLE + "\nEOF\n")
    script.chmod(0o755)
    monkeypatch.setenv("YARD_PI", str(script))
    catalog = list_pi_catalog(binary=str(script))
    assert catalog["error"] is None
    ids = [p["id"] for p in catalog["providers"]]
    assert ids == ["anthropic", "rcc", "omniroute"]
    rcc = next(p for p in catalog["providers"] if p["id"] == "rcc")
    assert rcc["models"] == ["glm-5.3", "deepseek-v4-pro"]
