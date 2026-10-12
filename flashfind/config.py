"""FlashFind 配置层。

配置保存在 %LOCALAPPDATA%/FlashFind/config.json，首次运行自动生成默认值，
用户可直接编辑该文件（程序下次启动生效），也可通过托盘菜单修改常用项。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "FlashFind"
APP_VERSION = "1.0.0"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
DB_PATH = DATA_DIR / "index.db"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "flashfind.log"

# 目录名黑名单（不区分大小写）。这些目录内容庞大且几乎不会被用户搜索。
DEFAULT_EXCLUDE_DIR_NAMES = [
    # 系统目录
    "Windows",
    "$Recycle.Bin",
    "System Volume Information",
    "Recovery",
    "PerfLogs",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Microsoft",  # AppData 下的 Microsoft，体量巨大且几乎无用
    # 依赖/构建缓存
    "node_modules",
    "__pycache__",
    "site-packages",
    ".venv",
    "venv",
    "env",
    ".gradle",
    ".m2",
    ".npm",
    ".nuget",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "egg-info",
    # 版本控制
    ".git",
    ".svn",
    ".hg",
    # 临时文件
    "Temp",
    "tmp",
    "CacheStorage",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "crashpad",
    "logs",
]

# 绝对路径前缀黑名单（不区分大小写，会做 startswith 判断）
DEFAULT_EXCLUDE_PATH_PREFIXES = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
]

# 不索引的扩展名（多为体积巨大且无检索价值的二进制/媒体文件，仅排除索引对象本身，
# 其所在目录仍会被遍历）
DEFAULT_EXCLUDE_EXTENSIONS = [
    ".log",
    ".tmp",
    ".temp",
    ".bak",
    ".crdownload",
    ".part",
    ".etl",
    ".db-shm",
    ".db-wal",
]

DEFAULT_CONFIG: dict = {
    # 索引根，空列表表示自动检测所有固定磁盘
    "roots": [],
    # 额外要索引的目录（在黑名单内也强制索引），例如 r"D:\projects"
    "include_extra": [],
    "exclude_dir_names": DEFAULT_EXCLUDE_DIR_NAMES,
    "exclude_path_prefixes": DEFAULT_EXCLUDE_PATH_PREFIXES,
    "exclude_extensions": DEFAULT_EXCLUDE_EXTENSIONS,
    # 全局热键，格式: "ctrl+alt+f"，可用修饰键 ctrl/alt/shift/win + 单字符或 F1-F24
    "hotkey": "ctrl+alt+f",
    # 单次搜索返回的最大结果数
    "max_results": 60,
    # 是否对常用目录（桌面/文档/下载等）做实时增量监听
    "watch_enabled": True,
    # 全盘增量重扫间隔（小时），0 表示不自动重扫
    "rescan_interval_hours": 6,
    # 界面主题: light / dark
    "theme": "light",
    # 开机自启
    "launch_on_startup": False,
    # 索引时是否把目录本身也纳入结果
    "index_dirs": True,
}


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class Config:
    """简单的字典式配置对象，读取时自动补齐缺失的键。"""

    def __init__(self, data: dict | None = None):
        self._data = dict(DEFAULT_CONFIG)
        if data:
            self._data.update(data)
        # 归一化：排除项统一小写，便于比较
        self._data["exclude_dir_names"] = [
            str(x).lower() for x in self._data.get("exclude_dir_names", [])
        ]
        self._data["exclude_path_prefixes"] = [
            str(x).lower().rstrip("\\") for x in self._data.get("exclude_path_prefixes", [])
        ]
        self._data["exclude_extensions"] = [
            str(x).lower() for x in self._data.get("exclude_extensions", [])
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return cls(json.load(f))
        except Exception:
            # 配置损坏时回退到默认值，避免程序起不来
            pass
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        ensure_data_dir()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def as_dict(self) -> dict:
        return dict(self._data)


def detect_fixed_drives() -> list[str]:
    """返回本机所有固定磁盘的根路径，如 ['C:\\', 'D:\\']。

    注意：GetLogicalDriveStringsW 返回 "C:\\<nul>D:\\<nul><nul>" 双 null 结尾，
    不能用 create_unicode_buffer().value —— 它会在第一个 <nul> 截断，
    导致 C 盘之后的磁盘全部丢失（D 盘消失的元凶）。
    """
    drives = []
    if os.name != "nt":
        return [str(Path.home())]
    import ctypes

    buf_len = 512
    buf = ctypes.create_unicode_buffer(buf_len)
    ctypes.windll.kernel32.GetLogicalDriveStringsW(buf_len, buf)
    # wchar 数组没有 .value/.raw 能拿到完整内容，只能按 UTF-16LE 字节整体解码
    text = bytes(buf).decode("utf-16-le", errors="ignore")
    for part in text.split("\x00"):
        part = part.strip()
        if len(part) >= 2 and part[1] == ":":
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(part))
            # DRIVE_FIXED = 3
            if dtype == 3:
                drives.append(part if part.endswith("\\") else part + "\\")
    return drives or ["C:\\"]


def default_hot_dirs() -> list[str]:
    """实时监听的常用目录。"""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Pictures",
        home / "Videos",
        home / "Music",
        home,
    ]
    return [str(c) for c in candidates if c.exists()]
