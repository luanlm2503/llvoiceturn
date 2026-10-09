import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.ui.main_window import TABS
from llvoice.ui.srt_page import SrtPage
from llvoice.srt import SrtCue

SRT = "1\n00:00:00,000 --> 00:00:02,000\nHello"


class FakeClient:
    def __init__(self): self.submitted = None
    def start_srt(self, cues, style, output_format, reference_audio=None): self.submitted = (list(cues), style, output_format, reference_audio); return "123e4567-e89b-12d3-a456-426614174000"
    def srt_status(self, job_id): return {"status": "completed", "completed_cues": 1, "total_cues": 1}
    def download_srt(self, job_id, output_format): return b"RIFF" + b"x" * 80


def app():
    i18n.load("vi")
    return QApplication.instance() or QApplication([])


def spin(application, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        application.processEvents(); time.sleep(.01)
    application.processEvents()
    assert predicate()


def test_paste_edit_and_submit_preserves_edited_cue(tmp_path):
    qapp = app(); client = FakeClient(); page = SrtPage(client)
    page.output_path_edit.setText(str(tmp_path / "dub.wav"))
    page.input_edit.setPlainText(SRT)
    page.parse_button.click()
    assert page.cue_table.rowCount() == 1 and page.generate_button.isEnabled()
    page.cue_table.item(0, 3).setText("Edited speech")
    page.style_edit.setText("warm")
    page.generate_button.click()
    spin(qapp, lambda: not page._generating)
    cues, style, output_format, reference = client.submitted
    assert cues == [SrtCue(1, 0, 2000, "Edited speech")]
    assert style == "warm" and output_format == "wav" and reference is None
    assert "đã lưu track lồng tiếng" in page.status_label.text().lower()
    page.close()


def test_invalid_overlap_disables_generation_and_parse_file_uses_same_parser(tmp_path, monkeypatch):
    qapp = app(); page = SrtPage(FakeClient())
    page.output_path_edit.setText(str(tmp_path / "dub.wav"))
    page.input_edit.setPlainText("1\n00:00:00,000 --> 00:00:02,000\na\n\n2\n00:00:01,000 --> 00:00:03,000\nb")
    page.parse_button.click()
    assert not page.generate_button.isEnabled()
    file_path = tmp_path / "one.srt"; file_path.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr("llvoice.ui.srt_page.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(file_path), "SRT"))
    page.open_file_button.click()
    assert page.cue_table.rowCount() == 1 and page.generate_button.isEnabled()
    page.close()


def test_failed_request_restores_controls(tmp_path):
    qapp = app(); client = FakeClient()
    def fail(*args): raise RuntimeError("sidecar unavailable")
    client.start_srt = fail
    page = SrtPage(client)
    page.output_path_edit.setText(str(tmp_path / "dub.wav")); page.input_edit.setPlainText(SRT); page.parse_button.click(); page.generate_button.click()
    spin(qapp, lambda: not page._generating)
    assert page.generate_button.isEnabled() and "sidecar unavailable" in page.status_label.text()
    page.close()


def test_invalid_table_edit_shows_error_and_disables_generation(tmp_path):
    qapp = app(); page = SrtPage(FakeClient())
    page.output_path_edit.setText(str(tmp_path / "dub.wav")); page.input_edit.setPlainText(SRT); page.parse_button.click()
    page.cue_table.item(0, 1).setText("bad timestamp")
    assert not page.generate_button.isEnabled()
    assert page.error_label.text()
    page.close()


def test_existing_output_is_never_overwritten(tmp_path):
    qapp = app(); page = SrtPage(FakeClient())
    target = tmp_path / "dub.wav"; target.write_bytes(b"user-data")
    page.output_path_edit.setText(str(target)); page.input_edit.setPlainText(SRT); page.parse_button.click()
    assert not page.generate_button.isEnabled()
    page.close()
