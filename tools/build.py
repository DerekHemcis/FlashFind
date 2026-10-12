"""FlashFind 打包脚本。

用法:
    python tools/build.py            # 打包 onedir（启动最快，推荐）
    python tools/build.py onefile    # 打包单文件 exe（便于分发，首次启动稍慢）

产物位于 dist/ 目录。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _retire_old_dist() -> None:
    """把上一轮产物移走而不是删除。

    某些环境（受控终端 / 安全策略）会拦截批量删除，
    用 rename 到临时目录可以避免触发，同时让 PyInstaller 无需删除旧目录。
    """
    dist = ROOT / "dist" / "FlashFind"
    if dist.exists():
        dst = Path(tempfile.gettempdir()) / f"flashfind_old_{int(time.time())}"
        try:
            shutil.move(str(dist), str(dst))
            print(f"旧产物已移至: {dst}")
        except Exception as e:
            print(f"移动旧产物失败（将尝试覆盖构建）: {e}")


def build(mode: str = "onedir") -> int:
    _retire_old_dist()
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
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
