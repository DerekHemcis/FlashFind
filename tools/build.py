"""FlashFind 打包脚本。

用法:
    python tools/build.py            # 打包 onedir（启动最快，推荐）
    python tools/build.py onefile    # 打包单文件 exe（便于分发，首次启动稍慢）

产物位于 dist/ 目录。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(mode: str = "onedir") -> int:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--name",
        "FlashFind",
        "--icon",
        str(ROOT / "assets" / "icon.ico"),
        "--add-data",
        f"{ROOT / 'assets' / 'icon.ico'};flashfind/assets",
        str(ROOT / "app.py"),
    ]
    if mode == "onefile":
        args.insert(3, "--onefile")
    else:
        args.insert(3, "--onedir")
    print(">>", " ".join(args))
    return subprocess.run(args, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "onedir"
    sys.exit(build(mode))
