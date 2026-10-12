<div align="center">

# FlashFind ⚡

**毫秒级 Windows 文件搜索 · 托盘常驻 · 全局热键唤起**

让"文件资源管理器里搜不到文件"成为历史。

</div>

## 为什么做这个

Windows 自带的文件搜索慢、索引不可控、AppData 一堆干扰。FlashFind 用一套
**自研索引引擎**（SQLite 元数据 + 内存文件名大表），在纯 Python、零第三方索引
依赖的情况下做到：

| 指标 | FlashFind | Windows 资源管理器 |
|---|---|---|
| 中文子串搜索 | **~0.4 ms**（实测 50 万文件名，平均 0.36 ms） | 数秒~数十秒 |
| 索引体积 | ~15 MB / 50 万文件 | GB 级 |
| 搜索方式 | 随时全局热键唤起浮层 | 要开窗口 |

> 实测对比：同样的文件名子串查询，SQLite `LIKE` 全表扫描需要 650ms，
> FTS5 trigram 索引体积 571MB 且中文 2 字查询失效 —— 这两个方案都被我们否决了，
> 最终采用自研的内存大表 + C 层扫描方案。

## 功能

- ⚡ **毫秒级文件名搜索**：中英文、大小写不敏感、任意子串
- 🌐 **全局热键唤起**：默认 `Ctrl+Alt+F`，浮层居中显示，Esc 收起
- 📌 **托盘常驻**：右键菜单可重建索引 / 编辑配置 / 开机自启
- 🔁 **实时增量更新**：桌面、文档、下载等常用目录用 `ReadDirectoryChangesW`
  递归监听，新建/删除/重命名即时生效
- 🧹 **智能排除**：自动跳过 `Windows`、`node_modules`、`.git`、`AppData` 噪音目录
- ⌨️ **键盘全流程**：`↑↓` 选择 · `Enter` 打开 · `Ctrl+Enter` 在资源管理器中定位
- 🎯 **智能排序**：前缀匹配优先 + 匹配位置 + 文件名长度 + 最近修改时间加权
- 🪶 **资源占用小**：常驻内存 ~50MB，空闲 CPU 几乎为 0

## 界面

![screenshot](docs/screenshot.png)

## 快速开始

### 方式一：直接下载 exe（推荐）

从 [Releases](../../releases) 下载 `FlashFind.zip`，解压后双击 `FlashFind.exe`：

1. 首次启动自动在后台建立全盘索引（托盘可见进度，几分钟）
2. 索引完成后按 `Ctrl+Alt+F` 唤起搜索

### 方式二：源码运行

```bash
git clone https://github.com/DerekHemcis/FlashFind.git
cd FlashFind
pip install -r requirements.txt
python app.py
```

要求：Windows 10/11 + Python 3.10+

## 配置

配置文件位于 `%LOCALAPPDATA%\FlashFind\config.json`，托盘菜单可直接打开编辑：

```jsonc
{
  "roots": [],                    // 索引根目录，空 = 自动检测所有固定磁盘
  "exclude_dir_names": [...],     // 排除的目录名（小写）
  "exclude_path_prefixes": [...], // 排除的绝对路径前缀
  "hotkey": "ctrl+alt+f",         // 全局热键，支持 ctrl/alt/shift/win + 单键
  "max_results": 60,              // 单次返回结果上限
  "watch_enabled": true,          // 常用目录实时监听
  "rescan_interval_hours": 6,     // 全盘增量重扫间隔，0 = 关闭
  "theme": "light",               // 界面主题
  "launch_on_startup": false,     // 开机自启（托盘菜单里勾选更方便）
  "index_dirs": true              // 是否把文件夹也纳入搜索
}
```

修改后重启程序生效。

## 架构

```
flashfind/
├── config.py    # 配置与排除规则
├── store.py     # SQLite 元数据 + 内存文件名大表 + 毫秒级搜索
├── indexer.py   # 多线程全盘扫描、临时表增量合并
├── watcher.py   # ReadDirectoryChangesW 实时监听
├── hotkey.py    # Win32 RegisterHotKey 全局热键
├── ui.py        # PySide6 无边框浮层 + 自绘结果列表
└── app.py       # 编排：托盘、单实例、定时重扫、开机自启
```

**索引引擎原理**：所有文件名小写后拼成一张大字符串（50 万文件名仅 ~15MB），
搜索时用 `str.find` 在 C 层扫描，配合偏移数组二分定位行号，再回 SQLite 取
完整元数据。避开了 FTS5 对中文子串搜索失效、索引体积爆炸的问题。

## 构建 exe

```bash
pip install pyinstaller
python tools/build.py          # onedir，启动最快
python tools/build.py onefile  # 单文件，便于分发
```

## Roadmap

- [ ] 拼音首字母搜索（输入 `bg` 找到 `报告.docx`）
- [ ] 文件内容全文搜索（可选）
- [ ] 深色主题
- [ ] 结果过滤（只看文档 / 只看图片）
- [ ] 多标签搜索历史

## License

[MIT](LICENSE)
