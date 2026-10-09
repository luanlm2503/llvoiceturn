"""Màn hình chính: nhập TXT/SRT vào bảng, tạo giọng hàng loạt bằng VoxCPM2 local."""

import re
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llvoice.batch_audio import extract_batch_mp3
from llvoice.i18n import tr
from llvoice.mp3_join import available_target, durations_ms, join_mp3
from llvoice.srt import (
    SrtCue,
    SrtError,
    format_srt,
    format_timing,
    parse_srt,
    parse_srt_bytes,
    parse_timing,
    validate_cues,
)
from llvoice.text_segments import (
    DEFAULT_DELIMITERS,
    SPLIT_LINES,
    SPLIT_NONE,
    SPLIT_PARAGRAPHS,
    SPLIT_PUNCTUATION,
    SplitOptions,
    split_text_segments,
)
from llvoice.voxcpm_api import DEFAULT_VOICE, MAX_PROMPT_TEXT, MAX_SPEED, MIN_SPEED, VoiceSettings, VoxCPMClient

MODE_TEXT = "text"
MODE_SRT = "srt"
MAX_ROWS = 100
COL_ID, COL_OUTPUT, COL_TIMING, COL_CONTENT, COL_STATUS = range(5)
_SRT_START = re.compile(r"\s*\d+\s*\n\s*\d{2,}:\d{2}:\d{2},\d{3}\s*-->")


