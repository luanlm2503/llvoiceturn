import json
import time

from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.ui.tts_page import TtsPage


class FakeBatchClient:
    def __init__(self):
        self.segments = None
        self.calls = []
    def health(self): return {"status": "ready"}
    def start_batch(self, segments, style=None, reference_audio=None):
        self.segments = list(segments)
        self.calls.append((style, reference_audio))
        return "123e4567-e89b-12d3-a456-426614174000"
    def batch_status(self, job_id):
        return {"status": "completed", "completed_segments": len(self.segments), "total_segments": len(self.segments)}
    def download_batch(self, job_id):
        import io, zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for index in range(1, len(self.segments) + 1):
                archive.writestr(f"{index:03d}.mp3", b"mp3-data")
        return stream.getvalue()
    def generate(self, *args): raise AssertionError("legacy single TTS should not be used")
    def clone(self, *args): raise AssertionError("legacy clone should not be used")


def spin(app, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not predicate():
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_preview_matches_batch_request_and_success_state(monkeypatch, tmp_path):
    i18n.load("vi")
    app = QApplication.instance() or QApplication([])
    client = FakeBatchClient()
    page = TtsPage(client)
    page.output_directory = tmp_path
    page.text_edit.setPlainText("Một, Hai. Ba/ Bốn\nNăm")
    assert page.preview_segments == ["Một", "Hai", "Ba", "Bốn", "Năm"]
    page.generate_button.click()
    spin(app, lambda: not page._generating)
    assert client.segments == page.preview_segments
    assert "5" in page.status_label.text()
    page.close()


def test_no_output_or_empty_segments_disables_batch():
    i18n.load("vi")
    app = QApplication.instance() or QApplication([])
    page = TtsPage(FakeBatchClient())
    page.text_edit.setPlainText("...,,/")
    app.processEvents()
    assert not page.generate_button.isEnabled()
    page.text_edit.setPlainText("some text")
    app.processEvents()
    assert not page.generate_button.isEnabled()
    page.close()


def test_batch_error_restores_controls(monkeypatch, tmp_path):
    i18n.load("vi")
    app = QApplication.instance() or QApplication([])
    client = FakeBatchClient()
    client.start_batch = lambda *args: (_ for _ in ()).throw(RuntimeError("sidecar offline"))
    page = TtsPage(client)
    page.output_directory = tmp_path
    page.text_edit.setPlainText("text")
    page.generate_button.click()
    spin(app, lambda: not page._generating)
    assert page.generate_button.isEnabled()
    assert "sidecar offline" in page.status_label.text()
    page.close()
