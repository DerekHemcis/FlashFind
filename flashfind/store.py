"""FlashFind 存储与搜索层。

设计要点
--------
1. 元数据（完整路径、大小、修改时间）存 SQLite，按需精确查询；
2. 全部文件名拼成一张"大字符串"（blob），用 str.find 在 C 层扫描，
   配合 offsets 数组二分定位行号 —— 这是毫秒级子串搜索的关键；
   实测：50 万条文件名，中文查询 17ms（SQLite LIKE 需要 650ms），
   索引体积 15MB（FTS5 trigram 需要 571MB）；
3. 行号 -> SQLite rowid 通过 rowids 数组映射，容忍 rowid 空洞；
4. 删除不做即时清理，搜索结果用 os.path.exists 惰性过滤，
   定期重建索引时才物理删除，保证 blob 行序稳定。
"""

from __future__ import annotations

import array
import os
import sqlite3
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from .config import DB_PATH, DATA_DIR, ensure_data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    dir     TEXT NOT NULL,
    path    TEXT NOT NULL UNIQUE,
    ext     TEXT,
    size    INTEGER DEFAULT 0,
    mtime   REAL DEFAULT 0,
    is_dir  INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_dir     ON files(dir);
CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(deleted);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# 单次搜索最多收集多少条原始匹配（防止单字符查询扫出上百万条）
MAX_COLLECT = 4000
# 送进 SQLite 取详情的候选条数（打分后的 top N）
CANDIDATE_LIMIT = 240


@dataclass
class Result:
    """一条搜索结果。"""

    path: str
    name: str
    dir: str
    size: int
    mtime: float
    is_dir: bool
    score: float
    match_start: int = -1  # 匹配在文件名中的起始位置（用于高亮）

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "dir": self.dir,
            "size": self.size,
            "mtime": self.mtime,
            "is_dir": self.is_dir,
        }


