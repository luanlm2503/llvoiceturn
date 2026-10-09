"""Local VoxCPM2 SRT dubbing page."""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from llvoice.i18n import tr
from llvoice.srt import SrtCue, SrtError, parse_srt, parse_srt_bytes, validate_cues
from llvoice.voxcpm_api import VoxCPMClient


class _SrtSignals(QObject):
    done = Signal(object)


class _SrtTask(QRunnable):
    def __init__(self, operation, callback):
        super().__init__()
        self.operation = operation
        self.signals = _SrtSignals()
        self.signals.done.connect(callback)

    def run(self):
        try:
            self.signals.done.emit(self.operation())
        except Exception as exc:
            self.signals.done.emit(exc)


class SrtPage(QWidget):
    def __init__(self, client: VoxCPMClient | None = None, *, thread_pool: QThreadPool | None = None):
        super().__init__()
        self.client = client or VoxCPMClient()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._tasks: set[_SrtTask] = set()
        self._cues: list[SrtCue] = []
        self._generating = False
        self._active_job_id: str | None = None
        self._last_error: str | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._poll)

        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.open_file_button = QPushButton(tr("srt.open"))
        self.open_file_button.clicked.connect(self._open_file)
        self.parse_button = QPushButton(tr("srt.parse"))
        self.parse_button.clicked.connect(self._parse_input)
        actions.addWidget(self.open_file_button); actions.addWidget(self.parse_button); actions.addStretch(1)
        layout.addLayout(actions)
        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(tr("srt.input_placeholder"))
        layout.addWidget(self.input_edit, 1)
        self.error_label = QLabel(""); self.error_label.setWordWrap(True); layout.addWidget(self.error_label)
        self.cue_table = QTableWidget(0, 4)
        self.cue_table.setHorizontalHeaderLabels([tr("srt.col_index"), tr("srt.col_start"), tr("srt.col_end"), tr("srt.col_text")])
        self.cue_table.horizontalHeader().setStretchLastSection(True)
        self.cue_table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.cue_table, 2)
        self.validation_label = QLabel(tr("srt.no_cues")); self.validation_label.setWordWrap(True); layout.addWidget(self.validation_label)

        form = QFormLayout()
        self.style_edit = QLineEdit(); self.style_edit.setPlaceholderText(tr("tts.style_placeholder")); form.addRow(tr("tts.style"), self.style_edit)
        reference_row = QHBoxLayout()
        self.reference_path: Path | None = None
        self.reference_label = QLabel(tr("tts.reference_none"))
        self.reference_button = QPushButton(tr("tts.reference_choose")); self.reference_button.clicked.connect(self._choose_reference)
        self.clear_reference_button = QPushButton(tr("tts.reference_clear")); self.clear_reference_button.setEnabled(False); self.clear_reference_button.clicked.connect(self._clear_reference)
        reference_row.addWidget(self.reference_button); reference_row.addWidget(self.clear_reference_button); reference_row.addWidget(self.reference_label, 1)
        form.addRow(tr("tts.reference_label"), reference_row)
        self.format_combo = QComboBox(); self.format_combo.addItem("WAV", "wav"); self.format_combo.addItem("MP3", "mp3"); form.addRow(tr("srt.output_format"), self.format_combo)
        output_row = QHBoxLayout()
        self.output_path_edit = QLineEdit(); self.output_path_edit.setPlaceholderText(tr("srt.output_path")); self.output_path_edit.textChanged.connect(self._update_generate_enabled)
        self.output_button = QPushButton(tr("srt.output_choose")); self.output_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.output_path_edit, 1); output_row.addWidget(self.output_button)
        form.addRow(tr("srt.output_file"), output_row)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.generate_button = QPushButton(tr("srt.generate")); self.generate_button.clicked.connect(self._generate)
        buttons.addWidget(self.generate_button); buttons.addStretch(1); layout.addLayout(buttons)
        self.status_label = QLabel(""); self.status_label.setWordWrap(True); layout.addWidget(self.status_label)
        self._update_generate_enabled()

    def _submit(self, operation, callback):
        task = _SrtTask(operation, callback); self._tasks.add(task)
        task.signals.done.connect(lambda _value, current=task: self._tasks.discard(current))
        self.thread_pool.start(task)

    def _open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr("srt.open_dialog"), "", "SubRip (*.srt)")
        if not filename: return
        try:
            data = Path(filename).read_bytes()
            cues = parse_srt_bytes(data)
        except (OSError, SrtError) as exc:
            self._last_error = str(exc)
            self.error_label.setText(tr("srt.parse_error", detail=str(exc)))
            self._cues = []; self._populate_table([]); self._update_generate_enabled(); return
        self.input_edit.setPlainText(data.decode("utf-8-sig"))
        self._set_cues(cues)

    def _parse_input(self):
        try:
            self._set_cues(parse_srt(self.input_edit.toPlainText()))
        except SrtError as exc:
            self._last_error = str(exc)
            self.error_label.setText(tr("srt.parse_error", detail=str(exc)))
            self._cues = []; self._populate_table([]); self._update_generate_enabled()

    def _set_cues(self, cues):
        self._cues = list(cues); self._last_error = None; self.error_label.clear(); self._populate_table(self._cues); self._update_generate_enabled()
        self.validation_label.setText(tr("srt.cue_count", count=len(self._cues)))

    def _populate_table(self, cues):
        self.cue_table.blockSignals(True)
        self.cue_table.setRowCount(len(cues))
        for row, cue in enumerate(cues):
            values = [str(cue.index), self._format_time(cue.start_ms), self._format_time(cue.end_ms), cue.text]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0: item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
                self.cue_table.setItem(row, column, item)
        self.cue_table.blockSignals(False)

    @staticmethod
    def _format_time(value):
        ms = int(value); hours, remainder = divmod(ms, 3_600_000); minutes, remainder = divmod(remainder, 60_000); seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _on_item_changed(self, _item):
        try:
            self._edited_cues()
            self.error_label.clear()
        except SrtError as exc:
            self._last_error = str(exc)
            self.error_label.setText(tr("srt.parse_error", detail=str(exc)))
        else:
            self._last_error = None
        self._update_generate_enabled()

    def _edited_cues(self):
        def parse_time(value):
            from llvoice.srt import _parse_time
            return _parse_time(value, 1)
        cues = []
        for row in range(self.cue_table.rowCount()):
            try:
                if any(self.cue_table.item(row, column) is None for column in range(4)):
                    raise ValueError("cue cells are missing")
                index = int(self.cue_table.item(row, 0).text())
                start = parse_time(self.cue_table.item(row, 1).text())
                end = parse_time(self.cue_table.item(row, 2).text())
                text = self.cue_table.item(row, 3).text()
                cues.append(SrtCue(index, start, end, text))
            except (ValueError, AttributeError, SrtError) as exc:
                raise SrtError(f"Cue row {row + 1}: {exc}") from exc
        return validate_cues(cues)

    def _update_generate_enabled(self, *_):
        output_path = Path(self.output_path_edit.text()).expanduser() if self.output_path_edit.text().strip() else None
        output_matches_format = bool(output_path and output_path.suffix.lower() == "." + self.format_combo.currentData())
        enabled = not self._generating and bool(self._cues) and output_matches_format and not (output_path and output_path.exists()) and self._last_error is None
        if enabled:
            try: self._edited_cues()
            except SrtError: enabled = False
        self.generate_button.setEnabled(enabled)

    def _choose_reference(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr("tts.reference_dialog"), "", "Audio reference (*.wav *.mp3)")
        if filename and Path(filename).suffix.lower() in {".wav", ".mp3"}:
            self.reference_path = Path(filename); self.reference_label.setText(self.reference_path.name); self.clear_reference_button.setEnabled(True)
        elif filename:
            self.error_label.setText(tr("tts.reference_invalid"))

    def _clear_reference(self):
        self.reference_path = None; self.reference_label.setText(tr("tts.reference_none")); self.clear_reference_button.setEnabled(False)

    def _choose_output(self):
        suffix = self.format_combo.currentData()
        filename, _ = QFileDialog.getSaveFileName(self, tr("srt.output_dialog"), f"dubbed.{suffix}", f"{suffix.upper()} (*.{suffix})")
        if filename: self.output_path_edit.setText(filename)

    def _generate(self):
        try:
            cues = self._edited_cues(); target = Path(self.output_path_edit.text()).expanduser()
            if target.exists(): raise FileExistsError(tr("srt.output_exists"))
            suffix = "." + self.format_combo.currentData()
            if target.suffix.lower() != suffix: raise ValueError(tr("srt.output_extension", extension=suffix))
        except Exception as exc:
            self.error_label.setText(tr("srt.parse_error", detail=str(exc))); return
        self._generating = True; self._set_controls(False); self.status_label.setText(tr("srt.starting"))
        self._output_target = target; self._output_format = self.format_combo.currentData()
        style = self.style_edit.text().strip() or None; reference = self.reference_path
        self._submit(lambda: self.client.start_srt(cues, style, self._output_format, reference), self._started)

    def _set_controls(self, enabled):
        for widget in (self.open_file_button, self.parse_button, self.input_edit, self.cue_table, self.style_edit, self.reference_button, self.clear_reference_button, self.format_combo, self.output_path_edit, self.output_button): widget.setEnabled(enabled)
        self.generate_button.setEnabled(enabled and bool(self._cues) and bool(self.output_path_edit.text().strip()))

    def _started(self, result):
        if isinstance(result, Exception): self._finish_error(result); return
        self._active_job_id = result; self._poll(); self._poll_timer.start()

    def _poll(self):
        if self._active_job_id: self._submit(lambda: self.client.srt_status(self._active_job_id), self._status)

    def _status(self, result):
        if not self._generating: return
        if isinstance(result, Exception): self._poll_timer.stop(); self._finish_error(result); return
        state = result.get("status")
        self.status_label.setText(tr("srt.progress", completed=result.get("completed_cues", 0), total=result.get("total_cues", 0)))
        if state in {"queued", "running"}: return
        self._poll_timer.stop()
        if state == "failed":
            self._finish_error(RuntimeError(tr("srt.job_failed", index=result.get("failed_index"), detail=result.get("error")))); return
        self._submit(lambda: self.client.download_srt(self._active_job_id, self._output_format), self._downloaded)

    def _downloaded(self, result):
        if isinstance(result, Exception): self._finish_error(result); return
        try:
            self._output_target.parent.mkdir(parents=True, exist_ok=True)
            with self._output_target.open("xb") as output: output.write(result)
        except Exception as exc:
            self._finish_error(exc); return
        self._active_job_id = None; self._generating = False; self._set_controls(True)
        self.status_label.setText(tr("srt.success", path=str(self._output_target)))
        self._update_generate_enabled()

    def _finish_error(self, error):
        self._poll_timer.stop(); self._active_job_id = None; self._generating = False; self._set_controls(True)
        self.status_label.setText(tr("srt.error", detail=str(error)))

    def closeEvent(self, event):
        self._poll_timer.stop(); self.thread_pool.waitForDone(5000); self._tasks.clear(); super().closeEvent(event)
