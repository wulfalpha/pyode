# SPDX-License-Identifier: GPL-2.0-or-later
"""Check real pip and uv tool installs without touching the user's tools.

Usage: python scripts/check_install.py dist/pyode_radio-0.2.0-py3-none-any.whl
Requires uv on PATH. No libmpv, audio hardware, or live radio is needed.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*command: str | Path, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True)


def main() -> None:
    wheel = Path(sys.argv[1]).resolve(strict=True)
    if wheel.is_dir():
        wheels = list(wheel.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit("Expected exactly one wheel in the distribution directory")
        wheel = wheels[0]
    with tempfile.TemporaryDirectory(prefix="pyode-install-") as directory:
        root = Path(directory)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.update(
            PYODE_STATIONS_FILE=str(root / "stations.csv"),
            PYODE_CONFIG_FILE=str(root / "config.toml"),
            UV_TOOL_DIR=str(root / "tools"),
            UV_TOOL_BIN_DIR=str(root / "bin"),
        )
        smoke = root / "smoke_installed.py"
        shutil.copyfile(Path(__file__).with_name("smoke_installed.py"), smoke)
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        python_name = "python.exe" if os.name == "nt" else "python"
        command_name = "pyode.exe" if os.name == "nt" else "pyode"
        run(sys.executable, "-m", "venv", root / "pip", cwd=root, env=env)
        python = root / "pip" / bin_dir / python_name
        run(python, "-m", "pip", "install", wheel, cwd=root, env=env)
        command = root / "pip" / bin_dir / command_name
        run(command, "--version", cwd=root, env=env)
        run(command, "--list", cwd=root, env=env)
        run(python, smoke, cwd=root, env=env)
        run("uv", "tool", "install", "--python", sys.executable, wheel, cwd=root, env=env)
        run(root / "bin" / command_name, "--version", cwd=root, env=env)
        run(root / "bin" / command_name, "--list", cwd=root, env=env)
        run(root / "tools" / "pyode-radio" / bin_dir / python_name, smoke, cwd=root, env=env)


if __name__ == "__main__":
    main()
