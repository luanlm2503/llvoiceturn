import io
import time
import wave
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.ui.tts_page import TtsPage


class FakeClient:
    def __init__(self):
        self.generated = []
        self.cloned = []

    def health(self):
        return {"status": "ready", "detail": None}

    def generate(self, text, style=None):
        self.generated.append((text, style))
        return b"RIFF" + b"\0" * 8 + b"WAVE" + b"\0" * 20

    def start_batch(self, segments, style=None, reference_audio=None):
        self.cloned.append((list(segments), Path(reference_audio) if reference_audio else None, style))
        self.segments = list(segments)
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


def wait(pool):
    deadline = time.monotonic() + 3
    while not pool.waitForDone(20) and time.monotonic() < deadline:
        QApplication.instance().processEvents()
    QApplication.instance().processEvents()


def setup_app():
    i18n.load("vi")
    return QApplication.instance() or QApplication([])


def test_reference_select_wav_and_mp3(monkeypatch, tmp_path):
    setup_app()
    fake = FakeClient()
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    wait(pool)
    first = tmp_path / "voice.wav"
    second = tmp_path / "voice.mp3"
    first.write_bytes(b"RIFF data")
    second.write_bytes(b"MP3 data")
    selections = iter([(str(first), "WAV"), (str(second), "MP3")])
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *args, **kwargs: next(selections))
    page.choose_reference_button.click()
    assert page.reference_path == first
    page.choose_reference_button.click()
    assert page.reference_path == second
    assert "voice.mp3" in page.reference_label.text()
    page.close()


def test_reference_cancel_or_clear_preserves_plain_local_batch(monkeypatch, tmp_path):
    setup_app()
    fake = FakeClient()
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    wait(pool)
    selected = tmp_path / "voice.wav"
    selected.write_bytes(b"RIFF data")
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(selected), "WAV"))
    page.choose_reference_button.click()
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *args, **kwargs: ("", ""))
    page.choose_reference_button.click()
    assert page.reference_path == selected
    page.clear_reference_button.click()
    assert page.reference_path is None
    page.output_directory = tmp_path
    page.text_edit.setPlainText("plain request")
    page.generate_button.click()
    wait(pool)
    assert fake.cloned == [(["plain request"], None, None)]
    assert fake.generated == []
    page.close()


def test_generate_with_reference_calls_batch_worker(tmp_path):
    setup_app()
    fake = FakeClient()
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    wait(pool)
    reference = tmp_path / "sample.mp3"
    reference.write_bytes(b"mp3")
    page.reference_path = reference
    page.output_directory = tmp_path
    page.text_edit.setPlainText("Xin chào")
    page.style_edit.setText("giọng ấm")
    page.generate_button.click()
    wait(pool)
    assert fake.cloned == [(["Xin chào"], reference, "giọng ấm")]
    assert fake.generated == []
    page.close()


def test_invalid_extension_shows_error_without_request(monkeypatch, tmp_path):
    setup_app()
    fake = FakeClient()
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    wait(pool)
    invalid = tmp_path / "voice.txt"
    invalid.write_text("not audio")
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(invalid), "All files"))
    page.choose_reference_button.click()
    assert "WAV" in page.status_label.text() or "MP3" in page.status_label.text()
    assert page.reference_path is None
    assert fake.generated == [] and fake.cloned == []
    page.close()


def test_clone_failure_reenables_controls_and_shows_error(tmp_path):
    setup_app()

    class FailingClient(FakeClient):
        def start_batch(self, segments, style=None, reference_audio=None):
            raise RuntimeError("invalid reference audio")

    pool = QThreadPool()
    page = TtsPage(FailingClient(), thread_pool=pool)
    wait(pool)
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"RIFFfake")
    page.reference_path = reference
    page.output_directory = tmp_path
    page.text_edit.setPlainText("hello")
    page.generate_button.click()
    wait(pool)
    assert "invalid reference audio" in page.status_label.text()
    assert page.generate_button.isEnabled()
    page.close()
