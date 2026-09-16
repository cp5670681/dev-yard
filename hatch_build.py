import shutil
import subprocess
import sys
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        web_dir = root / "web"
        spa_index = root / "src" / "dev_yard" / "web" / "spa" / "index.html"

        if not (web_dir / "package.json").is_file():
            return

        pnpm = shutil.which("pnpm") or shutil.which("npm")
        if not pnpm:
            if spa_index.is_file():
                return
            msg = (
                "pnpm/npm not found and frontend SPA is not built; "
                "install Node or run `pnpm --dir web build` first."
            )
            if getattr(self, "target_name", "") == "sdist":
                sys.stderr.write(f"Warning: {msg}\n")
                return
            raise RuntimeError(msg)

        if not (web_dir / "node_modules").is_dir():
            install_cmd = [pnpm, "install"]
            subprocess.run(install_cmd, cwd=str(web_dir), check=True)

        build_cmd = [pnpm, "run", "build"]
        subprocess.run(build_cmd, cwd=str(web_dir), check=True)
