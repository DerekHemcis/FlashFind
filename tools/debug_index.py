"""调试索引进度：60 秒内观察扫描条数增长。"""

import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp(prefix="ffdbg_")
from flashfind.config import Config
from flashfind.indexer import Indexer
from flashfind.store import Store

store = Store(db_path=Path(tmp) / "dbg.db", cache_dir=Path(tmp))
cfg = Config()
print("roots:", cfg.get("roots") or ["C:\\", "D:\\"])
idx = Indexer(store, cfg)
idx.start_background(roots=["C:\\"], incremental=False)

for i in range(12):
    time.sleep(5)
    print(f"{5*(i+1):3d}s  已索引 {idx.total:,}  running={idx.running}  err={idx.error}", flush=True)
    if not idx.running:
        break

idx.stop()
tasks = idx._split_tasks(["C:\\"])
print("任务数(顶层):", len(tasks))
print("前 10 个任务:", [t[0] for t in tasks[:10]])
