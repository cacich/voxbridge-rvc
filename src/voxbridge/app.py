"""Traditional Chinese Windows desktop interface for VoxBridge."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont, QIcon
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
    QTabWidget,
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
        icon_path = Path(__file__).resolve().parent / "resources" / "voxbridge.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1120, 860)
        self.setMinimumSize(900, 650)
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        self._smoke_test = smoke_test
        self._closing = False
        self._download_thread: QThread | None = None
        self._download_worker: DownloadWorker | None = None
        self._state = "stopped"
        self._last_monitor_error = ""
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

                self._devices = list_devices(show_advanced=False)
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
            QWidget { font-family: "Microsoft JhengHei UI", "Segoe UI"; font-size: 13px; color: #263747; }
            QMainWindow, QScrollArea, QWidget#page, QWidget#root { background: #f3f6f8; }
            QScrollArea { border: none; }
            QFrame#footer { background: #ffffff; border-top: 1px solid #dde5eb; }
            QGroupBox { background: #ffffff; border: 1px solid #e0e7ec; border-radius: 14px;
                margin-top: 0; padding: 46px 18px 18px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 24px; top: 18px; padding: 0; color: #33495b; font-size: 14px; }
            QLabel { background: transparent; }
            QLabel#muted { color: #687d8d; font-size: 12px; }
            QLabel#eyebrow { color: #65808c; font-size: 11px; font-weight: 600; }
            QLabel#badge { background: #e5f3ef; color: #217466; border-radius: 10px; padding: 5px 12px; }
            QLabel#state { background: #eef2f5; color: #586d7d; border-radius: 12px; padding: 8px 16px; }
            QLabel#state[status="running"] { background: #def4eb; color: #126b51; }
            QLabel#state[status="error"] { background: #fff0ed; color: #ac453b; }
            QLabel#state[status="loading"], QLabel#state[status="stopping"] { background: #fff5de; color: #896018; }
            QLineEdit, QComboBox, QSpinBox { background: #f8fafb; color: #263747;
                border: 1px solid #dce5eb; border-radius: 8px; padding: 7px 10px; min-height: 24px;
                selection-background-color: #d3eee6; selection-color: #175c50; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #36a28b; background: #ffffff; }
            QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #8796a1; background: #f1f4f6; }
            QComboBox::drop-down { border: none; width: 28px; }
            QComboBox::down-arrow { image: url("DOWN_ICON"); width: 14px; height: 14px; }
            QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
                width: 28px; border: none; background: transparent; }
            QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right;
                width: 28px; border: none; background: transparent; }
            QSpinBox::up-arrow { image: url("UP_ICON"); width: 12px; height: 12px; }
            QSpinBox::down-arrow { image: url("DOWN_ICON"); width: 12px; height: 12px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #263747; padding: 6px;
                border: 1px solid #dce5eb; selection-background-color: #e5f3ef; selection-color: #175c50; }
            QPushButton { background: #ffffff; color: #3a5364; border: 1px solid #dce5eb;
                border-radius: 8px; padding: 8px 14px; font-weight: 600; min-height: 22px; }
            QPushButton:hover { background: #edf7f3; border-color: #93caba; color: #176e5a; }
            QPushButton:pressed { background: #dcefe8; }
            QPushButton:focus { border-color: #168165; }
            QPushButton:disabled { background: #f3f5f7; color: #a0aeb8; border-color: #e5ebef; }
            QPushButton#start { background: #167a63; color: #ffffff; border: 1px solid #167a63;
                border-radius: 10px; padding: 10px 28px; font-size: 14px; }
            QPushButton#start:hover { background: #116550; }
            QPushButton#start:disabled { background: #e0e9e5; border-color: #e0e9e5; color: #90a59c; }
            QPushButton#stop { color: #9c5750; border-color: #ecdeda; padding: 10px 20px; }
            QPushButton#stop:disabled { color: #b6aaa6; border-color: #eee8e5; }
            QPushButton#voice { background: #ffffff; text-align: left; padding: 9px 15px; }
            QProgressBar { background: #edf2f5; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
            QProgressBar::chunk { background: #38ae8d; border-radius: 4px; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid #b7c8d1; background: #ffffff; }
            QCheckBox::indicator:checked { background: #167a63; border: 1px solid #167a63; image: url("CHECK_ICON"); }
            QCheckBox::indicator:disabled { background: #e6ecef; border-color: #d4dde2; }
            QSlider::groove:horizontal { height: 5px; background: #e4ecf0; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #55aa94; border-radius: 2px; }
            QSlider::handle:horizontal { width: 18px; height: 18px; margin: -7px 0; border: none;
                image: url("KNOB_ICON"); background: transparent; }
            QSlider::handle:horizontal:disabled { border-color: #b8c9c2; }
            QTabWidget::pane { border: none; background: #f3f6f8; top: 10px; }
            QTabBar::tab { background: transparent; color: #738593; padding: 12px 22px;
                border-bottom: 2px solid transparent; font-size: 14px; }
            QTabBar::tab:selected { color: #126c57; border-bottom: 2px solid #168165; font-weight: 600; }
            QTabBar::tab:hover { color: #126c57; background: #eaf3ef; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
            QScrollBar::handle:vertical { background: #cbd6dd; min-height: 35px; border-radius: 4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QToolTip { background: #263747; color: #ffffff; border: none; padding: 6px; }
            """.replace("CHECK_ICON", (Path(__file__).parent / "resources" / "check.svg").as_posix())
            .replace("DOWN_ICON", (Path(__file__).parent / "resources" / "chevron-down.svg").as_posix())
            .replace("UP_ICON", (Path(__file__).parent / "resources" / "chevron-up.svg").as_posix())
            .replace("KNOB_ICON", (Path(__file__).parent / "resources" / "slider-knob.svg").as_posix())
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        brand_icon = QLabel()
        brand_icon.setPixmap(self.windowIcon().pixmap(44, 44))
        header.addWidget(brand_icon)
        brand = QVBoxLayout()
        title = QLabel("VoxBridge")
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #223747;")
        subtitle = QLabel("你的聲音工作室  /  即時變聲")
        subtitle.setObjectName("muted")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch(1)
        self.voice_button = QPushButton("選擇你的聲線  →")
        self.voice_button.setObjectName("voice")
        self.voice_button.setMinimumWidth(220)
        header.addWidget(self.voice_button)
        badge = QLabel(f"{__version__}  BETA")
        badge.setObjectName("badge")
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        self.notice_label = QLabel("")
        self.notice_label.setObjectName("notice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        layout.addWidget(self.notice_label)

        model = QGroupBox("聲線資料庫")
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

        audio = QGroupBox("聲音連線")
        af = QFormLayout(audio)
        af.setVerticalSpacing(14)
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem("Discord 虛擬麥克風", "discord")
        self.output_mode_combo.addItem("只在耳機測試變聲", "monitor_only")
        af.addRow("輸出模式", self.output_mode_combo)
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self.monitor_combo = QComboBox()
        self._rebuild_device_combos(("", "", ""))
        af.addRow("麥克風", self.input_combo)
        af.addRow("變聲輸出", self.output_combo)
        af.addRow("監聽裝置", self.monitor_combo)
        self.advanced_devices_checkbox = QCheckBox("顯示其他音訊介面（進階）")
        self.advanced_devices_checkbox.setToolTip("進階清單可能包含未連接的舊裝置或同一裝置的其他介面。")
        self.refresh_devices_button = QPushButton("重新整理裝置")
        device_actions = QHBoxLayout()
        device_actions.addWidget(self.refresh_devices_button)
        device_actions.addWidget(self.advanced_devices_checkbox)
        device_actions.addStretch(1)
        af.addRow("", self._wrap(device_actions))
        self.device_help = QLabel("")
        self.device_help.setObjectName("muted")
        self.device_help.setWordWrap(True)
        af.addRow("", self.device_help)
        self.monitor_checkbox = QCheckBox("聽到自己的變聲")
        self.monitor_checkbox.setToolTip("只控制你聽到的聲音；Discord 的變聲輸出會繼續。建議使用耳機避免回授。")
        af.addRow("本機監聽", self.monitor_checkbox)
        self.monitor_slider, self.monitor_value = self._gain_controls()
        af.addRow("監聽音量", self._slider_row(self.monitor_slider, self.monitor_value))
        self.output_slider, self.output_value = self._gain_controls()
        af.addRow("變聲輸出音量", self._slider_row(self.output_slider, self.output_value))
        self.route_help = QLabel("")
        self.route_help.setObjectName("muted")
        self.route_help.setWordWrap(True)
        af.addRow("", self.route_help)
        layout.addWidget(audio)

        tuning = QGroupBox("聲音調整")
        tf = QFormLayout(tuning)
        tf.setVerticalSpacing(12)
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
        hint.setWordWrap(True)
        tf.addRow("", hint)
        layout.addWidget(tuning)

        run = QGroupBox("即時訊號")
        rv = QVBoxLayout(run)
        rv.setSpacing(14)
        process_row = QHBoxLayout()
        process_caption = QLabel("單次處理")
        process_caption.setObjectName("muted")
        process_row.addWidget(process_caption)
        process_row.addStretch(1)
        self.process_value = QLabel("— ms")
        self.process_value.setStyleSheet("font-size: 26px; font-weight: 600; color: #176f59;")
        process_row.addWidget(self.process_value)
        rv.addLayout(process_row)
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
        self.metrics_label = QLabel("引擎處理時間，非端到端延遲\n開始變聲後，這裡會顯示訊號狀態。")
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
        # Keep daily controls on one page, with setup and guidance one tab away.
        for card in (model, audio, tuning, run, help_box):
            layout.removeWidget(card)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        dashboard = QWidget()
        dashboard_layout = QHBoxLayout(dashboard)
        dashboard_layout.setContentsMargins(0, 10, 0, 14)
        dashboard_layout.setSpacing(18)
        dashboard_layout.addWidget(audio, 3)
        right_column = QVBoxLayout()
        right_column.setSpacing(14)
        right_column.addWidget(tuning)
        right_column.addWidget(run)
        right_column.addStretch(1)
        dashboard_layout.addLayout(right_column, 2)
        dashboard_layout.setAlignment(audio, Qt.AlignmentFlag.AlignTop)
        self.tabs.addTab(dashboard, "控制台")
        for label, card, description in (
            ("模型與引擎", model, "選擇你的 RVC 聲線。首次使用時，下載引擎檔案即可開始。"),
            ("使用指南", help_box, "先在耳機確認變聲效果，再將聲音送進 Discord。"),
        ):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 18, 0, 14)
            intro = QLabel(description)
            intro.setObjectName("muted")
            intro.setWordWrap(True)
            tab_layout.addWidget(intro)
            tab_layout.addWidget(card)
            tab_layout.addStretch(1)
            self.tabs.addTab(tab, label)
        layout.addWidget(self.tabs)
        layout.addStretch(1)
        self.voice_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.model_edit.textChanged.connect(self._update_voice_summary)
        for form in (mf, af, tf):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
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
        self.state_label.setObjectName("state")
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
        self.output_mode_combo.currentIndexChanged.connect(self._update_route_help)
        self.output_combo.currentIndexChanged.connect(self._update_route_help)
        self.monitor_combo.currentIndexChanged.connect(self._update_route_help)
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        self.advanced_devices_checkbox.toggled.connect(self._refresh_devices)

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
        combo.clear()
        combo.addItem(placeholder, "")
        for device in self._devices:
            if getattr(device, capability, 0) > 0:
                combo.addItem(device.label, device.key)

    @staticmethod
    def _select_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if value and index < 0:
            combo.setItemText(0, "裝置目前不可用，請重新選擇")
            combo.setItemData(0, "")
            combo.setToolTip(f"先前選擇的裝置目前不可用：{value}")
        else:
            combo.setToolTip("")
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _rebuild_device_combos(self, keys: tuple[str, str, str]) -> None:
        for combo, capability, placeholder, key in (
            (self.input_combo, "inputs", "選擇麥克風", keys[0]),
            (self.output_combo, "outputs", "選擇變聲輸出，例如 CABLE Input", keys[1]),
            (self.monitor_combo, "outputs", "選擇耳機或喇叭", keys[2]),
        ):
            combo.blockSignals(True)
            self._fill_devices(combo, capability, placeholder)
            self._select_data(combo, key)
            combo.blockSignals(False)

    def _refresh_devices(self, *_: Any) -> None:
        if self._state not in ("stopped", "error") or self._download_thread is not None:
            return
        if self._state == "error" and self._service is not None:
            wait = getattr(self._service, "wait", None)
            if callable(wait) and not wait(0):
                self._notice("音訊工作仍在結束，請稍後重新整理。", error=True)
                return
        keys = (
            self.input_combo.currentData() or "",
            self.output_combo.currentData() or "",
            self.monitor_combo.currentData() or "",
        )
        try:
            from voxbridge.audio import refresh_devices

            self._devices = refresh_devices(show_advanced=self.advanced_devices_checkbox.isChecked())
        except Exception as exc:
            self._notice(f"無法重新整理音訊裝置：{exc}", error=True)
            return
        self._rebuild_device_combos(keys)
        self._update_route_help()
        self._notice("音訊裝置清單已更新。" if self._devices else "目前找不到可用音訊裝置。請檢查 Windows 音效設定。")

    def _update_route_help(self, *_: Any) -> None:
        mode = self.output_mode_combo.currentData()
        output = self.output_combo.currentData() or ""
        monitor = self.monitor_combo.currentData() or ""
        playback_count = sum(getattr(device, "outputs", 0) > 0 for device in self._devices)
        if playback_count == 0:
            self.device_help.setText(
                "目前沒有可用的播放裝置。請連接耳機或喇叭，在 Windows 音效設定確認裝置已啟用，然後按「重新整理裝置」。"
            )
        elif self.advanced_devices_checkbox.isChecked():
            self.device_help.setText("進階清單可能包含未連接的舊裝置與同一裝置的其他音訊介面；建議優先選擇 Windows WASAPI。")
        else:
            self.device_help.setText("預設僅顯示目前可用的 Windows WASAPI 裝置。若找不到裝置，檢查連線後重新整理。")
        if mode == "monitor_only":
            self.route_help.setText("耳機測試模式不需要 CABLE。請選擇耳機或喇叭並開啟本機監聽；聲音不會送到 Discord。")
        elif output and output == monitor:
            self.route_help.setText("變聲輸出與監聽選了同一裝置。聲音只會播放一次；此時使用「變聲輸出音量」調整，關閉監聽不會靜音主要輸出。")
        else:
            self.route_help.setText("Discord 模式需要虛擬音訊線：此處選 CABLE Input，Discord 輸入選 CABLE Output。")
        self._refresh_controls()

    def _populate_settings(self) -> None:
        s = self.settings
        self._applied_monitor_enabled = s.monitor_enabled
        self.model_edit.setText(s.model_path)
        self.index_edit.setText(s.index_path)
        self.assets_edit.setText(s.assets_dir)
        self._select_data(self.output_mode_combo, s.output_mode)
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
        self._update_route_help()
        self._update_voice_summary()

    def _update_voice_summary(self, *_: Any) -> None:
        name = Path(self.model_edit.text().strip()).stem
        visible = name if len(name) <= 24 else name[:23] + "…"
        self.voice_button.setText(f"聲線  /  {visible}  →" if name else "選擇你的聲線  →")
        self.voice_button.setToolTip(self.model_edit.text() or "前往模型與引擎，選擇 RVC 模型")

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
            output_device=(self.output_combo.currentData() or "") if self.output_mode_combo.currentData() == "discord" else "",
            output_mode=self.output_mode_combo.currentData(),
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
            "background: #fff0ed; color: #a24035; border: 1px solid #f2d3cd; border-radius: 8px; padding: 11px;"
            if error else "background: #e8f4ef; color: #256854; border: 1px solid #cce7dc; border-radius: 8px; padding: 11px;"
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
        if not settings.input_device:
            self._notice("請先選擇可用的麥克風；若清單為空，請檢查 Windows 音效設定後重新整理。", error=True)
            return
        if settings.output_mode == "discord" and not settings.output_device:
            self._notice("Discord 模式需選擇變聲輸出，例如 CABLE Input；也可以改用「只在耳機測試變聲」。", error=True)
            return
        if settings.output_mode == "monitor_only" and not settings.monitor_enabled:
            self._notice("耳機測試模式需開啟本機監聽。", error=True)
            return
        if (settings.monitor_enabled or settings.output_mode == "monitor_only") and not settings.monitor_device:
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
            self._notice("請先選擇監聽裝置，再開啟監聽。執行中需先停止才能更換裝置。", error=True)
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
                if self.output_mode_combo.currentData() == "monitor_only":
                    self._notice("耳機測試正在播放變聲；此模式不會送到 Discord。")
                else:
                    self._notice("變聲正在輸出。請在 Discord 選擇 CABLE Output 作為輸入。")
            self._refresh_controls()
        monitor_error = snapshot.get("monitor_error") or ""
        if monitor_error and monitor_error != self._last_monitor_error:
            self._notice(f"監聽裝置發生錯誤，主要輸出仍繼續：{monitor_error}", error=True)
        self._last_monitor_error = monitor_error
        if monitor_error and snapshot.get("monitor_enabled") is False and self.monitor_checkbox.isChecked():
            self.monitor_checkbox.blockSignals(True)
            self.monitor_checkbox.setChecked(False)
            self.monitor_checkbox.blockSignals(False)
            self._applied_monitor_enabled = False
        self.input_meter.setValue(self._meter_value(snapshot.get("input_rms", 0)))
        self.output_meter.setValue(self._meter_value(snapshot.get("output_rms", 0)))
        process = snapshot.get("process_ms", 0)
        try:
            process_text = f"{float(process):.0f}"
        except (TypeError, ValueError):
            process_text = "—"
        self.process_value.setText(f"{process_text} ms" if state == "running" else "— ms")
        self.metrics_label.setText(
            "引擎處理時間，非端到端延遲\n"
            f"輸入丟失  {snapshot.get('input_drops', 0)}    輸出欠載  {snapshot.get('output_underruns', 0)}\n"
            f"監聽欠載  {snapshot.get('monitor_underruns', 0)}    處理超時  {snapshot.get('overruns', 0)}"
        )
        self._maybe_finish_close()

    @staticmethod
    def _meter_value(rms: Any) -> int:
        try:
            return max(0, min(100, round(float(rms) * 300)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _refresh_controls(self) -> None:
        labels = {"stopped": "●  已停止", "loading": "●  載入中", "running": "●  變聲中", "stopping": "●  停止中", "error": "●  發生錯誤"}
        self.state_label.setText(labels.get(self._state, self._state))
        if self.state_label.property("status") != self._state:
            self.state_label.setProperty("status", self._state)
            self.state_label.style().unpolish(self.state_label)
            self.state_label.style().polish(self.state_label)
        locked = self._state in ("loading", "running", "stopping") or self._download_thread is not None
        for widget in (
            self.model_edit, self.model_browse, self.index_edit, self.index_browse,
            self.assets_edit, self.assets_browse, self.input_combo, self.output_mode_combo,
            self.monitor_combo, self.compute_combo, self.pitch_spin, self.index_slider,
            self.block_combo,
        ):
            widget.setEnabled(not locked)
        mode = self.output_mode_combo.currentData()
        shared_output = mode == "discord" and bool(self.output_combo.currentData()) and self.output_combo.currentData() == self.monitor_combo.currentData()
        self.output_combo.setEnabled(not locked and mode == "discord")
        self.refresh_devices_button.setEnabled(not locked and self._state in ("stopped", "error"))
        self.advanced_devices_checkbox.setEnabled(not locked and self._state in ("stopped", "error"))
        self.download_button.setEnabled(not locked)
        self.start_button.setEnabled(not locked and self._service is not None)
        self.stop_button.setEnabled(self._state in ("loading", "running"))
        self.monitor_checkbox.setEnabled(self._state != "stopping")
        self.monitor_slider.setEnabled(self._state != "stopping" and not shared_output)
        self.output_slider.setEnabled(self._state != "stopping" and mode == "discord")

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
            for name in ("torch", "faiss", "librosa", "scipy", "soxr", "voxbridge.vendor.module.models", "voxbridge.vendor.rmvpe"):
                importlib.import_module(name)
            from transformers import HubertModel  # noqa: F401
            import numpy as np
            import soxr

            resampler = soxr.ResampleStream(44100, 48000, 1, dtype="float32")
            converted = resampler.resample_chunk(np.zeros(4096, dtype=np.float32), last=True)
            if converted.size == 0 or not np.all(np.isfinite(converted)):
                raise RuntimeError("soxr 取樣率轉換自檢沒有產生有效音訊")
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
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("cacich.VoxBridge")
        except (OSError, AttributeError):
            pass
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
