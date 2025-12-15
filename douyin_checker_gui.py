# -*- coding: utf-8 -*-
import sys
import time
import threading
import queue
import os
import re
from typing import List

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QLibraryInfo
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QLabel,
    QFileDialog,
    QSpinBox,
    QProgressBar,
)

from douyin_check import check_douyin_jump, check_weibo_jump


DOMAIN_REGEX = re.compile(r"\b(?:(?:[a-zA-Z0-9][a-zA-Z0-9-]*\.)+[a-zA-Z]{2,})\b")


def extract_domains_from_text(text: str) -> List[str]:
    """从任意文本中提取域名列表（去重、小写）。"""
    seen = set()
    domains: List[str] = []
    for match in DOMAIN_REGEX.findall(text):
        domain = match.lower()
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


class WorkerManager(QObject):
    """管理检测线程和任务队列。"""

    logMessage = pyqtSignal(str)
    resultReady = pyqtSignal(str, str)  # url, status
    statsUpdate = pyqtSignal(int, int, int, int)  # total, checked, normal, blocked
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[str] = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()

        self.total = 0
        self.checked = 0
        self.normal = 0
        self.blocked = 0
        self._active_threads = 0

        # 当前检测平台："douyin" 或 "weibo"
        self.mode: str = "douyin"

    def start(self, urls: List[str], num_threads: int, mode: str = "douyin") -> None:
        """开始一轮新的检测。"""
        # 重置状态
        with self._lock:
            self.total = len(urls)
            self.checked = 0
            self.normal = 0
            self.blocked = 0

        self.mode = mode

        self._stop.clear()
        self._pause.clear()

        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        # 填充新的任务
        for u in urls:
            self._queue.put(u)

        self._threads = []
        self._active_threads = num_threads

        for i in range(num_threads):
            t = threading.Thread(target=self._thread_entry, name=f"Checker-{i+1}", daemon=True)
            self._threads.append(t)
            t.start()

    def _thread_entry(self) -> None:
        try:
            self._worker_loop()
        finally:
            with self._lock:
                self._active_threads -= 1
                if self._active_threads <= 0:
                    self.finished.emit()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            # 暂停时简单等待
            while self._pause.is_set() and not self._stop.is_set():
                time.sleep(0.1)

            try:
                url = self._queue.get_nowait()
            except queue.Empty:
                break

            status = "error"
            try:
                if self.mode == "douyin":
                    result = check_douyin_jump(url)
                elif self.mode == "weibo":
                    result = check_weibo_jump(url)
                else:
                    raise ValueError(f"未知检测模式: {self.mode}")

                status = result.get("status", "error")
            except Exception as exc:  # 保底防止单个链接异常导致线程退出
                self.logMessage.emit(f"检测出错：{url} -> {exc}")
                status = "error"

            # 显示用中文状态
            if status == "ok":
                status_text = "正常"
            elif status == "blocked":
                status_text = "拦截"
            elif status == "unknown":
                status_text = "未知"
            else:
                status_text = "错误"

            with self._lock:
                self.checked += 1
                if status == "ok":
                    self.normal += 1
                elif status == "blocked":
                    self.blocked += 1

                total = self.total
                checked = self.checked
                normal = self.normal
                blocked = self.blocked

            # 发出信号更新 UI
            self.resultReady.emit(url, status)
            self.statsUpdate.emit(total, checked, normal, blocked)
            self.logMessage.emit(f"{url} -> {status_text}")

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("抖音检测工具")
        self.resize(1000, 600)

        self.manager = WorkerManager()
        self._running = False
        self._start_time: float | None = None
        self._current_mode: str = "douyin"  # 当前检测平台

        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        # 不做重度自定义皮肤，更多依赖 macOS 自带样式，只稍微调整边距和占位提示
        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # 顶部控制区域：导入、线程配置、开始抖音/微博检测、暂停
        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)

        self.btn_import = QPushButton("导入检测文件")
        self.btn_start_douyin = QPushButton("开始检测抖音")
        self.btn_start_weibo = QPushButton("开始检测微博")
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setEnabled(False)

        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 64)
        self.spin_threads.setValue(10)
        self.spin_threads.setToolTip("检测线程数")

        lbl_threads = QLabel("线程数：")

        top_layout.addWidget(self.btn_import)
        top_layout.addSpacing(10)
        top_layout.addWidget(lbl_threads)
        top_layout.addWidget(self.spin_threads)
        top_layout.addSpacing(20)
        top_layout.addWidget(self.btn_start_douyin)
        top_layout.addWidget(self.btn_start_weibo)
        top_layout.addWidget(self.btn_pause)
        top_layout.addStretch(1)

        main_layout.addLayout(top_layout)

        # 按钮下方导航统计栏
        self.label_stats = QLabel("待检测域名：0    已检测域名：0    正常链接：0    拦截链接：0    QPS：0.00    耗时：0.0s")
        main_layout.addWidget(self.label_stats)

        # 中间三列区域
        center_layout = QHBoxLayout()
        center_layout.setSpacing(10)

        # 左列：链接输入框
        left_layout = QVBoxLayout()
        left_layout.setSpacing(4)
        lbl_left = QLabel("待检测链接（每行一个）：")
        self.edit_input = QPlainTextEdit()
        self.edit_input.setPlaceholderText("在这里粘贴或输入待检测链接，每行一个，例如：\nhttps://example.com")
        left_layout.addWidget(lbl_left)
        left_layout.addWidget(self.edit_input)

        # 中间：检测日志
        middle_layout = QVBoxLayout()
        middle_layout.setSpacing(4)
        lbl_middle = QLabel("检测日志：")
        self.edit_log = QPlainTextEdit()
        self.edit_log.setReadOnly(True)
        self.edit_log.setPlaceholderText("检测过程中的详细日志会显示在这里……")
        middle_layout.addWidget(lbl_middle)
        middle_layout.addWidget(self.edit_log)

        # 右列：正常链接
        right_layout = QVBoxLayout()
        right_layout.setSpacing(4)
        lbl_right = QLabel("正常链接：")
        self.edit_ok = QPlainTextEdit()
        self.edit_ok.setReadOnly(True)
        self.edit_ok.setPlaceholderText("检测为【正常】的链接会显示在这里，方便复制导出。")
        right_layout.addWidget(lbl_right)
        right_layout.addWidget(self.edit_ok)

        center_layout.addLayout(left_layout, 3)
        center_layout.addLayout(middle_layout, 4)
        center_layout.addLayout(right_layout, 3)

        main_layout.addLayout(center_layout)

        # 底部状态栏 + 进度条（保持简洁，使用系统默认样式）
        status = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status.addPermanentWidget(self.progress, 1)

        # 定时器用于更新 QPS / 耗时
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_qps_and_time)

    def _connect_signals(self) -> None:
        self.btn_import.clicked.connect(self._import_file)
        self.btn_start_douyin.clicked.connect(self._start_check_douyin)
        self.btn_start_weibo.clicked.connect(self._start_check_weibo)
        self.btn_pause.clicked.connect(self._toggle_pause)

        self.manager.logMessage.connect(self._append_log)
        self.manager.resultReady.connect(self._handle_result)
        self.manager.statsUpdate.connect(self._update_stats_counts)
        self.manager.finished.connect(self._on_finished)

    # ---------- 顶部按钮逻辑 ----------

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择待检测域名文件", "", "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as exc:
            self._append_log(f"读取文件失败：{exc}")
            return

        domains = extract_domains_from_text(content)
        if not domains:
            self._append_log("未在文件中识别到有效域名，请检查文件内容。")
            return

        self.edit_input.setPlainText("\n".join(domains))
        self._append_log(f"已从文件中提取 {len(domains)} 个域名。")

    def _start_check_douyin(self) -> None:
        self._start_check(mode="douyin")

    def _start_check_weibo(self) -> None:
        self._start_check(mode="weibo")

    def _start_check(self, mode: str) -> None:
        if self._running:
            return

        # 从输入框的任意文本中提取域名
        raw_text = self.edit_input.toPlainText()
        unique_urls = extract_domains_from_text(raw_text)

        if not unique_urls:
            self._append_log("没有识别到待检测的域名，请在左侧输入或导入包含域名的文本。")
            return

        self._current_mode = mode

        num_threads = int(self.spin_threads.value())

        # 清空上次结果
        self.edit_log.clear()
        self.edit_ok.clear()

        self._running = True
        self._start_time = time.monotonic()

        total = len(unique_urls)
        self.progress.setMaximum(total)
        self.progress.setValue(0)

        self.manager.start(unique_urls, num_threads=num_threads, mode=mode)

        self.btn_start_douyin.setEnabled(False)
        self.btn_start_weibo.setEnabled(False)
        self.btn_import.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停")

        self.timer.start()
        self._refresh_stats_label(total, 0, 0, 0, 0.0, 0.0)

        mode_text = "抖音" if mode == "douyin" else "微博"
        self._append_log(f"开始{mode_text}检测，共 {total} 条链接，线程数：{num_threads}。")

    def _toggle_pause(self) -> None:
        if not self._running:
            return
        if not self.manager.is_paused():
            self.manager.pause()
            self.btn_pause.setText("继续")
            self._append_log("已暂停检测。")
        else:
            self.manager.resume()
            self.btn_pause.setText("暂停")
            self._append_log("已继续检测。")

    # ---------- WorkerManager 回调 ----------

    def _append_log(self, text: str) -> None:
        self.edit_log.appendPlainText(text)
        # 自动滚动到底部
        cursor = self.edit_log.textCursor()
        cursor.movePosition(cursor.End)
        self.edit_log.setTextCursor(cursor)

    def _handle_result(self, url: str, status: str) -> None:
        # 只在右侧展示正常链接
        if status == "ok":
            self.edit_ok.appendPlainText(url)

    def _update_stats_counts(self, total: int, checked: int, normal: int, blocked: int) -> None:
        # 更新进度条
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(checked)

        # QPS / 耗时在定时器里更新，这里先用 0 占位
        elapsed = 0.0
        qps = 0.0
        if self._start_time is not None:
            elapsed = max(time.monotonic() - self._start_time, 0.0)
            if elapsed > 0:
                qps = checked / elapsed

        pending = max(total - checked, 0)
        self._refresh_stats_label(pending, checked, normal, blocked, qps, elapsed)

    def _on_finished(self) -> None:
        self._running = False
        self.timer.stop()

        self.btn_start_douyin.setEnabled(True)
        self.btn_start_weibo.setEnabled(True)
        self.btn_import.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停")

        self._append_log("检测完成。")

    # ---------- 统计 / QPS / 耗时 ----------

    def _refresh_stats_label(
        self,
        pending: int,
        checked: int,
        normal: int,
        blocked: int,
        qps: float,
        elapsed: float,
    ) -> None:
        self.label_stats.setText(
            f"待检测域名：{pending}    已检测域名：{checked}    正常链接：{normal}    "
            f"拦截链接：{blocked}    QPS：{qps:.2f}    耗时：{elapsed:.1f}s"
        )

    def _update_qps_and_time(self) -> None:
        if not self._running or self._start_time is None:
            return

        # 从统计标签中取数值需要解析文本，比较繁琐
        # 这里简单做：只更新 QPS / 耗时部分，其余通过最近一次 statsUpdate 保持
        # 为保持实现简单，我们不解析现有文本，而是在上次保存的计数基础上更新
        # 因此在 _update_stats_counts 中已完整刷新一次，这里只在没有新结果时更新耗时/QPS
        text = self.label_stats.text()
        # 简单分割获取当前计数
        try:
            parts = text.split()
            pending = int(parts[0].split("：")[1])
            checked = int(parts[1].split("：")[1])
            normal = int(parts[2].split("：")[1])
            blocked = int(parts[3].split("：")[1])
        except Exception:
            return

        elapsed = max(time.monotonic() - self._start_time, 0.0)
        qps = (checked / elapsed) if elapsed > 0 else 0.0
        self._refresh_stats_label(pending, checked, normal, blocked, qps, elapsed)


def main() -> None:
    # 确保 Qt 能正确找到 platform 插件目录（例如 macOS 下的 cocoa）
    plugins_path = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    platforms_path = os.path.join(plugins_path, "platforms")
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", platforms_path)

    app = QApplication(sys.argv)
    # 使用 macOS 风格（在支持的系统上会更贴近原生外观）
    app.setStyle("macintosh")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