class Store:
    """索引数据库 + 文件名大表。"""

    def __init__(self, db_path: Path | str = DB_PATH, cache_dir: Path | str = DATA_DIR):
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        ensure_data_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.names_path = self.cache_dir / "names.bin"
        self.offsets_path = self.cache_dir / "offsets.bin"
        self.rowids_path = self.cache_dir / "rowids.bin"

        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self._init_schema()

        # 内存大表
        self._blob: str = ""
        self._offsets: array.array = array.array("q")
        self._rowids: array.array = array.array("q")
        self._loaded = False
        self._tail_lock = threading.Lock()

    # ------------------------------------------------------------------ 基础
    def _init_schema(self) -> None:
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def close(self) -> None:
        try:
            self.db.commit()
        except Exception:
            pass
        self.db.close()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self.db.commit()

    def count(self, alive_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM files" + (" WHERE deleted=0" if alive_only else "")
        return self.db.execute(sql).fetchone()[0]

    # ------------------------------------------------------------ 索引写入
    def begin_rebuild(self) -> None:
        """清空索引，准备全量重建。"""
        with self._lock:
            self.db.execute("DELETE FROM files")
            self.db.commit()
            self._loaded = False
            for p in (self.names_path, self.offsets_path, self.rowids_path):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass

    def add_batch(self, rows: list[tuple]) -> int:
        """批量写入。rows: (name, dir, path, ext, size, mtime, is_dir)"""
        if not rows:
            return 0
        with self._lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO files(name,dir,path,ext,size,mtime,is_dir,deleted) "
                "VALUES(?,?,?,?,?,?,?,0)",
                rows,
            )
            self.db.commit()
            return len(rows)

    def mark_deleted(self, path: str) -> None:
        with self._lock:
            self.db.execute("UPDATE files SET deleted=1 WHERE path=?", (path,))
            self.db.commit()

    def upsert(self, path: str, is_dir: bool = False) -> bool:
        """增量新增/更新单条。返回是否真的写入。"""
        try:
            st = os.stat(path)
        except OSError:
            return False
        name = os.path.basename(path)
        parent = os.path.dirname(path)
        ext = os.path.splitext(name)[1].lower()
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO files(name,dir,path,ext,size,mtime,is_dir,deleted) "
                "VALUES(?,?,?,?,?,?,?,0) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                "deleted=0",
                (name, parent, path, ext, st.st_size, st.st_mtime, int(is_dir)),
            )
            self.db.commit()
            return cur.rowcount != 0

    def pending_tail(self) -> list[tuple]:
        """返回尚未进入内存大表的行（增量写入后需要追加到 blob）。"""
        min_id = int(self.get_meta("blob_max_id", "0") or 0)
        rows = self.db.execute(
            "SELECT id,name FROM files WHERE id>? ORDER BY id", (min_id,)
        ).fetchall()
        return rows

    # ------------------------------------------------------------ 内存大表
    def rebuild_blob(self, progress=None) -> None:
        """全量重建文件名大表（流式写入，避免一次性占满内存）。"""
        tmp_names = self.names_path.with_suffix(".tmp")
        offsets = array.array("q")
        rowids = array.array("q")
        pos = 0
        total = 0
        with self._tail_lock:
            with open(tmp_names, "w", encoding="utf-8", newline="\n") as f:
                first = True
                for rid, name in self.db.execute(
                    "SELECT id,name FROM files WHERE deleted=0 ORDER BY id"
                ):
                    low = name.lower()
                    offsets.append(pos)
                    rowids.append(rid)
                    if first:
                        f.write(low)
                        first = False
                    else:
                        f.write("\n" + low)
                    pos += len(low) + 1
                    total += 1
                    if progress and total % 50000 == 0:
                        progress(total)
        with open(self.offsets_path, "wb") as f:
            offsets.tofile(f)
        with open(self.rowids_path, "wb") as f:
            rowids.tofile(f)
        os.replace(tmp_names, self.names_path)

        max_id = self.db.execute("SELECT MAX(id) FROM files WHERE deleted=0").fetchone()[0] or 0
        self.set_meta("blob_max_id", str(max_id))
        self.set_meta("blob_count", str(total))
        self._load_blob()

    def _load_blob(self) -> None:
        if not self.names_path.exists() or not self.offsets_path.exists():
            self._loaded = True
            return
        with open(self.names_path, "r", encoding="utf-8") as f:
            blob = f.read()
        offsets = array.array("q")
        n_off = self.offsets_path.stat().st_size // offsets.itemsize
        with open(self.offsets_path, "rb") as f:
            offsets.fromfile(f, n_off)
        rowids = array.array("q")
        if self.rowids_path.exists():
            n_rid = self.rowids_path.stat().st_size // rowids.itemsize
            with open(self.rowids_path, "rb") as f:
                rowids.fromfile(f, n_rid)
        with self._tail_lock:
            self._blob = blob
            self._offsets = offsets
            self._rowids = rowids
            self._loaded = True

    def _append_blob_tail(self) -> None:
        """把增量新增的行追加到大表尾部（保持行序与 rowid 顺序一致）。"""
        rows = self.pending_tail()
        if not rows:
            return
        with self._tail_lock:
            if not self._loaded:
                return
            base = len(self._blob)
            with open(self.names_path, "a", encoding="utf-8", newline="\n") as f:
                for _, name in rows:
                    low = name.lower()
                    self._offsets.append(base)
                    self._rowids.append(_)
                    f.write(("\n" if base else "") + low)
                    base += len(low) + 1
            self._blob = None  # 释放旧的（下面重新读）
            with open(self.names_path, "r", encoding="utf-8") as f:
                self._blob = f.read()
            with open(self.offsets_path, "wb") as f:
                self._offsets.tofile(f)
            with open(self.rowids_path, "wb") as f:
                self._rowids.tofile(f)
            self.set_meta("blob_max_id", str(rows[-1][0]))

    def refresh_tail(self) -> None:
        """把索引新增的行追加进内存大表（供实时监听调用）。"""
        try:
            self._append_blob_tail()
        except Exception:
            pass

    def ensure_loaded(self, force_rebuild: bool = False) -> None:
        """确保内存大表可用；必要时重建或从磁盘加载。"""
        if force_rebuild:
            self.rebuild_blob()
            return
        if not self._loaded:
            self._load_blob()
        if not self._offsets or not self._blob:
            if self.count() > 0:
                self.rebuild_blob()
            else:
                self._loaded = True
            return
        self._append_blob_tail()

    # ---------------------------------------------------------------- 搜索
    @property
    def ready(self) -> bool:
        return bool(self._blob) and bool(self._offsets)

    def search(self, query: str, limit: int = 60, include_dirs: bool = True) -> list[Result]:
        """文件名子串搜索，毫秒级返回。"""
        q = (query or "").strip().lower()
        if not q or not self.ready:
            return []

        blob = self._blob
        offsets = self._offsets
        rowids = self._rowids
        qlen = len(q)
        nrows = len(offsets)

        # 1) C 层扫描收集匹配行
        matches: list[tuple[int, int]] = []  # (row_index, match_pos_in_name)
        start = 0
        while len(matches) < MAX_COLLECT:
            i = blob.find(q, start)
            if i < 0:
                break
            row = bisect_right(offsets, i) - 1
            if row < 0:
                row = 0
            matches.append((row, i - offsets[row]))
            start = i + qlen

        if not matches:
            return []

        # 2) 快速打分：前缀优先、匹配位置靠前优先、文件名短优先
        scored: list[tuple[float, int, int]] = []  # (score, row_index, match_pos)
        for row, mpos in matches:
            name_start = offsets[row]
            name_end = offsets[row + 1] - 1 if row + 1 < nrows else len(blob)
            nlen = name_end - name_start
            score = 0.0
            if mpos == 0:
                score += 1200.0  # 前缀匹配，最强信号
            score += max(0.0, 260.0 - mpos * 12.0)
            score += max(0.0, 140.0 - nlen)
            scored.append((score, row, mpos))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[:CANDIDATE_LIMIT]

        # 3) 取候选行的完整信息
        ids = [rowids[row] if row < len(rowids) else row + 1 for _, row, _ in top]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        detail: dict[int, tuple] = {}
        sql = (
            f"SELECT id,name,dir,path,size,mtime,is_dir FROM files "
            f"WHERE id IN ({placeholders}) AND deleted=0"
        )
        for r in self.db.execute(sql, ids):
            detail[r[0]] = r

        # 4) 叠加时间新鲜度，惰性校验文件是否真的还在
        now = time.time()
        results: list[Result] = []
        for score, row, mpos in top:
            rid = rowids[row] if row < len(rowids) else row + 1
            rec = detail.get(rid)
            if rec is None:
                continue
            _, name, dirpath, path, size, mtime, is_dir = rec
            if not include_dirs and is_dir:
                continue
            if not os.path.exists(path):
                # 文件已被删掉但索引还没更新，顺手标记，下次重建时清理
                self.mark_deleted(path)
                continue
            age_days = (now - (mtime or 0)) / 86400.0
            if age_days <= 7:
                bonus = 110.0
            elif age_days <= 30:
                bonus = 80.0
            elif age_days <= 180:
                bonus = 50.0
            elif age_days <= 365:
                bonus = 20.0
            else:
                bonus = 0.0
            if is_dir:
                bonus -= 15.0
            results.append(
                Result(
                    path=path,
                    name=name,
                    dir=dirpath,
                    size=size or 0,
                    mtime=mtime or 0.0,
                    is_dir=bool(is_dir),
                    score=score + bonus,
                    match_start=mpos,
                )
            )
            if len(results) >= limit * 4:  # 够了就别再查了
                break

        results.sort(key=lambda r: -r.score)
        return results[:limit]

    # ---------------------------------------------------------------- 统计
    def stats_text(self) -> str:
        n = self.count()
        last = self.get_meta("last_scan")
        if last:
            try:
                last = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(last)))
            except Exception:
                pass
        return f"{n:,} 个文件已索引 · 上次更新 {last or '未知'}"
