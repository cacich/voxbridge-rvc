"""Traditional Chinese Windows desktop interface for VoxBridge."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from voxbridge import __version__
from voxbridge.settings import AppSettings, data_dir, load_settings, save_settings


class DownloadWorker(QObject):
    progress = Signal(str)
    finished = Signal(str)

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory

    @Slot()
    def run(self) -> None:
        try:
            from voxbridge.assets import download_assets

            download_assets(self.directory, self.progress.emit)
            self.finished.emit("")
        except Exception as exc:  # Show the precise download failure in the interface.
            self.finished.emit(str(exc))


class VoxBridgeWindow(QMainWindow):
    def __init__(
        self,
        *,
        smoke_test: bool = False,
        service: Any | None = None,
        devices: list[Any] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"VoxBridge {__version__} Beta — RVC 即時變聲")
        self.resize(900, 820)
        self.setMinimumSize(740, 650)
        self._smoke_test = smoke_test
        self._closing = False
        self._download_thread: QThread | None = None
        self._download_worker: DownloadWorker | None = None
        self._state = "stopped"
        self._service = service
        self._settings_notice = ""
        try:
            self.settings = load_settings()
        except (OSError, ValueError, TypeError) as exc:
            self.settings = AppSettings(assets_dir=str(data_dir() / "assets"))
            self._settings_notice = f"設定檔無法讀取，已採用預設值：{exc}"

        if devices is not None:
            self._devices = devices
        elif smoke_test:
            self._devices = []
        else:
            try:
                from voxbridge.audio import list_devices

                self._devices = list_devices()
            except Exception as exc:
                self._devices = []
                self._settings_notice = f"無法列出音訊裝置：{exc}"

        if self._service is None and not smoke_test:
            from voxbridge.audio import AudioService

            self._service = AudioService()

        self._build_ui()
        self._populate_settings()
        self._connect_live_controls()
        self._refresh_controls()
        if self._settings_notice:
            self._notice(self._settings_notice, error=True)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll)
        if not smoke_test:
            self._poll_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QScrollArea, QWidget#page, QWidget#root { background: #101722; color: #eaf0f7; }
            QFrame#footer { background: #172435; border-top: 1px solid #3d5368; }
            QGroupBox { background: #192536; border: 1px solid #34465a; border-radius: 10px;
                margin-top: 16px; padding: 16px 14px 12px; font-weight: 600; color: #eaf0f7; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
            QLabel { color: #eaf0f7; }
            QLabel#muted { color: #a9bdce; }
            QLabel#notice { background: #253447; border-radius: 6px; padding: 9px; }
            QLineEdit, QComboBox, QSpinBox { background: #101b29; color: #f4f8fd;
                border: 1px solid #4c6276; border-radius: 6px; padding: 6px; min-height: 23px; }
            QComboBox QAbstractItemView { background: #192536; color: #eaf0f7; selection-background-color: #315f8d; }
            QPushButton { background: #315f8d; color: #fff; border: 1px solid #4a7fad;
                border-radius: 6px; padding: 7px 14px; font-weight: 600; }
            QPushButton:hover { background: #3d729f; }
            QPushButton:disabled { background: #344457; color: #899aaa; border-color: #46586a; }
            QPushButton#start { background: #187a68; border-color: #31a68d; }
            QPushButton#stop { background: #9a493f; border-color: #c46a5d; }
            QProgressBar { background: #101b29; border: 1px solid #4c6276; border-radius: 5px;
                color: #eaf0f7; min-height: 16px; text-align: center; }
            QProgressBar::chunk { background: #35ad9b; border-radius: 4px; }
            QCheckBox { spacing: 8px; color: #eaf0f7; }
            QSlider::groove:horizontal { height: 6px; background: #42566a; border-radius: 3px; }
            QSlider::handle:horizontal { width: 16px; margin: -5px 0; background: #64b4e5; border-radius: 8px; }
            """
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(14)

        title = QLabel("VoxBridge")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #ffffff;")
        subtitle = QLabel("RVC 即時變聲  ·  Windows 11  ·  0.1.0 Beta")
        subtitle.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.notice_label = QLabel("")
        self.notice_label.setObjectName("notice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        layout.addWidget(self.notice_label)

        model = QGroupBox("1  聲線與必要檔案")
        mf = QFormLayout(model)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("選擇已訓練的 RVC .pth 模型")
        self.model_browse = QPushButton("瀏覽…")
        mf.addRow("RVC 模型 (.pth)", self._path_row(self.model_edit, self.model_browse))
        self.index_edit = QLineEdit()
        self.index_edit.setPlaceholderText("可選；不使用時檢索比例請設為 0")
        self.index_browse = QPushButton("瀏覽…")
        mf.addRow("索引 (.index，可選)", self._path_row(self.index_edit, self.index_browse))
        self.assets_edit = QLineEdit()
        self.assets_browse = QPushButton("瀏覽…")
        mf.addRow("引擎檔案位置", self._path_row(self.assets_edit, self.assets_browse))
        self.download_button = QPushButton("下載必要的引擎檔案")
        self.download_status = QLabel("首次使用需下載約 370 MB 的 HuBERT 與 RMVPE 基礎模型檔。")
        self.download_status.setObjectName("muted")
        self.download_status.setWordWrap(True)
        download_row = QHBoxLayout()
        download_row.addWidget(self.download_button)
        download_row.addWidget(self.download_status, 1)
        mf.addRow("", self._wrap(download_row))
        layout.addWidget(model)

        audio = QGroupBox("2  音訊路由")
        af = QFormLayout(audio)
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self.monitor_combo = QComboBox()
        self._fill_devices(self.input_combo, "inputs", "選擇麥克風")
        self._fill_devices(self.output_combo, "outputs", "選擇變聲輸出，例如 CABLE Input")
        self._fill_devices(self.monitor_combo, "outputs", "選擇耳機或喇叭")
        af.addRow("麥克風", self.input_combo)
        af.addRow("變聲輸出", self.output_combo)
        af.addRow("監聽裝置", self.monitor_combo)
        self.monitor_checkbox = QCheckBox("聽到自己的變聲（預設關閉）")
        self.monitor_checkbox.setToolTip("只控制你聽到的聲音；Discord 的變聲輸出會繼續。建議使用耳機避免回授。")
        af.addRow("本機監聽", self.monitor_checkbox)
        self.monitor_slider, self.monitor_value = self._gain_controls()
        af.addRow("監聽音量", self._slider_row(self.monitor_slider, self.monitor_value))
        self.output_slider, self.output_value = self._gain_controls()
        af.addRow("變聲輸出音量", self._slider_row(self.output_slider, self.output_value))
        layout.addWidget(audio)

        tuning = QGroupBox("3  轉換設定")
        tf = QFormLayout(tuning)
        self.compute_combo = QComboBox()
        self.compute_combo.addItem("NVIDIA GPU（CUDA，建議）", "cuda")
        self.compute_combo.addItem("CPU（較慢）", "cpu")
        tf.addRow("運算裝置", self.compute_combo)
        self.pitch_spin = QSpinBox()
        self.pitch_spin.setRange(-24, 24)
        self.pitch_spin.setSuffix(" 半音")
        tf.addRow("音高調整", self.pitch_spin)
        self.index_slider = QSlider(Qt.Orientation.Horizontal)
        self.index_slider.setRange(0, 100)
        self.index_value = QLabel("0%")
        self.index_value.setMinimumWidth(42)
        tf.addRow("索引檢索比例", self._slider_row(self.index_slider, self.index_value))
        self.block_combo = QComboBox()
        for size in (100, 150, 200, 250, 300, 400):
            self.block_combo.addItem(f"{size} ms", size)
        tf.addRow("處理區塊", self.block_combo)
        hint = QLabel("較小區塊通常較快，但可能增加卡頓；變更後需重新開始。")
        hint.setObjectName("muted")
        tf.addRow("", hint)
        layout.addWidget(tuning)

        run = QGroupBox("4  執行與狀態")
        rv = QVBoxLayout(run)
        meters = QGridLayout()
        self.input_meter = QProgressBar()
        self.output_meter = QProgressBar()
        for meter in (self.input_meter, self.output_meter):
            meter.setRange(0, 100)
            meter.setTextVisible(False)
        meters.addWidget(QLabel("麥克風"), 0, 0)
        meters.addWidget(self.input_meter, 0, 1)
        meters.addWidget(QLabel("變聲輸出"), 1, 0)
        meters.addWidget(self.output_meter, 1, 1)
        rv.addLayout(meters)
        self.metrics_label = QLabel("處理時間：— ms（僅引擎處理時間，非端到端延遲）")
        self.metrics_label.setObjectName("muted")
        self.metrics_label.setWordWrap(True)
        rv.addWidget(self.metrics_label)
        self.diagnostic_button = QPushButton("匯出診斷資料…")
        rv.addWidget(self.diagnostic_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(run)

        help_box = QGroupBox("Discord 快速設定")
        hv = QVBoxLayout(help_box)
        help_text = QLabel(
            "① 安裝虛擬音訊線（例如 VB-CABLE）。在 VoxBridge 將「變聲輸出」選為 CABLE Input。\n"
            "② 在 Discord 的「語音與視訊」將輸入裝置選為 CABLE Output。\n"
            "③ 若聲音失真，試著關閉 Discord 的雜音抑制、自動增益與回音消除。\n"
            "④ 想聽到自己的變聲時，選擇耳機作為監聽裝置並開啟監聽。"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("line-height: 1.5;")
        hv.addWidget(help_text)
        layout.addWidget(help_box)
        layout.addStretch(1)
        scroll.setWidget(page)
        root = QWidget()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(scroll, 1)
        footer = QFrame()
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 10, 24, 10)
        self.start_button = QPushButton("開始變聲")
        self.start_button.setObjectName("start")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stop")
        footer_layout.addWidget(self.start_button)
        footer_layout.addWidget(self.stop_button)
        footer_layout.addStretch(1)
        self.state_label = QLabel("已停止")
        self.state_label.setStyleSheet("font-weight: 600;")
        footer_layout.addWidget(self.state_label)
        root_layout.addWidget(footer)
        self.setCentralWidget(root)

        self.model_browse.clicked.connect(lambda: self._browse_file(self.model_edit, "RVC 模型 (*.pth)"))
        self.index_browse.clicked.connect(lambda: self._browse_file(self.index_edit, "RVC 索引 (*.index)"))
        self.assets_browse.clicked.connect(self._browse_assets)
        self.download_button.clicked.connect(self._download_assets)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.diagnostic_button.clicked.connect(self._export_diagnostics)
        self.index_slider.valueChanged.connect(lambda n: self.index_value.setText(f"{n}%"))

    @staticmethod
    def _wrap(layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    @staticmethod
    def _path_row(edit: QLineEdit, browse: QPushButton) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return VoxBridgeWindow._wrap(row)

    @staticmethod
    def _slider_row(slider: QSlider, label: QLabel) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(slider, 1)
        row.addWidget(label)
        return VoxBridgeWindow._wrap(row)

    @staticmethod
    def _gain_controls() -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 200)
        label = QLabel("100%")
        label.setMinimumWidth(48)
        slider.valueChanged.connect(lambda value: label.setText(f"{value}%"))
        return slider, label

    def _fill_devices(self, combo: QComboBox, capability: str, placeholder: str) -> None:
        combo.addItem(placeholder, "")
        for device in self._devices:
            if getattr(device, capability, 0) > 0:
                combo.addItem(device.label, device.key)

    @staticmethod
    def _select_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _populate_settings(self) -> None:
        s = self.settings
        self._applied_monitor_enabled = s.monitor_enabled
        self.model_edit.setText(s.model_path)
        self.index_edit.setText(s.index_path)
        self.assets_edit.setText(s.assets_dir)
        self._select_data(self.input_combo, s.input_device)
        self._select_data(self.output_combo, s.output_device)
        self._select_data(self.monitor_combo, s.monitor_device)
        self.monitor_checkbox.setChecked(s.monitor_enabled)
        self.monitor_slider.setValue(round(s.monitor_gain * 100))
        self.output_slider.setValue(round(s.output_gain * 100))
        self._select_data(self.compute_combo, s.device)
        self.pitch_spin.setValue(s.pitch)
        self.index_slider.setValue(round(s.index_rate * 100))
        self._select_data(self.block_combo, s.block_ms)

    def _connect_live_controls(self) -> None:
        self.monitor_checkbox.toggled.connect(self._update_monitor)
        self.monitor_slider.valueChanged.connect(self._update_monitor)
        self.output_slider.valueChanged.connect(self._update_output_gain)

    def _settings_from_ui(self) -> AppSettings:
        return AppSettings(
            model_path=self.model_edit.text().strip(),
            index_path=self.index_edit.text().strip(),
            assets_dir=self.assets_edit.text().strip(),
            input_device=self.input_combo.currentData() or "",
            output_device=self.output_combo.currentData() or "",
            monitor_device=self.monitor_combo.currentData() or "",
            monitor_enabled=self.monitor_checkbox.isChecked(),
            monitor_gain=self.monitor_slider.value() / 100,
            output_gain=self.output_slider.value() / 100,
            pitch=self.pitch_spin.value(),
            index_rate=self.index_slider.value() / 100,
            device=self.compute_combo.currentData(),
            block_ms=self.block_combo.currentData(),
        )

    def _save(self) -> bool:
        try:
            self.settings = self._settings_from_ui()
            save_settings(self.settings)
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._notice(f"無法儲存設定：{exc}", error=True)
            return False

    def _notice(self, message: str, *, error: bool = False) -> None:
        self.notice_label.setText(message)
        self.notice_label.setStyleSheet(
            "background: #573039; color: #ffe4e7; border-radius: 6px; padding: 9px;"
            if error else "background: #254335; color: #e0f8eb; border-radius: 6px; padding: 9px;"
        )
        self.notice_label.show()

    def _browse_file(self, edit: QLineEdit, pattern: str) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "選擇檔案", edit.text(), pattern)
        if selected:
            edit.setText(selected)

    def _browse_assets(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "選擇引擎檔案資料夾", self.assets_edit.text())
        if selected:
            self.assets_edit.setText(selected)

    def _download_assets(self) -> None:
        if self._download_thread is not None:
            return
        directory = self.assets_edit.text().strip()
        if not directory:
            self._notice("請先選擇引擎檔案資料夾。", error=True)
            return
        self.download_status.setText("準備下載…")
        self.download_button.setEnabled(False)
        thread = QThread(self)
        worker = DownloadWorker(Path(directory))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.download_status.setText)
        worker.finished.connect(self._download_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._download_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._download_thread = thread
        self._download_worker = worker
        thread.start()
        self._refresh_controls()

    @Slot(str)
    def _download_finished(self, error: str) -> None:
        if error:
            self.download_status.setText("下載失敗")
            self._notice(f"下載引擎檔案失敗：{error}", error=True)
        else:
            self.download_status.setText("引擎檔案下載完成。")
            self._notice("引擎檔案已就緒。")

    @Slot()
    def _download_thread_finished(self) -> None:
        self._download_thread = None
        self._download_worker = None
        self._refresh_controls()
        self._maybe_finish_close()

    def _start(self) -> None:
        if self._service is None or self._state not in ("stopped", "error"):
            return
        settings = self._settings_from_ui()
        if not settings.model_path or not Path(settings.model_path).is_file():
            self._notice("請先選擇存在的 RVC .pth 模型。", error=True)
            return
        if settings.index_path and not Path(settings.index_path).is_file():
            self._notice("所選 .index 索引檔不存在。", error=True)
            return
        if not settings.input_device or not settings.output_device:
            self._notice("請選擇麥克風與變聲輸出裝置。", error=True)
            return
        if settings.monitor_device and settings.monitor_device == settings.output_device:
            self._notice("監聽與變聲輸出需選擇不同裝置，避免重複播放。", error=True)
            return
        if settings.monitor_enabled and not settings.monitor_device:
            self._notice("開啟監聽前，請選擇耳機或喇叭。", error=True)
            return
        if settings.index_rate > 0 and not settings.index_path:
            self._notice("索引檢索比例大於 0 時，請選擇 .index 檔，或將比例設為 0。", error=True)
            return
        try:
            from voxbridge.assets import check_assets

            missing = check_assets(Path(settings.assets_dir))
            if missing:
                self._notice("缺少引擎檔案，請先按「下載必要的引擎檔案」：" + "、".join(map(str, missing)), error=True)
                return
            if not self._save():
                return
            self._service.start(settings)
            self._applied_monitor_enabled = settings.monitor_enabled
            self._state = "loading"
            self._notice("正在載入模型，介面會保持可操作。")
            self._refresh_controls()
        except Exception as exc:
            self._state = "error"
            self._notice(f"無法開始變聲：{exc}", error=True)
            self._refresh_controls()

    def _stop(self) -> None:
        if self._service is None:
            return
        try:
            self._service.stop()
            self._state = "stopping"
            self._refresh_controls()
        except Exception as exc:
            self._notice(f"停止變聲時發生錯誤：{exc}", error=True)

    def _update_monitor(self, *_: Any) -> None:
        if self.monitor_checkbox.isChecked() and not self.monitor_combo.currentData():
            self.monitor_checkbox.blockSignals(True)
            self.monitor_checkbox.setChecked(False)
            self.monitor_checkbox.blockSignals(False)
            self._notice("請先停止變聲並選擇監聽裝置，再開啟監聽。", error=True)
            return
        if self._service is not None and self._state in ("loading", "running"):
            try:
                self._service.set_monitor(self.monitor_checkbox.isChecked(), self.monitor_slider.value() / 100)
                self._applied_monitor_enabled = self.monitor_checkbox.isChecked()
            except Exception as exc:
                self.monitor_checkbox.blockSignals(True)
                self.monitor_checkbox.setChecked(self._applied_monitor_enabled)
                self.monitor_checkbox.blockSignals(False)
                self._notice(f"無法更新監聽：{exc}", error=True)

    def _update_output_gain(self, *_: Any) -> None:
        if self._service is not None and self._state in ("loading", "running"):
            try:
                self._service.set_output_gain(self.output_slider.value() / 100)
            except Exception as exc:
                self._notice(f"無法更新輸出音量：{exc}", error=True)

    def _snapshot(self) -> dict[str, Any]:
        if self._service is None:
            return {"state": "stopped"}
        try:
            return self._service.snapshot()
        except Exception as exc:
            return {"state": "error", "error": str(exc)}

    def _poll(self) -> None:
        snapshot = self._snapshot()
        state = snapshot.get("state", "error")
        if state != self._state:
            self._state = state
            if state == "error":
                self._notice(f"變聲引擎發生錯誤：{snapshot.get('error') or '未知錯誤'}", error=True)
            elif state == "running":
                self._notice("變聲正在輸出。請在 Discord 選擇 CABLE Output 作為輸入。")
            self._refresh_controls()
        labels = {"stopped": "已停止", "loading": "正在載入模型…", "running": "變聲中", "stopping": "正在停止…", "error": "發生錯誤"}
        self.state_label.setText(labels.get(state, state))
        self.input_meter.setValue(self._meter_value(snapshot.get("input_rms", 0)))
        self.output_meter.setValue(self._meter_value(snapshot.get("output_rms", 0)))
        process = snapshot.get("process_ms", 0)
        try:
            process_text = f"{float(process):.0f}"
        except (TypeError, ValueError):
            process_text = "—"
        self.metrics_label.setText(
            f"處理時間：{process_text} ms（僅引擎處理時間，非端到端延遲）  ·  "
            f"輸入丟失：{snapshot.get('input_drops', 0)}  ·  "
            f"輸出欠載：{snapshot.get('output_underruns', 0)}  ·  "
            f"監聽欠載：{snapshot.get('monitor_underruns', 0)}  ·  "
            f"處理超時：{snapshot.get('overruns', 0)}"
        )
        self._maybe_finish_close()

    @staticmethod
    def _meter_value(rms: Any) -> int:
        try:
            return max(0, min(100, round(float(rms) * 300)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _refresh_controls(self) -> None:
        locked = self._state in ("loading", "running", "stopping") or self._download_thread is not None
        for widget in (
            self.model_edit, self.model_browse, self.index_edit, self.index_browse,
            self.assets_edit, self.assets_browse, self.input_combo, self.output_combo,
            self.monitor_combo, self.compute_combo, self.pitch_spin, self.index_slider,
            self.block_combo,
        ):
            widget.setEnabled(not locked)
        self.download_button.setEnabled(not locked)
        self.start_button.setEnabled(not locked and self._service is not None)
        self.stop_button.setEnabled(self._state in ("loading", "running"))
        self.monitor_checkbox.setEnabled(self._state != "stopping")
        self.monitor_slider.setEnabled(self._state != "stopping")
        self.output_slider.setEnabled(self._state != "stopping")

    def _export_diagnostics(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "儲存診斷資料", str(Path.home() / "VoxBridge-diagnostics.json"), "JSON 檔案 (*.json)"
        )
        if not filename:
            return
        try:
            from voxbridge.diagnostics import export_diagnostics

            destination = export_diagnostics(
                Path(filename), self._settings_from_ui(), self._snapshot(), self._devices
            )
            self._notice(f"診斷資料已儲存：{destination}")
        except Exception as exc:
            self._notice(f"無法匯出診斷資料：{exc}", error=True)

    def closeEvent(self, event: Any) -> None:
        self._closing = True
        if not self._smoke_test:
            self._save()
        if self._state in ("loading", "running"):
            self._stop()
        if self._state == "stopping" or self._download_thread is not None:
            self._notice("正在結束音訊工作；下載完成後會關閉視窗。")
            event.ignore()
            return
        event.accept()

    def _maybe_finish_close(self) -> None:
        if self._closing and self._state not in ("loading", "running", "stopping") and self._download_thread is None:
            QTimer.singleShot(0, self.close)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoxBridge RVC 即時變聲桌面介面")
    parser.add_argument("--smoke-test", action="store_true", help="建立介面後立即結束，不載入音訊引擎")
    parser.add_argument("--check-engine", action="store_true", help="檢查封裝版是否包含推論模組，不載入模型或音訊裝置")
    parser.add_argument("--check-engine-report", type=Path, metavar="PATH", help="將推論模組檢查結果寫為 JSON")
    args = parser.parse_args(argv)
    if args.check_engine:
        error = ""
        try:
            for name in ("torch", "faiss", "librosa", "scipy", "voxbridge.vendor.module.models", "voxbridge.vendor.rmvpe"):
                importlib.import_module(name)
            from transformers import HubertModel  # noqa: F401
        except Exception as exc:
            error = str(exc)
        if args.check_engine_report:
            try:
                args.check_engine_report.write_text(
                    json.dumps({"ok": not bool(error), "error": error}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                error = f"無法寫入檢查報告：{exc}"
        output = sys.stderr if error else sys.stdout
        if output is not None:
            print(f"推論模組檢查失敗：{error}" if error else "推論模組檢查通過", file=output)
        return 1 if error else 0
    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        window = VoxBridgeWindow(smoke_test=args.smoke_test)
    except Exception as exc:
        if not args.smoke_test:
            QMessageBox.critical(None, "VoxBridge 無法啟動", str(exc))
        elif sys.stderr is not None:
            print(f"介面檢查失敗：{exc}", file=sys.stderr)
        return 1
    if args.smoke_test:
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