def looks_like_srt(text: str) -> bool:
    return bool(_SRT_START.match(text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")))


class _TaskSignals(QObject):
    done = Signal(object)


class _ClientTask(QRunnable):
    def __init__(self, operation, callback):
        super().__init__()
        self.operation = operation
        self.signals = _TaskSignals()
        self.signals.done.connect(callback)

    def run(self):
        try:
            self.signals.done.emit(self.operation())
        except Exception as exc:
            self.signals.done.emit(exc)


class PasteDialog(QDialog):
    def __init__(self, split_options: SplitOptions, parent=None):
        super().__init__(parent)
        self._split_options = split_options
        self.setWindowTitle(tr("tts.paste_title"))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(tr("tts.paste_placeholder"))
        layout.addWidget(self.text_edit)
        self.count_label = QLabel(tr("tts.paste_count_empty"))
        self.count_label.setStyleSheet("color: gray;")
        self.text_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.count_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_count(self) -> None:
        text = self.text_edit.toPlainText()
        if not text.strip():
            self.count_label.setText(tr("tts.paste_count_empty"))
        elif looks_like_srt(text):
            self.count_label.setText(tr("tts.paste_count_srt"))
        else:
            count = len(split_text_segments(text, self._split_options))
            self.count_label.setText(tr("tts.paste_count", count=count))


class TtsPage(QWidget):
    def __init__(self, client: VoxCPMClient | None = None, *, thread_pool: QThreadPool | None = None):
        super().__init__()
        self.client = client or VoxCPMClient()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._tasks: set[_ClientTask] = set()
        self._generating = False
        self._stopping = False
        self._active_job_id: str | None = None
        self._job_mode: str | None = None
        self._job_format = "wav"
        self._completed = 0
        self._elapsed_ms = 0
        self._stopwatch = QElapsedTimer()
        self.mode: str | None = None
        self.source_path: Path | None = None
        self._raw_text: str | None = None
        self.source_label_text = tr("tts.source_none")
        self.output_directory: Path | None = None
        self.reference_path: Path | None = None
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._poll_status)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._update_summary)
        self._health_timer = QTimer(self)
        self._health_timer.setSingleShot(True)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._check_health)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self._build_voice_group(), 3)
        top.addWidget(self._build_voice_settings_group(), 2)
        top.addWidget(self._build_split_group(), 2)
        top.addWidget(self._build_output_group(), 2)
        layout.addLayout(top)

        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        toolbar = QHBoxLayout()
        self.start_button = QPushButton(tr("tts.start"))
        self.start_button.setStyleSheet("QPushButton { color: #1b7d2c; font-weight: bold; padding: 4px 18px; }")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton(tr("tts.stop"))
        self.stop_button.setToolTip(tr("tts.stop_hint"))
        self.stop_button.setStyleSheet("QPushButton:enabled { color: #c62828; font-weight: bold; padding: 4px 18px; }")
        self.stop_button.clicked.connect(self._stop)
        self.import_button = QPushButton(tr("tts.import_file"))
        self.import_button.clicked.connect(self._import_file)
        self.paste_button = QPushButton(tr("tts.paste"))
        self.paste_button.clicked.connect(self._paste)
        self.open_output_button = QPushButton(tr("tts.open_output"))
        self.open_output_button.clicked.connect(self._open_output)
        self.join_button = QPushButton(tr("tts.join_mp3"))
        self.join_button.setToolTip(tr("tts.join_hint"))
        self.join_button.clicked.connect(self._join_mp3)
        self.export_srt_button = QPushButton(tr("tts.export_srt"))
        self.export_srt_button.setToolTip(tr("tts.export_srt_hint"))
        self.export_srt_button.clicked.connect(lambda: self._export_srt())
        self._joining = False
        for button in (
            self.start_button,
            self.stop_button,
            self.import_button,
            self.paste_button,
            self.open_output_button,
            self.join_button,
            self.export_srt_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("tts.col_id"), tr("tts.col_output"), tr("tts.col_timing"), tr("tts.col_content"), tr("tts.col_status")]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setToolTip(tr("tts.table_hint"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_OUTPUT, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_TIMING, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_CONTENT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_OUTPUT, 110)
        self.table.setColumnWidth(COL_TIMING, 200)
        self.table.setColumnWidth(COL_STATUS, 130)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._play_row)
        layout.addWidget(self.table, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c62828;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        footer = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.health_label = QLabel(tr("tts.checking"))
        self.health_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.health_label.setStyleSheet("color: #7b3fa0; font-weight: bold;")
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.health_label)
        layout.addLayout(footer)

        self._refresh()
        self._check_health()

    def _build_voice_group(self) -> QGroupBox:
        group = QGroupBox(tr("tts.voice_group"))
        grid = QGridLayout(group)
        self.reference_edit = QLineEdit()
        self.reference_edit.setReadOnly(True)
        self.reference_edit.setPlaceholderText(tr("tts.reference_none"))
        self.choose_reference_button = QPushButton(tr("tts.reference_choose"))
        self.choose_reference_button.clicked.connect(self._choose_reference)
        self.clear_reference_button = QPushButton(tr("tts.reference_clear"))
        self.clear_reference_button.setEnabled(False)
        self.clear_reference_button.clicked.connect(self._clear_reference)
        grid.addWidget(QLabel(tr("tts.reference")), 0, 0)
        grid.addWidget(self.reference_edit, 0, 1)
        grid.addWidget(self.choose_reference_button, 0, 2)
        grid.addWidget(self.clear_reference_button, 0, 3)
        self.prompt_text_edit = QLineEdit()
        self.prompt_text_edit.setMaxLength(MAX_PROMPT_TEXT)
        self.prompt_text_edit.setPlaceholderText(tr("tts.prompt_text_placeholder"))
        self.prompt_text_edit.setToolTip(tr("tts.prompt_text_hint"))
        self.prompt_text_edit.setEnabled(False)
        self.prompt_text_edit.textChanged.connect(lambda _text: self._update_style_state())
        prompt_label = QLabel(tr("tts.prompt_text"))
        prompt_label.setToolTip(tr("tts.prompt_text_hint"))
        grid.addWidget(prompt_label, 1, 0)
        grid.addWidget(self.prompt_text_edit, 1, 1, 1, 3)
        self.style_edit = QLineEdit()
        self.style_edit.setPlaceholderText(tr("tts.style_placeholder"))
        grid.addWidget(QLabel(tr("tts.style")), 2, 0)
        grid.addWidget(self.style_edit, 2, 1, 1, 3)
        help_label = QLabel(tr("tts.reference_help"))
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray;")
        grid.addWidget(help_label, 3, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        return group

    def _build_voice_settings_group(self) -> QGroupBox:
        self.voice_settings_group = QGroupBox(tr("tts.voice_settings"))
        self.voice_settings_group.setCheckable(True)
        self.voice_settings_group.setChecked(True)
        self.voice_settings_group.setToolTip(tr("tts.voice_settings_hint"))
        grid = QGridLayout(self.voice_settings_group)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(MIN_SPEED, MAX_SPEED)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setToolTip(tr("tts.speed_hint"))

        def percent(tooltip_key: str) -> QSpinBox:
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setSingleStep(5)
            spin.setSuffix(" %")
            spin.setToolTip(tr(tooltip_key))
            return spin

        self.stability_spin = percent("tts.stability_hint")
        self.similarity_spin = percent("tts.similarity_hint")
        self.style_spin = percent("tts.style_exaggeration_hint")
        self.speaker_boost_check = QCheckBox(tr("tts.speaker_boost"))
        self.speaker_boost_check.setToolTip(tr("tts.speaker_boost_hint"))
        rows = (
            ("tts.speed", self.speed_spin),
            ("tts.stability", self.stability_spin),
            ("tts.similarity", self.similarity_spin),
            ("tts.style_exaggeration", self.style_spin),
        )
        for row, (label_key, spin) in enumerate(rows):
            label = QLabel(tr(label_key))
            label.setToolTip(spin.toolTip())
            grid.addWidget(label, row, 0)
            grid.addWidget(spin, row, 1)
        grid.addWidget(self.speaker_boost_check, len(rows), 0, 1, 2)
        self.speed_reset_button = QPushButton(tr("tts.speed_reset"))
        self.speed_reset_button.setToolTip(tr("tts.voice_reset_hint"))
        self.speed_reset_button.clicked.connect(self.reset_voice_settings)
        grid.addWidget(self.speed_reset_button, len(rows), 2)
        grid.setColumnStretch(1, 1)
        self.reset_voice_settings()
        return self.voice_settings_group

    def reset_voice_settings(self) -> None:
        self.speed_spin.setValue(1.0)
        self.stability_spin.setValue(DEFAULT_VOICE.stability)
        self.similarity_spin.setValue(DEFAULT_VOICE.similarity)
        self.style_spin.setValue(DEFAULT_VOICE.style_exaggeration)
        self.speaker_boost_check.setChecked(True)

    def voice_settings(self) -> tuple[float, VoiceSettings]:
        """Speed and voice settings to send; the unchecked group means VoxCPM2 defaults."""
        if not self.voice_settings_group.isChecked():
            return 1.0, DEFAULT_VOICE
        return round(self.speed_spin.value(), 2), VoiceSettings(
            stability=self.stability_spin.value(),
            similarity=self.similarity_spin.value(),
            style_exaggeration=self.style_spin.value(),
            speaker_boost=self.speaker_boost_check.isChecked(),
        )

    def _build_split_group(self) -> QGroupBox:
        group = QGroupBox(tr("tts.split_group"))
        grid = QGridLayout(group)
        self.split_mode_combo = QComboBox()
        for mode, key in (
            (SPLIT_PUNCTUATION, "tts.split_punctuation"),
            (SPLIT_LINES, "tts.split_lines"),
            (SPLIT_PARAGRAPHS, "tts.split_paragraphs"),
            (SPLIT_NONE, "tts.split_none"),
        ):
            self.split_mode_combo.addItem(tr(key), mode)
        self.delimiters_edit = QLineEdit(DEFAULT_DELIMITERS)
        self.delimiters_edit.setToolTip(tr("tts.split_delimiters_hint"))
        self.merge_spin = QSpinBox()
        self.merge_spin.setRange(0, 4000)
        self.merge_spin.setSingleStep(50)
        self.merge_spin.setSuffix(tr("tts.split_merge_suffix"))
        self.merge_spin.setSpecialValueText(tr("tts.split_merge_off"))
        self.merge_spin.setToolTip(tr("tts.split_merge_hint"))
        grid.addWidget(QLabel(tr("tts.split_mode")), 0, 0)
        grid.addWidget(self.split_mode_combo, 0, 1)
        grid.addWidget(QLabel(tr("tts.split_delimiters")), 1, 0)
        grid.addWidget(self.delimiters_edit, 1, 1)
        grid.addWidget(QLabel(tr("tts.split_merge")), 2, 0)
        grid.addWidget(self.merge_spin, 2, 1)
        grid.setColumnStretch(1, 1)
        self.split_mode_combo.currentIndexChanged.connect(self._on_split_options_changed)
        self.delimiters_edit.textChanged.connect(self._on_split_options_changed)
        self.merge_spin.valueChanged.connect(self._on_split_options_changed)
        return group

    def split_options(self) -> SplitOptions:
        return SplitOptions(
            mode=self.split_mode_combo.currentData(),
            delimiters=self.delimiters_edit.text(),
            merge_limit=self.merge_spin.value(),
        )

    def _on_split_options_changed(self, *_args) -> None:
        self.delimiters_edit.setEnabled(
            not self._generating and self.split_mode_combo.currentData() == SPLIT_PUNCTUATION
        )
        # Tách lại văn bản gốc đang mở; sửa tay trong bảng sẽ bị thay bằng kết quả mới.
        if self.mode == MODE_TEXT and self._raw_text is not None and not self._generating:
            self.load_text(self._raw_text, self.source_path)

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox(tr("tts.output_group"))
        grid = QGridLayout(group)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText(tr("tts.output_none"))
        self.choose_output_button = QPushButton("…")
        self.choose_output_button.setFixedWidth(32)
        self.choose_output_button.clicked.connect(self._choose_output_directory)
        grid.addWidget(QLabel(tr("tts.output_dir")), 0, 0)
        grid.addWidget(self.output_edit, 0, 1)
        grid.addWidget(self.choose_output_button, 0, 2)
        self.format_combo = QComboBox()
        self.format_combo.addItem("WAV", "wav")
        self.format_combo.addItem("MP3", "mp3")
        grid.addWidget(QLabel(tr("tts.srt_format")), 1, 0)
        grid.addWidget(self.format_combo, 1, 1, 1, 2)
        help_label = QLabel(tr("tts.output_help"))
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray;")
        grid.addWidget(help_label, 2, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        return group

    # ---- nạp dữ liệu vào bảng ----

    def load_text(self, text: str, source: Path | None = None) -> None:
        segments = split_text_segments(text, self.split_options())
        self._load_rows(MODE_TEXT, [(str(i), "", s) for i, s in enumerate(segments, 1)], source)
        self._raw_text = text

    def load_srt(self, cues: list[SrtCue], source: Path | None = None) -> None:
        self._raw_text = None
        self._load_rows(MODE_SRT, [(str(c.index), format_timing(c.start_ms, c.end_ms), c.text) for c in cues], source)

    def _load_rows(self, mode: str, rows: list[tuple[str, str, str]], source: Path | None) -> None:
        self.mode = mode if rows else None
        self.source_path = source
        self.source_label_text = source.name if source else (tr("tts.source_pasted") if rows else tr("tts.source_none"))
        self._completed = 0
        self._elapsed_ms = 0
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for row, (identifier, timing, content) in enumerate(rows):
            self.table.setItem(row, COL_ID, self._fixed_item(identifier))
            self.table.setItem(row, COL_OUTPUT, self._fixed_item(""))
            timing_item = QTableWidgetItem(timing) if mode == MODE_SRT else self._fixed_item("")
            self.table.setItem(row, COL_TIMING, timing_item)
            self.table.setItem(row, COL_CONTENT, QTableWidgetItem(content))
            self.table.setItem(row, COL_STATUS, self._fixed_item(""))
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        self.status_label.clear()
        self._refresh()

    @staticmethod
    def _fixed_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _import_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, tr("tts.import_dialog"), "", "Văn bản / phụ đề (*.srt *.txt)")
        if not filename:
            return
        path = Path(filename)
        try:
            data = path.read_bytes()
            if path.suffix.lower() == ".srt":
                self.load_srt(parse_srt_bytes(data), path)
            else:
                self.load_text(data.decode("utf-8-sig"), path)
        except (OSError, UnicodeDecodeError, SrtError) as exc:
            self._show_error(tr("tts.import_error", detail=str(exc)))

    def _paste(self) -> None:
        dialog = PasteDialog(self.split_options(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.load_pasted(dialog.text_edit.toPlainText())

    def load_pasted(self, text: str) -> None:
        if looks_like_srt(text):
            try:
                self.load_srt(parse_srt(text))
            except SrtError as exc:
                self._show_error(tr("tts.import_error", detail=str(exc)))
        else:
            self.load_text(text)

    # ---- kiểm tra bảng ----

    def rows_payload(self) -> list[str] | list[SrtCue]:
        """Đọc bảng thành đúng dữ liệu gửi sidecar; lỗi thì ném ValueError/SrtError."""
        count = self.table.rowCount()
        if count > MAX_ROWS:
            raise ValueError(tr("tts.too_many", count=count))
        contents = []
        for row in range(count):
            content = self.table.item(row, COL_CONTENT).text().strip()
            if not content:
                raise ValueError(tr("tts.empty_row", row=row + 1))
            contents.append(content)
        if self.mode != MODE_SRT:
            return contents
        cues = []
        for row, content in enumerate(contents):
            start_ms, end_ms = parse_timing(self.table.item(row, COL_TIMING).text(), row + 1)
            cues.append(SrtCue(int(self.table.item(row, COL_ID).text()), start_ms, end_ms, content))
        return validate_cues(cues)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (COL_TIMING, COL_CONTENT):
            self._refresh()

    def _refresh(self) -> None:
        valid = False
        if self.mode is not None:
            try:
                self.rows_payload()
            except (ValueError, SrtError) as exc:
                self._show_error(tr("tts.invalid", detail=str(exc)))
            else:
                self._show_error("")
                valid = True
        else:
            self._show_error("")
        # Start luôn bấm được (trừ khi đang chạy); thiếu gì sẽ báo khi bấm.
        self.start_button.setEnabled(not self._generating)
        self.stop_button.setEnabled(self._generating and not self._stopping)
        self._ready_to_start = valid and self.output_directory is not None
        self.format_combo.setEnabled(not self._generating and self.mode == MODE_SRT)
        self.open_output_button.setEnabled(self.output_directory is not None)
        self.join_button.setEnabled(not self._generating and not self._joining and len(self.joinable_files()) >= 2)
        self.export_srt_button.setEnabled(
            not self._generating and self.output_directory is not None and self._has_timing()
        )
        self._update_summary()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _update_summary(self) -> None:
        total = self.table.rowCount()
        processing = 1 if self._generating and self._completed < total else 0
        elapsed = self._stopwatch.elapsed() if self._generating else self._elapsed_ms
        self.summary_label.setText(
            tr(
                "tts.summary",
                source=self.source_label_text,
                done=self._completed,
                processing=processing,
                total=total,
                elapsed=elapsed // 1000,
            )
        )

    # ---- giọng và đầu ra ----

    def _choose_reference(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, tr("tts.reference_dialog"), "", "Audio (*.wav *.mp3)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() not in {".wav", ".mp3"}:
            self.status_label.setText(tr("tts.reference_invalid"))
            return
        if path != self.reference_path:
            self.prompt_text_edit.clear()  # lời thoại cũ không khớp audio mới
        self.reference_path = path
        self.reference_edit.setText(str(path))
        self.clear_reference_button.setEnabled(True)
        self._update_style_state()

    def _clear_reference(self) -> None:
        self.reference_path = None
        self.reference_edit.clear()
        self.prompt_text_edit.clear()
        self.clear_reference_button.setEnabled(False)
        self._update_style_state()

    def prompt_text(self) -> str | None:
        """Lời thoại của audio mẫu; chỉ dùng khi đang có audio mẫu."""
        if self.reference_path is None:
            return None
        return self.prompt_text_edit.text().strip() or None

    def _update_style_state(self) -> None:
        # Có lời thoại mẫu thì VoxCPM2 nói tiếp audio mẫu và bỏ qua mô tả giọng.
        idle = not self._generating
        self.prompt_text_edit.setEnabled(idle and self.reference_path is not None)
        uses_prompt = self.prompt_text() is not None
        self.style_edit.setEnabled(idle and not uses_prompt)
        self.style_edit.setToolTip(tr("tts.style_ignored") if uses_prompt else "")

    def _choose_output_directory(self) -> None:
        start = str(self.output_directory) if self.output_directory else ""
        selected = QFileDialog.getExistingDirectory(self, tr("tts.output_dialog"), start)
        if selected:
            self.set_output_directory(Path(selected))

    def set_output_directory(self, path: Path) -> None:
        self.output_directory = path.resolve()
        self.output_edit.setText(str(self.output_directory))
        self.output_edit.setToolTip(str(self.output_directory))
        self.output_edit.setCursorPosition(0)
        self._refresh()

    def _open_output(self) -> None:
        if not self.output_directory:
            return
        if not self.output_directory.is_dir():
            self.status_label.setText(tr("tts.output_missing", path=str(self.output_directory)))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_directory)))

    # ---- chạy job ----

    def _submit(self, operation, callback):
        task = _ClientTask(operation, callback)
        self._tasks.add(task)
        task.signals.done.connect(lambda _result, current=task: self._tasks.discard(current))
        self.thread_pool.start(task)

    def _check_health(self) -> None:
        self._submit(self.client.health, self._on_health)

    def _on_health(self, result: object) -> None:
        if isinstance(result, Exception):
            self.health_label.setText(tr("tts.offline", detail=str(result)))
        elif result.get("status") == "ready":
            self.health_label.setText(tr("tts.ready"))
            return
        elif result.get("status") == "loading":
            self.health_label.setText(tr("tts.loading"))
        else:
            self.health_label.setText(tr("tts.offline", detail=str(result.get("detail") or "")))
        self._health_timer.start()

    def missing_before_start(self) -> list[str]:
        """Những thứ còn thiếu để bắt đầu, theo thứ tự người dùng cần làm."""
        missing = []
        if self.mode is None or self.table.rowCount() == 0:
            missing.append(tr("tts.missing_rows"))
        if self.output_directory is None:
            missing.append(tr("tts.missing_output"))
        elif not self.output_directory.is_dir():
            missing.append(tr("tts.output_missing", path=str(self.output_directory)))
        if self.reference_path is not None and not self.reference_path.is_file():
            missing.append(tr("tts.missing_reference", path=str(self.reference_path)))
        return missing

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, tr("tts.missing_title"), message)

    def _start(self) -> None:
        if self._generating:
            return
        missing = self.missing_before_start()
        if missing:
            message = tr("tts.missing", items="\n".join(f"• {item}" for item in missing))
            self._show_error(message)
            self.status_label.setText(tr("tts.missing_short"))
            self._warn(message)
            return
        try:
            payload = self.rows_payload()
        except (ValueError, SrtError) as exc:
            message = tr("tts.invalid", detail=str(exc))
            self._show_error(message)
            self._warn(message)
            return
        if self.mode == MODE_TEXT:
            for index in range(1, len(payload) + 1):
                if (self.output_directory / f"{index:03d}.mp3").exists():
                    message = tr("tts.output_conflict", name=f"{index:03d}.mp3")
                    self._show_error(message)
                    self._warn(message)
                    return
        self._job_mode = self.mode
        self._job_format = self.format_combo.currentData()
        self._completed = 0
        self._generating = True
        self._stopping = False
        self._set_controls_enabled(False)
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, COL_OUTPUT).setText("")
            self.table.item(row, COL_OUTPUT).setData(Qt.ItemDataRole.UserRole, None)
            if self._job_mode == MODE_TEXT:
                self.table.item(row, COL_TIMING).setText("")
        self.table.blockSignals(False)
        self._set_row_states(0)
        self.status_label.setText(tr("tts.starting"))
        self._stopwatch.start()
        self._tick_timer.start()
        style = self.style_edit.text().strip() or None
        reference = self.reference_path
        prompt_text = self.prompt_text()
        speed, voice = self.voice_settings()
        if self._job_mode == MODE_SRT:
            output_format = self._job_format
            self._submit(
                lambda: self.client.start_srt(
                    payload, style, output_format, reference, speed=speed, voice=voice, prompt_text=prompt_text
                ),
                self._on_started,
            )
        else:
            self._submit(
                lambda: self.client.start_batch(
                    payload, style, reference, speed=speed, voice=voice, prompt_text=prompt_text
                ),
                self._on_started,
            )

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.import_button,
            self.paste_button,
            self.choose_reference_button,
            self.choose_output_button,
            self.split_mode_combo,
            self.merge_spin,
            self.voice_settings_group,
        ):
            widget.setEnabled(enabled)
        self.delimiters_edit.setEnabled(enabled and self.split_mode_combo.currentData() == SPLIT_PUNCTUATION)
        self.clear_reference_button.setEnabled(enabled and self.reference_path is not None)
        self._update_style_state()
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
            if enabled
            else QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._refresh()

    def _stop(self) -> None:
        """Dừng job: đoạn đang tạo làm nốt, các đoạn đã xong vẫn được lưu."""
        if not self._generating or self._stopping:
            return
        self._stopping = True
        self._refresh()
        self.status_label.setText(tr("tts.stopping"))
        if self._active_job_id:
            self._send_cancel(self._active_job_id)
        # Chưa có job ID thì _on_started sẽ gửi lệnh dừng ngay khi nhận được.

    def _send_cancel(self, job_id: str) -> None:
        operation = self.client.cancel_srt if self._job_mode == MODE_SRT else self.client.cancel_batch
        self._submit(lambda: operation(job_id), self._on_cancel_sent)

    def _on_cancel_sent(self, result: object) -> None:
        if isinstance(result, Exception) and self._generating:
            self._stopping = False
            self._refresh()
            self.status_label.setText(tr("tts.stop_error", detail=str(result)))

    def _set_row_states(self, completed: int, failed_row: int | None = None, cancelled: bool = False) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if failed_row is not None and row == failed_row:
                state = tr("tts.row_failed")
            elif row < completed:
                state = tr("tts.row_done")
            elif cancelled:
                state = tr("tts.row_cancelled")
            elif failed_row is None and row == completed and self._generating:
                state = tr("tts.row_running")
            elif failed_row is None:
                state = tr("tts.row_waiting")
            else:
                state = ""
            self.table.item(row, COL_STATUS).setText(state)
        self.table.blockSignals(False)

    def _on_started(self, result: object) -> None:
        if isinstance(result, Exception):
            self._finish_error(result)
            return
        self._active_job_id = str(result)
        if self._stopping:
            self._send_cancel(self._active_job_id)
        self._poll_status()
        if self._generating:
            self._poll_timer.start()

    def _poll_status(self) -> None:
        if not self._active_job_id:
            return
        job_id = self._active_job_id
        operation = self.client.srt_status if self._job_mode == MODE_SRT else self.client.batch_status
        self._submit(lambda: operation(job_id), self._on_status)

    def _on_status(self, result: object) -> None:
        if not self._generating or not self._active_job_id:
            return
        if isinstance(result, Exception):
            self._finish_error(result)
            return
        srt = self._job_mode == MODE_SRT
        completed = int(result.get("completed_cues" if srt else "completed_segments") or 0)
        total = int(result.get("total_cues" if srt else "total_segments") or self.table.rowCount())
        self._completed = completed
        self._set_row_states(completed)
        if not self._stopping:
            self.status_label.setText(tr("tts.progress", completed=completed, total=total))
        self._update_summary()
        status = result.get("status")
        if status in {"queued", "running"}:
            return
        self._poll_timer.stop()
        if status == "failed":
            position = result.get("failed_position" if srt else "failed_segment")
            failed_row = int(position) - 1 if position else None
            self._finish_error(RuntimeError(result.get("error") or status), failed_row)
            return
        if status == "cancelled" and completed == 0:
            self._finish()
            self._set_row_states(0, cancelled=True)
            self.status_label.setText(tr("tts.stopped_empty"))
            return
        job_id = self._active_job_id
        if srt:
            output_format = self._job_format
            self._submit(lambda: self.client.download_srt(job_id, output_format), self._on_downloaded)
        else:
            self._submit(lambda: self.client.download_batch(job_id), self._on_downloaded)

    def _on_downloaded(self, result: object) -> None:
        if isinstance(result, Exception):
            self._finish_error(result)
            return
        # Bị dừng thì sidecar chỉ trả về các đoạn đã xong (self._completed đoạn đầu).
        cancelled = self._stopping and self._completed < self.table.rowCount()
        finished = self._completed if cancelled else self.table.rowCount()
        try:
            if self._job_mode == MODE_SRT:
                target = self._srt_target()
                with target.open("xb") as output:
                    output.write(bytes(result))
                paths = [target] * finished
                message = tr("tts.srt_success", path=str(target))
            else:
                paths = extract_batch_mp3(bytes(result), self.output_directory, finished)
                message = tr("tts.batch_success", count=len(paths), path=str(self.output_directory))
        except Exception as exc:
            self._finish_error(exc)
            return
        if cancelled:
            message = tr("tts.stopped", done=finished, total=self.table.rowCount(), detail=message)
        self.table.blockSignals(True)
        for row, path in enumerate(paths):
            item = self.table.item(row, COL_OUTPUT)
            item.setText(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
        self.table.blockSignals(False)
        self._completed = finished
        self._finish()
        self._set_row_states(self._completed, cancelled=cancelled)
        self.status_label.setText(message)
        if self._job_mode == MODE_TEXT:
            self._submit(lambda: durations_ms(paths), self._on_durations)

    def _on_durations(self, result: object) -> None:
        """Điền timeline nối tiếp (khớp với file sau khi Join Mp3 theo thứ tự dòng)."""
        if self.mode != MODE_TEXT or self._generating:
            return
        if isinstance(result, Exception):
            self.status_label.setText(f"{self.status_label.text()} · {tr('tts.timeline_error', detail=str(result))}")
            return
        self.table.blockSignals(True)
        start = 0
        for row, duration in enumerate(result[: self.table.rowCount()]):
            self.table.item(row, COL_TIMING).setText(format_timing(start, start + duration))
            start += duration
        self.table.blockSignals(False)
        self._refresh()
        # Timeline khớp file sau Join Mp3, nên lưu luôn phụ đề .srt đi kèm.
        self._export_srt(append=True)

    def _has_timing(self) -> bool:
        return any(
            self.table.item(row, COL_TIMING) is not None and self.table.item(row, COL_TIMING).text().strip()
            for row in range(self.table.rowCount())
        )

    def srt_cues_for_export(self) -> list[SrtCue]:
        """Các dòng đã có timeline (dòng chưa tạo xong thì bỏ qua), theo thứ tự bảng."""
        cues = []
        for row in range(self.table.rowCount()):
            timing = self.table.item(row, COL_TIMING).text().strip()
            content = self.table.item(row, COL_CONTENT).text().strip()
            if timing and content:
                start_ms, end_ms = parse_timing(timing, row + 1)
                cues.append(SrtCue(row + 1, start_ms, end_ms, content))
        return cues

    def _export_srt(self, append: bool = False) -> Path | None:
        """Ghi phụ đề .srt từ cột Timing + Nội dung; không bao giờ ghi đè file có sẵn."""
        if self.output_directory is None or self._generating:
            return None
        prefix = f"{self.status_label.text()} · " if append and self.status_label.text() else ""
        try:
            cues = self.srt_cues_for_export()
            if not cues:
                raise ValueError(tr("tts.export_srt_empty"))
            stem = self.source_path.stem if self.source_path else "phu_de"
            target = available_target(self.output_directory, stem, ".srt")
            with target.open("x", encoding="utf-8", newline="\n") as output:
                output.write(format_srt(cues))
        except (OSError, ValueError, SrtError) as exc:
            self.status_label.setText(prefix + tr("tts.export_srt_error", detail=str(exc)))
            return None
        self.status_label.setText(prefix + tr("tts.export_srt_success", count=len(cues), path=str(target)))
        return target

    def joinable_files(self) -> list[Path]:
        """Các file MP3 đã tạo trong bảng (chế độ văn bản), theo thứ tự dòng."""
        if self.mode != MODE_TEXT:
            return []
        files = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_OUTPUT)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            if path and path.lower().endswith(".mp3") and Path(path).is_file():
                files.append(Path(path))
        return files

    def _join_mp3(self) -> None:
        files = self.joinable_files()
        if len(files) < 2 or self._generating or self._joining or self.output_directory is None:
            return
        stem = f"{self.source_path.stem}_joined" if self.source_path else "joined"
        # Lưu vào đúng thư mục đang hiển thị ở ô Đầu ra (cũng là thư mục nút Open mở).
        target = available_target(self.output_directory, stem)
        self._joining = True
        self._refresh()
        self.status_label.setText(tr("tts.join_running", count=len(files)))
        self._submit(lambda: join_mp3(files, target), self._on_joined)

    def _on_joined(self, result: object) -> None:
        self._joining = False
        self._refresh()
        if isinstance(result, Exception):
            self.status_label.setText(tr("tts.join_error", detail=str(result)))
        else:
            self.status_label.setText(tr("tts.join_success", path=str(result)))

    def _srt_target(self) -> Path:
        """Tên track theo file SRT; trùng thì thêm _1, _2… để không bao giờ ghi đè."""
        stem = self.source_path.stem if self.source_path else "long_tieng"
        target = self.output_directory / f"{stem}.{self._job_format}"
        counter = 1
        while target.exists():
            target = self.output_directory / f"{stem}_{counter}.{self._job_format}"
            counter += 1
        return target

    def _finish(self) -> None:
        self._stopping = False
        self._active_job_id = None
        self._poll_timer.stop()
        self._tick_timer.stop()
        self._elapsed_ms = self._stopwatch.elapsed()
        self._generating = False
        self._set_controls_enabled(True)

    def _finish_error(self, error: Exception, failed_row: int | None = None) -> None:
        self._finish()
        if failed_row is None and self._completed < self.table.rowCount():
            failed_row = self._completed
        self._set_row_states(self._completed, failed_row)
        if failed_row is not None:
            self.status_label.setText(tr("tts.failed_row", row=failed_row + 1, detail=str(error)))
        else:
            self.status_label.setText(tr("tts.error", detail=str(error)))

    def _play_row(self, row: int, column: int) -> None:
        if column in (COL_TIMING, COL_CONTENT):
            return  # nhấp đúp hai cột này là để sửa
        item = self.table.item(row, COL_OUTPUT)
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if path and Path(path).exists():
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()

    def closeEvent(self, event):
        self._poll_timer.stop()
        self._tick_timer.stop()
        self._health_timer.stop()
        self.thread_pool.waitForDone(5000)
        self._tasks.clear()
        self._player.stop()
        super().closeEvent(event)
