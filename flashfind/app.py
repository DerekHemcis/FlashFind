"""FlashFind 应用编排：索引、搜索、托盘、热键的粘合层。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets

from . import hotkey
from .config import (
    APP_NAME,
    APP_VERSION,
    CONFIG_PATH,
    DATA_DIR,
    LOG_PATH,
    Config,
    default_hot_dirs,
    detect_fixed_drives,
    ensure_data_dir,
)
from .indexer import Indexer
from .store import Store
from .ui import SearchWindow, human_size, human_time
from .watcher import Watcher

HOTKEY_ID = 1
_ICON_CANDIDATES = ["flashfind/assets/icon.ico", "assets/icon.ico"]


def log(msg: str) -> None:
    try:
        ensure_data_dir()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def _icon_path() -> str | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for rel in _ICON_CANDIDATES:
        p = base / rel
        if p.exists():
            return str(p)
    return None


def _fallback_icon() -> QtGui.QIcon:
    """没有图标文件时用代码画一个放大镜，保证源码直接运行也有托盘图标。"""
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor("#2F6FEB"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    pen = QtGui.QPen(QtGui.QColor("#FFFFFF"))
    pen.setWidth(6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawEllipse(18, 18, 22, 22)
    p.drawLine(35, 35, 47, 47)
    p.end()
    return QtGui.QIcon(pm)


def load_icon() -> QtGui.QIcon:
    p = _icon_path()
    if p:
        icon = QtGui.QIcon(p)
        if not icon.isNull():
            return icon
    return _fallback_icon()


class FlashFindApp:
    def __init__(self, argv: list[str]):
        self.cfg = Config.load()
        ensure_data_dir()
        self.store = Store()
        self.indexer = Indexer(self.store, self.cfg)
        self.watcher: Watcher | None = None
        self._pending_query = ""
        self._index_ready = False
        self._last_rescan_check = 0.0

        self.app = QtWidgets.QApplication(argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName(APP_NAME)
        self.app.setApplicationDisplayName(APP_NAME)
        self.app.setWindowIcon(load_icon())

        self.win = SearchWindow()
        self.win.queryChanged.connect(self._on_query)
        self.win.resultActivated.connect(self._open_result)
        self.win.resultLocated.connect(self._locate_result)
        self.win.escapePressed.connect(self._hide)
        self.win.focusLost.connect(self._on_focus_lost)

        self._search_timer = QtCore.QTimer(self.app)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)

        self._tick = QtCore.QTimer(self.app)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)

        self._build_tray()
        self._install_hotkey()

    # ------------------------------------------------------------ 单实例
    def ensure_single_instance(self) -> bool:
        probe = QtNetwork.QLocalSocket()
        probe.connectToServer(APP_NAME)
        if probe.waitForConnected(200):
            try:
                probe.write(b"show")
                probe.flush()
                probe.waitForBytesWritten(200)
            except Exception:
                pass
            probe.disconnectFromServer()
            return False
        QtNetwork.QLocalServer.removeServer(APP_NAME)
        self._server = QtNetwork.QLocalServer()
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(APP_NAME)
        return True

    def _on_new_connection(self):
        conn = self._server.nextPendingConnection()
        if not conn:
            return
        conn.readyRead.connect(lambda: self._show(conn))

    def _show(self, conn=None):
        if conn is not None:
            try:
                conn.readAll()
            except Exception:
                pass
        self.win.show_at_cursor_screen()

    # ------------------------------------------------------------ 托盘
    def _build_tray(self):
        self.tray = QtWidgets.QSystemTrayIcon(load_icon(), self.app)
        self.tray.setToolTip(f"{APP_NAME} · 正在启动")
        menu = QtWidgets.QMenu()
        style = (
            "QMenu{background:#fff;border:1px solid #E3E6EA;border-radius:8px;padding:6px;}"
            "QMenu::item{padding:7px 24px;border-radius:5px;color:#1F2328;}"
            "QMenu::item:selected{background:#EAF2FF;}"
            "QMenu::separator{height:1px;background:#EFF1F3;margin:5px 8px;}"
        )
        menu.setStyleSheet(style)

        combo = self.cfg.get("hotkey", "ctrl+alt+f")
        act_show = menu.addAction(f"显示搜索   ({combo.upper()})")
        act_show.triggered.connect(lambda: self._show())
        menu.addSeparator()

        self.act_rebuild = menu.addAction("重建索引")
        self.act_rebuild.triggered.connect(self._rebuild)
        act_open_dir = menu.addAction("打开配置目录")
        act_open_dir.triggered.connect(lambda: os.startfile(str(DATA_DIR)))
        act_edit_cfg = menu.addAction("编辑配置文件")
        act_edit_cfg.triggered.connect(lambda: os.startfile(str(CONFIG_PATH)))
        menu.addSeparator()

        self.act_autostart = menu.addAction("开机自启")
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(bool(self.cfg.get("launch_on_startup", False)))
        self.act_autostart.triggered.connect(self._toggle_autostart)
        menu.addSeparator()

        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show()
            if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick)
            else None
        )
        self.tray.show()

    # ------------------------------------------------------------ 热键
    def _install_hotkey(self):
        self._hotkey_filter = hotkey.HotkeyFilter()
        self._hotkey_filter.install(self.app, HOTKEY_ID, lambda: QtCore.QMetaObject.invokeMethod(
            self.win, "show_at_cursor_screen", QtCore.Qt.QueuedConnection
        ))
        hwnd = int(self.win.winId())
        combo = self.cfg.get("hotkey", "ctrl+alt+f")
        ok, err = hotkey.register(hwnd, combo, HOTKEY_ID)
        self._hotkey_ok = ok
        if not ok:
            msg = f"热键 {combo.upper()} 注册失败（可能被其它软件占用，错误码 {err}）"
            log(msg)
            self.tray.showMessage(APP_NAME, msg + "\n可在配置文件中改成其它组合",
                                  QtWidgets.QSystemTrayIcon.Warning, 6000)
        self.app.aboutToQuit.connect(lambda: hotkey.unregister(hwnd, HOTKEY_ID))

    # ------------------------------------------------------------ 搜索
    def _on_query(self, text: str):
        self._pending_query = text
        self._search_timer.start(90)

    def _do_search(self):
        q = self._pending_query.strip()
        if not q:
            self.win.set_results([], status_extra=self._status_text())
            return
        if not self.store.ready:
            self.win.set_results([], status_extra="索引尚未就绪，正在建立中…")
            return
        t0 = time.perf_counter()
        try:
            rows = self.store.search(q, limit=int(self.cfg.get("max_results", 60)))
        except Exception as e:
            log("搜索异常: " + traceback.format_exc())
            self.win.set_results([], status_extra=f"搜索出错: {e}")
            return
        ms = (time.perf_counter() - t0) * 1000
        data = []
        for r in rows:
            ext = os.path.splitext(r.name)[1].lower()
            info = human_time(r.mtime)
            sz = human_size(r.size)
            if sz:
                info = f"{info} · {sz}" if info else sz
            data.append(
                {
                    "name": r.name,
                    "dir": r.dir,
                    "path": r.path,
                    "size": r.size,
                    "mtime": r.mtime,
                    "is_dir": r.is_dir,
                    "ext": ext,
                    "match_start": r.match_start,
                    "match_len": len(q),
                    "info": info,
                }
            )
        self.win.set_results(data, ms, status_extra=self._status_text())

    def _status_text(self) -> str:
        if self.indexer.running:
            return f"正在建立索引…已扫描 {self.indexer.total:,} 个文件"
        return self.store.stats_text()

    # ------------------------------------------------------------ 动作
    def _open_result(self, d: dict):
        self._hide()
        try:
            os.startfile(d["path"])
        except Exception as e:
            self.tray.showMessage(APP_NAME, f"打不开：{e}", QtWidgets.QSystemTrayIcon.Warning, 4000)

    def _locate_result(self, d: dict):
        self._hide()
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(d["path"])])
        except Exception as e:
            self.tray.showMessage(APP_NAME, f"定位失败：{e}", QtWidgets.QSystemTrayIcon.Warning, 4000)

    def _hide(self):
        self.win.hide()

    def _on_focus_lost(self):
        # 焦点移开就收起，右键菜单弹出期间不收
        if getattr(self.win, "_suppress_hide", False):
            return
        QtCore.QTimer.singleShot(160, lambda: (self.win.hide() if not self.win.isActiveWindow() else None))

    def _rebuild(self):
        if self.indexer.running:
            return
        self.tray.showMessage(APP_NAME, "开始在后台重建索引…", QtWidgets.QSystemTrayIcon.Information, 3000)
        threading.Thread(target=self._rebuild_worker, daemon=True).start()

    def _rebuild_worker(self):
        try:
            self.indexer.start_background(roots=self.cfg.get("roots") or None, incremental=True).join()
        except Exception:
            log("重建索引异常: " + traceback.format_exc())

    def _toggle_autostart(self, checked: bool):
        try:
            import winreg

            exe = sys.executable
            if getattr(sys, "frozen", False):
                cmd = f'"{exe}"'
            else:
                pyw = exe.replace("python.exe", "pythonw.exe")
                entry = str(Path(__file__).resolve().parent.parent / "app.py")
                cmd = f'"{pyw}" "{entry}"'
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            if checked:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            self.cfg.set("launch_on_startup", checked)
            self.cfg.save()
            self.act_autostart.setChecked(checked)
        except Exception as e:
            log("设置开机自启失败: " + traceback.format_exc())
            self.tray.showMessage(APP_NAME, f"设置失败：{e}", QtWidgets.QSystemTrayIcon.Warning, 4000)

    # ------------------------------------------------------------ 启动
    def boot(self):
        threading.Thread(target=self._boot_worker, daemon=True, name="boot").start()

    def _boot_worker(self):
        try:
            self.store.ensure_loaded()
            self._index_ready = self.store.ready
            if self.store.count() == 0 or not self.store.ready:
                # 首次运行：全盘建立索引
                self.indexer.start_background(
                    roots=self.cfg.get("roots") or detect_fixed_drives(), incremental=True
                ).join()
                self.store.ensure_loaded()
                self._index_ready = True
            self._start_watcher()
            self._last_rescan_check = time.time()
        except Exception:
            log("启动索引异常: " + traceback.format_exc())

    def _start_watcher(self):
        if self.watcher is not None or not self.cfg.get("watch_enabled", True):
            return
        try:
            self.watcher = Watcher(self.store, default_hot_dirs())
            self.watcher.start()
        except Exception:
            log("启动监听异常: " + traceback.format_exc())

    def _on_tick(self):
        # 托盘提示与状态栏
        if self.indexer.running:
            tip = f"{APP_NAME} · 正在建立索引 {self.indexer.total:,} 个文件"
        else:
            tip = f"{APP_NAME} · " + self.store.stats_text()
        self.tray.setToolTip(tip)
        if self.win.isVisible() and not self._pending_query.strip():
            self.win.set_results([], status_extra=self._status_text())

        # 定时重扫
        try:
            hours = float(self.cfg.get("rescan_interval_hours", 6) or 0)
        except Exception:
            hours = 0
        if hours > 0 and not self.indexer.running:
            now = time.time()
            if now - self._last_rescan_check > 300:
                self._last_rescan_check = now
                last = self.store.get_meta("last_scan")
                try:
                    stale = (not last) or (now - float(last) > hours * 3600)
                except Exception:
                    stale = True
                if stale:
                    threading.Thread(
                        target=lambda: self.indexer.start_background(
                            roots=self.cfg.get("roots") or None, incremental=True
                        ).join(),
                        daemon=True,
                    ).start()

    # ------------------------------------------------------------ 生命周期
    def run(self) -> int:
        self.boot()
        return self.app.exec()

    def quit(self):
        try:
            hotkey.unregister(int(self.win.winId()), HOTKEY_ID)
        except Exception:
            pass
        if self.watcher:
            self.watcher.stop()
        self.indexer.stop()
        try:
            self.store.close()
        except Exception:
            pass
        self.tray.hide()
        self.app.quit()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        ff = FlashFindApp(argv)
        if not ff.ensure_single_instance():
            return 0  # 已有实例在跑，把显示请求发给它后退出
        return ff.run()
    except Exception:
        log("致命错误: " + traceback.format_exc())
        raise
