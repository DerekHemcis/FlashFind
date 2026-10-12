"""FlashFind 搜索浮层界面。

无边框圆角卡片 + 阴影，全局热键唤起，键盘全流程操作。
"""

from __future__ import annotations

import math
import os
import subprocess
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import LIGHT, type_of

ROW_HEIGHT = 58
HEADER_HEIGHT = 62
FOOTER_HEIGHT = 34
WINDOW_WIDTH = 780
MAX_WINDOW_HEIGHT = 560
MAX_VISIBLE_ROWS = 8


def human_size(n: int) -> str:
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(n, 1024))) if n > 0 else 0
    i = min(i, len(units) - 1)
    v = n / (1024 ** i)
    return f"{v:.0f} {units[i]}" if i == 0 else f"{v:.1f} {units[i]}"


def human_time(ts: float) -> str:
    if not ts:
        return ""
    now = time.time()
    delta = now - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} 天前"
    t = time.localtime(ts)
    if t.tm_year == time.localtime(now).tm_year:
        return time.strftime("%m-%d", t)
    return time.strftime("%Y-%m-%d", t)


class ResultDelegate(QtWidgets.QStyledItemDelegate):
    """自绘结果行：类型徽章 + 文件名（命中高亮）+ 路径 + 时间。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t = LIGHT

    def paint(self, painter: QtGui.QPainter, option, index):
        data = index.data(QtCore.Qt.UserRole)
        if not data:
            return
        r = option.rect
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        if selected:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(self.t["selected"]))
            painter.drawRoundedRect(r.adjusted(6, 2, -6, -2), 8, 8)
        elif hovered:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(self.t["hover"]))
            painter.drawRoundedRect(r.adjusted(6, 2, -6, -2), 8, 8)

        # ---- 类型徽章
        color, label = type_of(data.get("ext", ""), data.get("is_dir", False))
        badge = QtCore.QRect(r.left() + 14, r.top() + 12, 34, 34)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(color))
        painter.drawRoundedRect(badge, 9, 9)
        f = QtGui.QFont(option.font)
        f.setPointSize(7 if len(label) > 3 else 8)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(badge, QtCore.Qt.AlignCenter, label[:4])

        # ---- 右侧信息（时间 / 大小），先算宽度好给左侧留空间
        f_info = QtGui.QFont(option.font)
        f_info.setPointSize(8)
        painter.setFont(f_info)
        fm_info = QtGui.QFontMetrics(f_info)
        info = data.get("info", "")
        info_w = fm_info.horizontalAdvance(info) if info else 0
        if info:
            info_rect = QtCore.QRect(r.right() - 14 - info_w, r.top() + 12, info_w, 18)
            painter.setPen(QtGui.QColor(self.t["text_faint"]))
            painter.drawText(info_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, info)

        # ---- 文件名（分段高亮）
        name_x = badge.right() + 12
        name_w = r.right() - 14 - name_x - (info_w + 12 if info else 0)
        f_name = QtGui.QFont(option.font)
        f_name.setPointSize(10)
        f_name.setBold(True)
        painter.setFont(f_name)
        fm_name = QtGui.QFontMetrics(f_name)

        name = data.get("name", "")
        ms = data.get("match_start", -1)
        ml = data.get("match_len", 0)
        avail = max(60, name_w)
        full = fm_name.horizontalAdvance(name)

        # 先整体省略，再在可见文本内做高亮，避免高亮段被截掉
        shown = name
        if full > avail:
            shown = fm_name.elidedText(name, QtCore.Qt.ElideMiddle, avail)

        x = name_x
        y_text = r.top() + 12
        if ms >= 0 and ml > 0:
            pre = name[:ms]
            hit = name[ms:ms + ml]
            post = name[ms + ml:]
            # 命中段必须可见：若前面过长则从左截断
            pre_w = fm_name.horizontalAdvance(pre)
            hit_w = fm_name.horizontalAdvance(hit)
            post_w = fm_name.horizontalAdvance(post)
            if pre_w + hit_w > avail and pre_w > 0:
                drop = pre_w + hit_w - avail
                shown_pre = "…" + pre[-(max(1, len(pre) - int(drop / max(1, len(pre))))):]
                pre = shown_pre if len(pre) > 3 else "…"
            for seg, is_hit in ((pre, False), (hit, True), (post, False)):
                if not seg:
                    continue
                if is_hit:
                    w = fm_name.horizontalAdvance(hit)
                    hr = QtCore.QRect(x, y_text + 1, min(w, avail), 17)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QtGui.QColor(self.t["highlight_bg"]))
                    painter.drawRoundedRect(hr, 3, 3)
                    painter.setPen(QtGui.QColor(self.t["highlight_fg"]))
                else:
                    painter.setPen(QtGui.QColor(self.t["text"]))
                seg_w = fm_name.horizontalAdvance(seg)
                if x + seg_w > name_x + avail + 2:
                    seg = fm_name.elidedText(seg, QtCore.Qt.ElideRight, max(10, name_x + avail - x))
                    painter.drawText(QtCore.QPoint(x, y_text + 14), seg)
                    x += fm_name.horizontalAdvance(seg)
                    break
                painter.drawText(QtCore.QPoint(x, y_text + 14), seg)
                x += seg_w
        else:
            painter.setPen(QtGui.QColor(self.t["text"]))
            painter.drawText(QtCore.QPoint(x, y_text + 14), shown)

        # ---- 路径
        f_path = QtGui.QFont(option.font)
        f_path.setPointSize(8)
        painter.setFont(f_path)
        fm_path = QtGui.QFontMetrics(f_path)
        path = data.get("dir", "")
        path_w = r.right() - 14 - name_x
        painter.setPen(QtGui.QColor(self.t["text_dim"]))
        painter.drawText(
            QtCore.QPoint(name_x, r.bottom() - 14),
            fm_path.elidedText(path, QtCore.Qt.ElideMiddle, max(40, path_w)),
        )
        painter.restore()

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), ROW_HEIGHT)


class SearchWindow(QtWidgets.QWidget):
    """搜索浮层窗口。"""

    resultActivated = QtCore.Signal(dict)      # 回车 / 双击：打开
    resultLocated = QtCore.Signal(dict)        # Ctrl+回车：定位到文件夹
    escapePressed = QtCore.Signal()
    queryChanged = QtCore.Signal(str)
    focusLost = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.t = LIGHT
        self._drag_pos = None
        self._results: list[dict] = []
        self._suppress_hide = False
        self._setup_window()
        self._build_ui()

    # ------------------------------------------------------------ 窗口
    def _setup_window(self):
        self.setWindowTitle("FlashFind")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setFixedWidth(WINDOW_WIDTH)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        self.card = QtWidgets.QFrame(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            f"#card{{background:{self.t['card']};border:1px solid {self.t['border']};"
            f"border-radius:14px;}}"
        )
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(120, 130, 145, 120))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        v = QtWidgets.QVBoxLayout(self.card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---------- 顶部搜索区
        head = QtWidgets.QWidget()
        head.setFixedHeight(HEADER_HEIGHT)
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(18, 12, 18, 12)
        hl.setSpacing(10)

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(18, 18)
        self._paint_magnifier()
        hl.addWidget(self.icon_label)

        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("搜索文件名…   ↑↓ 选择   Enter 打开   Ctrl+Enter 定位")
        self.edit.setStyleSheet(
            f"QLineEdit{{border:none;background:transparent;font-size:15px;"
            f"color:{self.t['text']};padding:0;}}"
        )
        fm = QtGui.QFontMetrics(self.edit.font())
        self.edit.setFixedHeight(max(30, fm.height() + 8))
        self.edit.textChanged.connect(self._on_text_changed)
        hl.addWidget(self.edit, 1)
        v.addWidget(head)

        # ---------- 分隔线
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"background:{self.t['divider']};border:none;")
        line.setFixedHeight(1)
        v.addWidget(line)

        # ---------- 结果列表
        self.list = QtWidgets.QListWidget()
        self.list.setItemDelegate(ResultDelegate(self.list))
        self.list.setSpacing(0)
        self.list.setVerticalScrollMode(QtWidgets.QListWidget.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;outline:none;}}"
            f"QScrollBar:vertical{{background:transparent;width:8px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:#D6DAE0;border-radius:4px;min-height:30px;}}"
            f"QScrollBar::handle:vertical:hover{{background:#BFC6CE;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(lambda i: self._activate(i, False))
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        v.addWidget(self.list, 1)

        # ---------- 底部状态栏
        self.footer = QtWidgets.QWidget()
        self.footer.setFixedHeight(FOOTER_HEIGHT)
        fl = QtWidgets.QHBoxLayout(self.footer)
        fl.setContentsMargins(18, 0, 18, 6)
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet(
            f"color:{self.t['text_faint']};font-size:11px;background:transparent;"
        )
        fl.addWidget(self.status, 1)
        v.addWidget(self.footer)

        self.list.installEventFilter(self)
        self.edit.installEventFilter(self)

    def _paint_magnifier(self):
        pm = QtGui.QPixmap(18, 18)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(self.t["text_dim"]))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawEllipse(2, 2, 10, 10)
        p.drawLine(10, 10, 16, 16)
        p.end()
        self.icon_label.setPixmap(pm)

    # ------------------------------------------------------------ 数据
    def set_results(self, rows: list[dict], elapsed_ms: float = 0.0, status_extra: str = ""):
        """rows: list of dict(name,dir,path,size,mtime,is_dir,ext,match_start,match_len)"""
        self._results = rows
        self.list.clear()
        for d in rows:
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(WINDOW_WIDTH - 40, ROW_HEIGHT))
            item.setData(QtCore.Qt.UserRole, d)
            self.list.addItem(item)
        if rows:
            self.list.setCurrentRow(0)
        n = len(rows)
        if elapsed_ms:
            self.set_status(f"{n} 个结果 · {elapsed_ms:.0f} ms" + (f" · {status_extra}" if status_extra else ""))
        elif status_extra:
            self.set_status(status_extra)
        else:
            self.set_status("")
        self._resize_to_content()

    def set_status(self, text: str):
        self.status.setText(text)

    def _resize_to_content(self):
        n = len(self._results)
        rows = min(n, MAX_VISIBLE_ROWS)
        if n == 0:
            h = HEADER_HEIGHT + 1 + FOOTER_HEIGHT + 8
        else:
            h = HEADER_HEIGHT + 1 + rows * ROW_HEIGHT + FOOTER_HEIGHT + 8
        self.setFixedHeight(min(MAX_WINDOW_HEIGHT, max(120, h + 28)))
        self.list.setVisible(n > 0)

    # ------------------------------------------------------------ 交互
    def _on_text_changed(self, text: str):
        self.queryChanged.emit(text)

    def _activate(self, item, locate: bool):
        d = item.data(QtCore.Qt.UserRole) if item else None
        if not d:
            return
        (self.resultLocated if locate else self.resultActivated).emit(d)

    def _show_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        d = item.data(QtCore.Qt.UserRole)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:#fff;border:1px solid {self.t['border']};border-radius:8px;"
            f"padding:6px;}}"
            f"QMenu::item{{padding:7px 22px;border-radius:5px;color:{self.t['text']};}}"
            f"QMenu::item:selected{{background:{self.t['selected']};}}"
        )
        menu.addAction("打开", lambda: self.resultActivated.emit(d))
        menu.addAction("在资源管理器中显示", lambda: self.resultLocated.emit(d))
        menu.addSeparator()
        menu.addAction("复制完整路径", lambda: QtWidgets.QApplication.clipboard().setText(d["path"]))
        menu.addAction("复制所在文件夹", lambda: QtWidgets.QApplication.clipboard().setText(d["dir"]))
        self._suppress_hide = True
        menu.exec(self.list.mapToGlobal(pos))
        # 菜单关闭后恢复失焦自动收起
        QtCore.QTimer.singleShot(200, self._clear_suppress)

    def _clear_suppress(self):
        self._suppress_hide = False

    def event(self, e):
        if e.type() == QtCore.QEvent.WindowDeactivate:
            self.focusLost.emit()
        return super().event(e)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                cur = self.list.currentRow()
                n = self.list.count()
                if n:
                    nxt = min(n - 1, cur + 1) if key == QtCore.Qt.Key_Down else max(0, cur - 1)
                    self.list.setCurrentRow(nxt)
                    self.list.scrollToItem(self.list.item(nxt))
                return True
            if key == QtCore.Qt.Key_PageDown:
                if self.list.count():
                    self.list.setCurrentRow(min(self.list.count() - 1, self.list.currentRow() + MAX_VISIBLE_ROWS))
                return True
            if key == QtCore.Qt.Key_PageUp:
                if self.list.count():
                    self.list.setCurrentRow(max(0, self.list.currentRow() - MAX_VISIBLE_ROWS))
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                locate = bool(mods & QtCore.Qt.ControlModifier)
                self._activate(self.list.currentItem(), locate)
                return True
            if key == QtCore.Qt.Key_Escape:
                self.escapePressed.emit()
                return True
            if key == QtCore.Qt.Key_Tab:
                return True
        if obj is self.list and event.type() == QtCore.QEvent.MouseButtonPress:
            return False
        return super().eventFilter(obj, event)

    # 无边框窗口拖动
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ------------------------------------------------------------ 显隐
    def show_at_cursor_screen(self):
        """居中偏上显示，抢焦点并全选已有文本。"""
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.adjustSize()
        x = geo.center().x() - self.width() // 2
        y = geo.top() + int(geo.height() * 0.16)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        # Windows 上抢焦点：Qt 的 activateWindow 有时被系统拦下
        try:
            import ctypes

            hwnd = int(self.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.edit.setFocus()
        self.edit.selectAll()
