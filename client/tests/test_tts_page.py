import io
import time

import wave
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.ui.tts_page import TtsPage


class FakeClient:
    def __init__(self, health=None, wav=None, error=None):
        self.health_result = health or {"status": "ready", "detail": None}
        self.wav = wav or _wav_bytes()
        self.error = error
        self.generated = []
        self.health_calls = 0

    def health(self):
        self.health_calls += 1
        return self.health_result

    def generate(self, text, style=None):
        self.generated.append((text, style))
        if self.error:
            raise self.error
        return self.wav

    def start_batch(self, segments, style=None, reference_audio=None):
        self.generated.append((segments, style, reference_audio))
        if self.error:
            raise self.error
        self.segments = segments
        return "123e4567-e89b-12d3-a456-426614174000"

    def batch_status(self, job_id):
        return {"status": "completed", "completed_segments": len(self.segments), "total_segments": len(self.segments)}

    def download_batch(self, job_id):
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for index in range(1, len(self.segments) + 1):
                archive.writestr(f"{index:03d}.mp3", b"mp3-data")
        return stream.getvalue()


def _wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 100)
    return stream.getvalue()


def _app():
    i18n.load("vi")
    return QApplication.instance() or QApplication([])


def _wait(pool, timeout=3):
    deadline = time.monotonic() + timeout
    while not pool.waitForDone(20) and time.monotonic() < deadline:
        _app().processEvents()
    _app().processEvents()


def test_generate_disabled_for_empty_text():
    _app()
    page = TtsPage(FakeClient())
    assert not page.generate_button.isEnabled()
    page.text_edit.setPlainText("hello")
    assert not page.generate_button.isEnabled()


def test_generate_calls_sidecar_from_worker_and_displays_result():
    _app()
    fake = FakeClient()
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    page.output_directory = __import__("pathlib").Path(__import__("tempfile").mkdtemp())
    page.text_edit.setPlainText("Xin chào")
    page.style_edit.setText("giọng ấm")
    page.generate_button.click()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and page._generating:
        _app().processEvents()
        time.sleep(0.01)
    _app().processEvents()
    assert fake.generated[0] == (["Xin chào"], "giọng ấm", None)
    assert "Đã lưu 1 file MP3" in page.status_label.text()
    page.close()


def test_offline_health_shows_start_instruction():
    _app()
    fake = FakeClient()
    fake.health = lambda: (_ for _ in ()).throw(RuntimeError("Cannot reach VoxCPM2"))
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    _wait(pool)
    assert "run.ps1" in page.status_label.text()
    page.close()


def test_generation_error_is_visible_and_reenables_button():
    _app()
    fake = FakeClient(error=RuntimeError("CUDA out of memory"))
    pool = QThreadPool()
    page = TtsPage(fake, thread_pool=pool)
    page.output_directory = __import__("pathlib").Path(__import__("tempfile").mkdtemp())
    page.text_edit.setPlainText("hello")
    page.generate_button.click()
    _wait(pool)
    assert "CUDA out of memory" in page.status_label.text()
    assert page.generate_button.isEnabled()
    page.close()


def test_save_uses_user_selected_path(monkeypatch, tmp_path):
    _app()
    page = TtsPage(FakeClient())
    page.audio_path = tmp_path / "source.wav"
    page.audio_path.write_bytes(_wav_bytes())
    page.save_button.setEnabled(True)
    destination = tmp_path / "saved.wav"
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getSaveFileName", lambda *args, **kwargs: (str(destination), "WAV (*.wav)"))
    page.save_button.click()
    assert destination.read_bytes().startswith(b"RIFF")
