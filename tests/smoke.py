"""FlashFind 冒烟测试：索引 -> 搜索 -> 界面渲染。

运行: python tests/smoke.py
会在临时目录里造一批样本文件，验证索引与搜索，并抓一张界面截图。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# 必须在导入 flashfind.config 之前重定向数据目录，避免污染真实配置
TMP_ROOT = tempfile.mkdtemp(prefix="flashfind_test_")
os.environ["LOCALAPPDATA"] = TMP_ROOT

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flashfind.config import Config, DATA_DIR  # noqa: E402
from flashfind.indexer import Indexer  # noqa: E402
from flashfind.store import Store  # noqa: E402

print(f"临时数据目录: {DATA_DIR}")

# ---------------------------------------------------------------- 造样本
SAMPLE = Path(TMP_ROOT) / "sample"
names = [
    ("projects", "季度报告.docx"),
    ("projects", "项目计划.xlsx"),
    ("projects", "README.md"),
    ("projects", "main.py"),
    ("projects/src", "utils.py"),
    ("projects/src", "helper.js"),
    ("docs", "会议记录 2026.txt"),
    ("docs", "notes.txt"),
    ("docs/存档", "年度报告 2025.pdf"),
    ("图片", "截图 2026-10-11.png"),
]
for sub, name in names:
    d = SAMPLE / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("x" * 128, encoding="utf-8")

print(f"样本文件: {len(names)} 个")

# ---------------------------------------------------------------- 索引
cfg = Config()
store = Store()
indexer = Indexer(store, cfg)

t0 = time.time()
n = indexer.run(roots=[str(SAMPLE)], incremental=True)
t1 = time.time()
print(f"索引完成: {n} 条, 耗时 {t1-t0:.2f}s, ready={store.ready}")

assert n >= len(names), f"索引条数不足: {n} < {len(names)}"
assert store.ready, "内存大表未就绪"

# ---------------------------------------------------------------- 搜索
cases = [
    ("报告", ["季度报告.docx", "年度报告 2025.pdf"]),
    ("readme", ["README.md"]),  # 小写查大写，验证大小写不敏感
    (".py", ["main.py", "utils.py"]),
    ("会议", ["会议记录 2026.txt"]),
    ("2026", ["会议记录 2026.txt", "截图 2026-10-11.png"]),
    ("projects", ["projects"]),  # 目录也应在索引里
    ("不存在的东西xyz", []),
]

ok = True
for q, expect in cases:
    t = time.perf_counter()
    res = store.search(q, limit=60)
    ms = (time.perf_counter() - t) * 1000
    got = [r.name for r in res]
    hit = all(e in got for e in expect)
    ok = ok and hit
    flag = "PASS" if hit else "FAIL"
    print(f"  [{flag}] '{q}' -> {got}  ({ms:.1f} ms)")
    if not hit:
        print(f"         期望包含: {expect}")

# ---------------------------------------------------------------- 性能
perf_q = ["报告", "py", "2026", "p"]
elapsed = []
for q in perf_q:
    for _ in range(20):
        t = time.perf_counter()
        store.search(q, limit=60)
        elapsed.append((time.perf_counter() - t) * 1000)
elapsed.sort()
avg = sum(elapsed) / len(elapsed)
print(f"性能: 平均 {avg:.2f} ms, p95 {elapsed[int(len(elapsed)*0.95)]:.2f} ms, "
      f"最慢 {elapsed[-1]:.2f} ms  ({len(elapsed)} 次查询)")

# ---------------------------------------------------------------- 增量
new_file = SAMPLE / "docs" / "新增文件.txt"
new_file.write_text("new", encoding="utf-8")
store.upsert(str(new_file))
store.refresh_tail()
res = store.search("新增", limit=10)
print(f"增量新增: 搜索 '新增' -> {[r.name for r in res]}")
ok = ok and any(r.name == "新增文件.txt" for r in res)

# 删除后应自动过滤
os.remove(str(new_file))
res = store.search("新增", limit=10)
print(f"删除后惰性过滤: 搜索 '新增' -> {[r.name for r in res]}")
ok = ok and not any(r.name == "新增文件.txt" for r in res)

# ---------------------------------------------------------------- 界面
try:
    from PySide6 import QtCore, QtWidgets

    from flashfind.ui import SearchWindow, human_size, human_time

    app = QtWidgets.QApplication(sys.argv)
    w = SearchWindow()
    rows = store.search("报告", limit=10) or store.search("py", limit=10)
    data = []
    for r in rows or []:
        data.append({
            "name": r.name, "dir": r.dir, "path": r.path, "size": r.size,
            "mtime": r.mtime, "is_dir": r.is_dir, "ext": os.path.splitext(r.name)[1].lower(),
            "match_start": r.match_start, "match_len": 2,
            "info": (human_time(r.mtime) + " · " + human_size(r.size)).strip(" ·"),
        })
    if not data:  # 没数据也要能画
        data = [{
            "name": "季度报告.docx", "dir": str(SAMPLE / "projects"), "path": str(SAMPLE / "projects" / "季度报告.docx"),
            "size": 128, "mtime": time.time(), "is_dir": False, "ext": ".docx",
            "match_start": 2, "match_len": 2, "info": "刚刚 · 128 B",
        }]
    w.set_results(data, 12.0, "1,234 个文件已索引")
    w.show_at_cursor_screen()

    shot = Path(ROOT) / "tests" / "_ui_preview.png"

    def grab_and_quit():
        pix = w.grab()
        pix.save(str(shot))
        print(f"界面截图: {shot} ({pix.width()}x{pix.height()})")
        app.quit()

    QtCore.QTimer.singleShot(400, grab_and_quit)
    app.exec()
except Exception as e:
    print("界面测试失败:", e)
    import traceback
    traceback.print_exc()
    ok = False

# ---------------------------------------------------------------- 清理
store.close()
shutil.rmtree(TMP_ROOT, ignore_errors=True)

print("\n结果:", "全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
