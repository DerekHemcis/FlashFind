"""FlashFind 索引引擎。

- 多线程递归扫描（os.scandir，I/O 密集，多线程有效）
- 目录名 / 绝对路径 / 扩展名三重排除规则
- 跳过符号链接与 junction，避免 AppData 里的循环链接导致无限递归
- 重扫时先写入临时表再合并，合并期间旧索引仍可正常搜索
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

from .config import Config, detect_fixed_drives
from .store import Store

BATCH_SIZE = 5000
DEFAULT_WORKERS = 6


class Indexer:
    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg
        self.exclude_names = {str(x).lower() for x in cfg["exclude_dir_names"]}
        self.exclude_prefixes = tuple(str(x).lower() for x in cfg["exclude_path_prefixes"])
        self.exclude_ext = {str(x).lower() for x in cfg.get("exclude_extensions", [])}
        self.include_extra = [str(x).lower().rstrip("\\") for x in cfg.get("include_extra", [])]
        self.index_dirs = bool(cfg.get("index_dirs", True))

        self.total = 0
        self.running = False
        self.finished_at: float | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue(maxsize=128)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 排除判定
    def _in_include_extra(self, path_lower: str) -> bool:
        for p in self.include_extra:
            if path_lower == p or path_lower.startswith(p + "\\"):
                return True
        return False

    def _skip_dir(self, name_lower: str, path_lower: str) -> bool:
        if self._in_include_extra(path_lower):
            return False
        if name_lower in self.exclude_names:
            return True
        for p in self.exclude_prefixes:
            if path_lower.startswith(p):
                return True
        return False

    def _skip_file(self, name_lower: str) -> bool:
        dot = name_lower.rfind(".")
        if dot <= 0:
            return False
        return name_lower[dot:] in self.exclude_ext

    # ------------------------------------------------------------ 扫描
    def _walk(self, root: str, emit, max_depth: int | None = None) -> None:
        """迭代式深度优先遍历一棵子树。emit(list_of_tuples) 用于交付批次。

        max_depth 限制下钻层数：根任务只收直接文件、不下钻，
        子目录交给各自的独立任务，避免与根任务重复扫描。
        """
        stack = [(root, 0)]
        batch: list[tuple] = []

        def flush():
            if batch:
                emit(list(batch))
                batch.clear()

        while stack:
            if self._stop.is_set():
                return
            cur, depth = stack.pop()
            try:
                scanner = os.scandir(cur)
            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                continue
            with scanner:
                while True:
                    try:
                        entry = next(scanner)
                    except StopIteration:
                        break
                    except (PermissionError, OSError):
                        continue
                    if self._stop.is_set():
                        break
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_link = entry.is_symlink()
                    except OSError:
                        continue
                    name = entry.name
                    path = entry.path
                    low = name.lower()
                    if is_dir:
                        # 符号链接 / junction 一律跳过：Windows 的
                        # AppData\Local\Application Data 这类链接会造成无限递归
                        if is_link:
                            continue
                        if self._skip_dir(low, path.lower()):
                            continue
                        if max_depth is None or depth + 1 <= max_depth:
                            stack.append((path, depth + 1))
                        if self.index_dirs:
                            try:
                                st = entry.stat(follow_symlinks=False)
                                mtime = st.st_mtime
                                size = 0
                            except OSError:
                                mtime, size = 0.0, 0
                            batch.append((name, cur, path, "", size, mtime, 1))
                    else:
                        if self._skip_file(low):
                            continue
                        ext = low[low.rfind("."):] if "." in low else ""
                        try:
                            st = entry.stat(follow_symlinks=False)
                            size, mtime = st.st_size, st.st_mtime
                        except OSError:
                            size, mtime = 0, 0.0
                        batch.append((name, cur, path, ext, size, mtime, 0))
                    if len(batch) >= BATCH_SIZE:
                        flush()
        flush()

    def _split_tasks(self, roots: list[str]) -> list[tuple[str, int | None]]:
        """把根目录拆成 (路径, 下钻深度) 任务。

        盘符根只收直接文件（depth=1），一级子目录各自成为无限深度任务，
        既保证并行度又不重复扫描。注意：'C:\' 绝不能写成 'C:' ——
        后者在 Windows 上指向当前工作目录而非盘根。
        """
        tasks: list[tuple[str, int | None]] = []
        for root in roots:
            root = str(root)
            if len(root) == 2 and root[1] == ":":
                root += "\\"
            if not os.path.isdir(root):
                continue
            tasks.append((root, 1))
            try:
                for entry in os.scandir(root):
                    if self._stop.is_set():
                        break
                    try:
                        if not entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                            continue
                    except OSError:
                        continue
                    if self._skip_dir(entry.name.lower(), entry.path.lower()):
                        continue
                    tasks.append((entry.path, None))
            except (PermissionError, OSError):
                continue
        return tasks

    def _scan(self, roots: list[str], emit) -> int:
        tasks = self._split_tasks(roots)
        if not tasks:
            return 0
        tq: queue.Queue = queue.Queue()
        for t in tasks:
            tq.put(t)

        workers = min(DEFAULT_WORKERS, max(2, len(tasks)))
        threads = []

        def worker():
            while not self._stop.is_set():
                try:
                    task_path, max_depth = tq.get_nowait()
                except queue.Empty:
                    return
                try:
                    self._walk(task_path, emit, max_depth)
                except Exception:
                    pass
                finally:
                    tq.task_done()

        for _ in range(workers):
            th = threading.Thread(target=worker, daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        return self.total

    # ------------------------------------------------------- 对外入口
    def run(self, roots: list[str] | None = None, incremental: bool = True) -> int:
        """执行索引。incremental=True 时写入临时表后合并，旧索引期间仍可用。"""
        if self.running:
            return self.total
        self.running = True
        self._stop.clear()
        self.total = 0
        self.error = None
        t0 = time.time()
        roots = roots or self.cfg.get("roots") or detect_fixed_drives()
        table = "files_tmp" if incremental else "files"

        try:
            with self.store.db:
                self.store.db.execute(f"DROP TABLE IF EXISTS {table}")
                self.store.db.execute(
                    f"CREATE TABLE {table} ("
                    "id INTEGER PRIMARY KEY, name TEXT, dir TEXT, path TEXT UNIQUE, "
                    "ext TEXT, size INTEGER, mtime REAL, is_dir INTEGER, deleted INTEGER DEFAULT 0)"
                )
        except Exception as e:  # pragma: no cover
            self.error = str(e)
            self.running = False
            return 0

        def emit(rows: list[tuple]):
            if not rows:
                return
            try:
                self.store.db.executemany(
                    f"INSERT OR IGNORE INTO {table}"
                    "(name,dir,path,ext,size,mtime,is_dir,deleted) VALUES(?,?,?,?,?,?,?,0)",
                    rows,
                )
                self.store.db.commit()
            except Exception:
                pass
            with self._lock:
                self.total += len(rows)

        try:
            self._scan(roots, emit)
            if incremental and not self._stop.is_set():
                self._merge_tmp()
            self.store.set_meta("last_scan", str(time.time()))
        except Exception as e:
            self.error = str(e)
        finally:
            try:
                self.store.db.execute("DROP TABLE IF EXISTS files_tmp")
                self.store.db.commit()
            except Exception:
                pass
            self.running = False
            self.finished_at = time.time()
            self.elapsed = time.time() - t0
            # 重建内存大表
            try:
                self.store.rebuild_blob()
            except Exception as e:
                self.error = self.error or str(e)
        return self.total

    def _merge_tmp(self) -> None:
        """把临时表合并进主表：新增/更新保留，消失的标记删除并物理清除。"""
        db = self.store.db
        # 1) 新增或更新的行
        db.execute(
            "INSERT OR REPLACE INTO files(id,name,dir,path,ext,size,mtime,is_dir,deleted) "
            "SELECT NULL,name,dir,path,ext,size,mtime,is_dir,0 FROM files_tmp"
        )
        db.commit()
        # 2) 已消失的行
        db.execute(
            "UPDATE files SET deleted=1 WHERE path NOT IN (SELECT path FROM files_tmp)"
        )
        db.commit()
        # 3) 物理删除，回收空间
        db.execute("DELETE FROM files WHERE deleted=1")
        db.commit()

    def start_background(self, roots: list[str] | None = None, incremental: bool = True) -> threading.Thread:
        th = threading.Thread(
            target=self.run, args=(roots, incremental), daemon=True, name="indexer"
        )
        th.start()
        return th

    def stop(self) -> None:
        self._stop.set()
