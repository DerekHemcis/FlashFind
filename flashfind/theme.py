"""界面主题配色与类型色板。"""

# 浅色主题
LIGHT = {
    "bg": "#FFFFFF",
    "card": "#FFFFFF",
    "border": "#E3E6EA",
    "input_bg": "#F4F5F7",
    "input_bg_focus": "#FFFFFF",
    "text": "#1F2328",
    "text_dim": "#8A9099",
    "text_faint": "#AEB4BC",
    "hover": "#F5F6F8",
    "selected": "#EAF2FF",
    "selected_border": "#BBD6FF",
    "highlight_bg": "#D8E8FF",
    "highlight_fg": "#1A56DB",
    "badge_text": "#FFFFFF",
    "shadow": "#9AA3AF",
    "divider": "#EFF1F3",
    "accent": "#2F6FEB",
}

# 文件类型色板：扩展名前缀 -> (颜色, 徽章文字)
TYPE_COLORS = {
    "folder": ("#F0A020", "DIR"),
    "code": ("#4C7DF0", ""),
    "doc": ("#2FA36B", ""),
    "sheet": ("#2FA36B", ""),
    "image": ("#8B5CF6", ""),
    "video": ("#E0518A", ""),
    "audio": ("#E08A2B", ""),
    "archive": ("#C89A1E", ""),
    "exe": ("#D9534F", ""),
    "db": ("#5C7C99", ""),
    "other": ("#8A9099", ""),
}

EXT_TYPE = {
    # 代码 / 文本
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".json": "code", ".html": "code", ".htm": "code", ".css": "code", ".scss": "code",
    ".cpp": "code", ".c": "code", ".h": "code", ".hpp": "code", ".java": "code",
    ".go": "code", ".rs": "code", ".rb": "code", ".php": "code", ".cs": "code",
    ".sh": "code", ".ps1": "code", ".bat": "code", ".cmd": "code", ".yml": "code",
    ".yaml": "code", ".toml": "code", ".ini": "code", ".cfg": "code", ".xml": "code",
    ".vue": "code", ".kt": "code", ".swift": "code", ".sql": "code", ".ipynb": "code",
    # 文档
    ".doc": "doc", ".docx": "doc", ".pdf": "doc", ".txt": "doc", ".md": "doc",
    ".rtf": "doc", ".odt": "doc", ".ppt": "doc", ".pptx": "doc", ".epub": "doc",
    # 表格
    ".xls": "sheet", ".xlsx": "sheet", ".csv": "sheet", ".tsv": "sheet",
    # 图片
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".bmp": "image", ".svg": "image", ".webp": "image", ".ico": "image",
    ".psd": "image", ".heic": "image", ".tif": "image", ".tiff": "image",
    # 视频
    ".mp4": "video", ".mkv": "video", ".avi": "video", ".mov": "video",
    ".flv": "video", ".wmv": "video", ".webm": "video", ".mts": "video",
    # 音频
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".m4a": "audio",
    ".aac": "audio", ".ogg": "audio", ".wma": "audio",
    # 压缩包
    ".zip": "archive", ".rar": "archive", ".7z": "archive", ".tar": "archive",
    ".gz": "archive", ".bz2": "archive", ".xz": "archive", ".iso": "archive",
    # 可执行
    ".exe": "exe", ".msi": "exe", ".dll": "exe", ".sys": "exe", ".apk": "exe",
    # 数据库
    ".db": "db", ".sqlite": "db", ".sqlite3": "db", ".mdb": "db", ".dat": "db",
}


def type_of(ext: str, is_dir: bool = False) -> tuple[str, str]:
    """返回 (颜色, 徽章文字)。"""
    if is_dir:
        return TYPE_COLORS["folder"]
    ext = (ext or "").lower()
    kind = EXT_TYPE.get(ext, "other")
    color, label = TYPE_COLORS[kind]
    if not label:
        label = (ext.lstrip(".")[:3] or "···").upper()
    return color, label
