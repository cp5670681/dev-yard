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
            if not spa_index.is_file():
                sys.stderr.write(
                    "Warning: pnpm/npm not found and frontend SPA is not built.\n"
                )
            return

        if not (web_dir / "node_modules").is_dir():
            install_cmd = [pnpm, "install"]
            subprocess.run(install_cmd, cwd=str(web_dir), check=True)

        build_cmd = [pnpm, "run", "build"]
        subprocess.run(build_cmd, cwd=str(web_dir), check=True)
