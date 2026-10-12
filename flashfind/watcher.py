"""实时增量监听：ReadDirectoryChangesW 递归监听常用目录。

文件变化时立刻更新索引，无需等待全盘重扫。事件先入队，再按固定间隔批量落库，
避免 git checkout、解压这类瞬间大量事件把主线程拖死。
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FILE_ACTION_ADDED = 0x00000001
FILE_ACTION_REMOVED = 0x00000002
FILE_ACTION_MODIFIED = 0x00000003
FILE_ACTION_RENAMED_OLD_NAME = 0x00000004
FILE_ACTION_RENAMED_NEW_NAME = 0x00000005

NOTIFY_FILTER = (
    0x00000001  # FILE_NOTIFY_CHANGE_FILE_NAME
    | 0x00000002  # FILE_NOTIFY_CHANGE_DIR_NAME
    | 0x00000004  # FILE_NOTIFY_CHANGE_ATTRIBUTES
    | 0x00000008  # FILE_NOTIFY_CHANGE_SIZE
    | 0x00000010  # FILE_NOTIFY_CHANGE_LAST_WRITE
    | 0x00000040  # FILE_NOTIFY_CHANGE_CREATION
)


class _FileNotifyInformation(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.DWORD),
        ("Action", wintypes.DWORD),
        ("FileNameLength", wintypes.DWORD),  # 字节数
        ("FileName", wintypes.WCHAR * 1),  # 变长，运行时再取
    ]


class Watcher:
    def __init__(self, store, dirs: list[str], interval: float = 0.8):
        self.store = store
        self.dirs = list(dirs or [])
        self.interval = interval
        self._q: queue.Queue = queue.Queue()
        self._pending_add: set[str] = set()
        self._pending_del: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ 监听线程
    def _watch_loop(self, directory: str) -> None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(directory),
            FILE_LIST_DIRECTORY,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or handle == -1:
            return
        buf = ctypes.create_string_buffer(65536)
        returned = wintypes.DWORD()
        try:
            while not self._stop.is_set():
                ok = kernel32.ReadDirectoryChangesW(
                    wintypes.HANDLE(handle),
                    buf,
                    ctypes.sizeof(buf),
                    True,  # bWatchSubtree：递归监听整棵子树
                    NOTIFY_FILTER,
                    ctypes.byref(returned),
                    None,
                    None,
                )
                if not ok:
                    break
                try:
                    self._parse(buf, returned.value, directory)
                except Exception:
                    # 单条解析失败不能中断整个目录的监听
                    continue
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))

    def _parse(self, buf, nbytes: int, base: str) -> None:
        if nbytes <= 0:
            return
        # 关键：直接在稳定的缓冲区上做指针偏移。
        # 之前用 create_string_buffer(raw[offset:]) 造临时对象再 cast，
        # 临时对象随即被 GC，指针悬空 → access violation 崩溃。
        base_addr = ctypes.addressof(buf)
        rec_size = ctypes.sizeof(_FileNotifyInformation) - 2  # FileName[1] 是变长起点
        offset = 0
        while offset + rec_size <= nbytes:
            entry = ctypes.cast(
                base_addr + offset, ctypes.POINTER(_FileNotifyInformation)
            ).contents
            nchars = entry.FileNameLength // 2
            if nchars <= 0:
                break
            addr = base_addr + offset + _FileNotifyInformation.FileName.offset
            try:
                name = ctypes.wstring_at(addr, nchars)
            except OSError:
                break
            full = os.path.join(base, name)
            action = entry.Action
            with self._lock:
                if action in (FILE_ACTION_ADDED, FILE_ACTION_RENAMED_NEW_NAME, FILE_ACTION_MODIFIED):
                    self._pending_add.add(full)
                    self._pending_del.discard(full)
                elif action in (FILE_ACTION_REMOVED, FILE_ACTION_RENAMED_OLD_NAME):
                    self._pending_del.add(full)
                    self._pending_add.discard(full)
            if entry.NextEntryOffset == 0:
                break
            offset += entry.NextEntryOffset

    # ------------------------------------------------------------ 应用线程
    def _apply_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            try:
                self.flush()
            except Exception:
                pass

    def flush(self) -> int:
        with self._lock:
            adds = list(self._pending_add)
            dels = list(self._pending_del)
            self._pending_add.clear()
            self._pending_del.clear()
        n = 0
        for p in dels:
            try:
                self.store.mark_deleted(p)
            except Exception:
                pass
        for p in adds:
            try:
                if os.path.exists(p):
                    self.store.upsert(p, is_dir=os.path.isdir(p))
                    n += 1
            except Exception:
                pass
        if n:
            try:
                self.store.refresh_tail()
            except Exception:
                pass
        return n

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if not self.dirs:
            return
        self._stop.clear()
        for d in self.dirs:
            if os.path.isdir(d):
                th = threading.Thread(target=self._watch_loop, args=(d,), daemon=True)
                th.start()
                self._threads.append(th)
        applier = threading.Thread(target=self._apply_loop, daemon=True, name="watcher-apply")
        applier.start()
        self._threads.append(applier)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.flush()
        except Exception:
            pass
