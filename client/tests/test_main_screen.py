import io
import time
import zipfile

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.srt import SrtCue
from llvoice.ui.main_window import MainWindow
from llvoice.ui.tts_page import (
    COL_CONTENT,
    COL_OUTPUT,
    COL_STATUS,
    COL_TIMING,
    TtsPage,
    looks_like_srt,
)

JOB = "123e4567-e89b-12d3-a456-426614174000"
SRT = "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:05,000\nWorld"


class FakeClient:
    def __init__(self, fail_at=None):
        self.batch = None
        self.srt = None
        self.fail_at = fail_at

    def health(self):
        return {"status": "ready"}

    def start_batch(self, segments, style=None, reference_audio=None, speed=1.0, voice=None, prompt_text=None):
        self.batch = (list(segments), style, reference_audio)
        self.speed = speed
        self.voice = voice
        self.prompt_text = prompt_text
        return JOB

    def batch_status(self, job_id):
        total = len(self.batch[0])
        if self.fail_at:
            return {"status": "failed", "completed_segments": self.fail_at - 1, "total_segments": total, "failed_segment": self.fail_at, "error": "CUDA out of memory"}
        return {"status": "completed", "completed_segments": total, "total_segments": total}

    def download_batch(self, job_id):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for index in range(1, len(self.batch[0]) + 1):
                archive.writestr(f"{index:03d}.mp3", b"mp3-data")
        return stream.getvalue()

    def start_srt(self, cues, style, output_format, reference_audio=None, speed=1.0, voice=None, prompt_text=None):
        self.srt = (list(cues), style, output_format, reference_audio)
        self.speed = speed
        self.voice = voice
        self.prompt_text = prompt_text
        return JOB

    def srt_status(self, job_id):
        return {"status": "completed", "completed_cues": len(self.srt[0]), "total_cues": len(self.srt[0])}

    def download_srt(self, job_id, output_format):
        return b"RIFF" + b"x" * 40


def _app():
    i18n.load("vi")
    return QApplication.instance() or QApplication([])


def _page(client, tmp_path=None):
    _app()
    pool = QThreadPool()
    page = TtsPage(client, thread_pool=pool)
    page.warnings = []
    page._warn = page.warnings.append  # không mở hộp thoại trong test
    if tmp_path is not None:
        page.set_output_directory(tmp_path)
    return page, pool


def _run(page):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and page._generating:
        _app().processEvents()
        time.sleep(0.01)
    _app().processEvents()
    assert not page._generating


def test_window_is_single_screen_with_toolbar():
    _app()
    window = MainWindow(voxcpm_client=FakeClient(), thread_pool=QThreadPool())
    assert window.centralWidget() is window.tts_page
    page = window.tts_page
    assert [page.table.horizontalHeaderItem(c).text() for c in range(5)] == ["Id", "Output", "Timing", "Content", "Status"]
    assert page.start_button.isEnabled()
    assert page.speed_spin.value() == 1.0
    window.close()


def test_text_rows_split_and_generate_numbered_mp3(tmp_path, monkeypatch):
    import llvoice.ui.tts_page as tts_page

    measured = []
    monkeypatch.setattr(tts_page, "durations_ms", lambda files: measured.append(files) or [1500, 800, 2000, 61250])
    client = FakeClient()
    page, pool = _page(client, tmp_path)
    page.load_text("Một, Hai. Ba\nBốn")
    assert page.table.rowCount() == 4
    assert not page.table.isColumnHidden(COL_TIMING)
    assert page.table.item(0, COL_TIMING).text() == ""
    page.table.item(1, COL_CONTENT).setText("Hai đã sửa")
    page.style_edit.setText("giọng ấm")
    page.start_button.click()
    _run(page)
    assert pool.waitForDone(2000)
    _app().processEvents()
    assert client.batch == (["Một", "Hai đã sửa", "Ba", "Bốn"], "giọng ấm", None)
    assert (tmp_path / "004.mp3").read_bytes() == b"mp3-data"
    assert page.table.item(3, COL_OUTPUT).text() == "004.mp3"
    assert page.table.item(0, COL_STATUS).text() == "Xong"
    assert "Done: 4" in page.summary_label.text()
    assert [path.name for path in measured[0]] == ["001.mp3", "002.mp3", "003.mp3", "004.mp3"]
    assert [page.table.item(row, COL_TIMING).text() for row in range(4)] == [
        "00:00:00,000 --> 00:00:01,500",
        "00:00:01,500 --> 00:00:02,300",
        "00:00:02,300 --> 00:00:04,300",
        "00:00:04,300 --> 00:01:05,550",
    ]
    page.close()


def test_join_mp3_button_joins_generated_rows_in_order(tmp_path, monkeypatch):
    import llvoice.ui.tts_page as tts_page

    joined = []

    def fake_join(files, destination):
        joined.append((list(files), destination))
        destination.write_bytes(b"joined")
        return destination

    monkeypatch.setattr(tts_page, "join_mp3", fake_join)
    page, pool = _page(FakeClient(), tmp_path)
    page.load_text("Một. Hai. Ba")
    assert not page.join_button.isEnabled()
    (tmp_path / "joined.mp3").write_bytes(b"user-data")
    page.start_button.click()
    _run(page)
    assert page.join_button.isEnabled()
    page.join_button.click()
    assert pool.waitForDone(2000)
    _app().processEvents()
    files, destination = joined[0]
    assert [path.name for path in files] == ["001.mp3", "002.mp3", "003.mp3"]
    assert destination == tmp_path / "joined_1.mp3"
    assert (tmp_path / "joined.mp3").read_bytes() == b"user-data"
    assert "joined_1.mp3" in page.status_label.text()
    assert page.join_button.isEnabled()
    page.close()


def test_open_output_opens_exactly_the_shown_directory(tmp_path, monkeypatch):
    import llvoice.ui.tts_page as tts_page

    opened = []
    monkeypatch.setattr(tts_page.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    folder = tmp_path / "Thư mục ra #1"
    folder.mkdir()
    page, _pool = _page(FakeClient(), tmp_path / "Thư mục ra #1" / ".." / "Thư mục ra #1")
    assert page.output_edit.text() == str(folder.resolve())
    page.open_output_button.click()
    assert tts_page.Path(opened[0]) == tts_page.Path(page.output_edit.text())
    page.set_output_directory(tmp_path / "không có")
    page.open_output_button.click()
    assert len(opened) == 1
    assert "không tồn tại" in page.status_label.text()
    page.close()


def test_srt_rows_keep_timing_and_write_track_without_overwrite(tmp_path):
    client = FakeClient()
    page, _pool = _page(client, tmp_path)
    (tmp_path / "long_tieng.wav").write_bytes(b"user-data")
    page.load_pasted(SRT)
    assert not page.table.isColumnHidden(COL_TIMING)
    assert page.table.item(0, COL_TIMING).text() == "00:00:00,000 --> 00:00:02,000"
    page.table.item(1, COL_TIMING).setText("00:00:03,000 --> 00:00:06,000")
    page.start_button.click()
    _run(page)
    cues, _style, output_format, _ref = client.srt
    assert cues[1] == SrtCue(2, 3000, 6000, "World")
    assert output_format == "wav"
    assert (tmp_path / "long_tieng.wav").read_bytes() == b"user-data"
    assert (tmp_path / "long_tieng_1.wav").read_bytes().startswith(b"RIFF")
    page.close()


def test_invalid_edit_blocks_start_and_shows_error(tmp_path):
    client = FakeClient()
    page, _pool = _page(client, tmp_path)
    page.load_pasted(SRT)
    page.table.item(1, COL_TIMING).setText("00:00:01,000 --> 00:00:04,000")
    assert page.error_label.text()
    assert page.start_button.isEnabled()
    page.start_button.click()
    assert not page._generating and client.srt is None
    assert page.warnings == [page.error_label.text()]
    page.close()


def test_start_is_clickable_and_lists_what_is_missing(tmp_path):
    client = FakeClient()
    page, _pool = _page(client)
    assert page.start_button.isEnabled()
    page.start_button.click()
    assert not page._generating and client.batch is None
    message = page.warnings[-1]
    assert "nội dung" in message and "thư mục đầu ra" in message
    assert message in page.error_label.text()
    page.load_text("A")
    page.start_button.click()
    assert "nội dung" not in page.warnings[-1] and "thư mục đầu ra" in page.warnings[-1]
    page.set_output_directory(tmp_path)
    page.reference_path = tmp_path / "mất.wav"
    page.start_button.click()
    assert "mất.wav" in page.warnings[-1]
    assert client.batch is None
    page.close()


def test_start_rejects_existing_mp3(tmp_path):
    page, _pool = _page(FakeClient())
    page.load_text("A")
    (tmp_path / "001.mp3").write_bytes(b"old")
    page.set_output_directory(tmp_path)
    page.start_button.click()
    assert not page._generating
    assert "001.mp3" in page.error_label.text() and "001.mp3" in page.warnings[-1]
    assert (tmp_path / "001.mp3").read_bytes() == b"old"
    page.close()


def test_speed_is_sent_for_text_and_srt(tmp_path):
    client = FakeClient()
    page, pool = _page(client, tmp_path)
    page.load_text("A")
    page.speed_spin.setValue(1.25)
    page.start_button.click()
    _run(page)
    assert client.speed == 1.25
    page.load_pasted(SRT)
    page.speed_spin.setValue(0.8)
    page.start_button.click()
    _run(page)
    assert client.speed == 0.8
    page.speed_reset_button.click()
    assert page.speed_spin.value() == 1.0
    assert pool.waitForDone(2000)
    page.close()


def test_voice_settings_group_matches_elevenlabs_defaults_and_is_sent(tmp_path):
    from llvoice.voxcpm_api import DEFAULT_VOICE, VoiceSettings

    client = FakeClient()
    page, pool = _page(client, tmp_path)
    assert page.voice_settings_group.isChecked()
    assert (page.speed_spin.value(), page.stability_spin.value(), page.similarity_spin.value(), page.style_spin.value()) == (1.0, 50, 75, 0)
    assert page.speaker_boost_check.isChecked()
    page.load_text("A")
    page.stability_spin.setValue(80)
    page.similarity_spin.setValue(90)
    page.style_spin.setValue(40)
    page.speaker_boost_check.setChecked(False)
    page.start_button.click()
    assert not page.voice_settings_group.isEnabled()
    _run(page)
    assert page.voice_settings_group.isEnabled()
    assert client.voice == VoiceSettings(stability=80, similarity=90, style_exaggeration=40, speaker_boost=False)
    page.load_pasted(SRT)
    page.speed_spin.setValue(1.5)
    page.voice_settings_group.setChecked(False)
    page.start_button.click()
    _run(page)
    assert (client.speed, client.voice) == (1.0, DEFAULT_VOICE)
    assert not page.speed_reset_button.isEnabled()
    page.voice_settings_group.setChecked(True)
    page.speed_reset_button.click()
    assert (page.stability_spin.value(), page.similarity_spin.value(), page.style_spin.value()) == (50, 75, 0)
    assert page.speaker_boost_check.isChecked()
    assert pool.waitForDone(2000)
    page.close()


def test_prompt_text_needs_reference_and_is_sent(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    reference = tmp_path / "mau.wav"
    reference.write_bytes(b"RIFF")
    other = tmp_path / "khac.mp3"
    other.write_bytes(b"ID3")
    client = FakeClient()
    page, pool = _page(client, tmp_path)
    assert not page.prompt_text_edit.isEnabled()
    assert page.prompt_text() is None
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(reference), ""))
    page.choose_reference_button.click()
    assert page.prompt_text_edit.isEnabled() and page.style_edit.isEnabled()
    page.prompt_text_edit.setText("  Xin chào các bạn  ")
    assert not page.style_edit.isEnabled()  # mô tả giọng bị bỏ qua khi có lời thoại mẫu
    page.load_text("A")
    page.start_button.click()
    assert not page.prompt_text_edit.isEnabled()
    _run(page)
    assert client.prompt_text == "Xin chào các bạn"
    assert page.prompt_text_edit.isEnabled() and not page.style_edit.isEnabled()
    page.load_pasted(SRT)
    page.start_button.click()
    _run(page)
    assert client.prompt_text == "Xin chào các bạn"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(other), ""))
    page.choose_reference_button.click()
    assert page.prompt_text_edit.text() == ""  # audio khác thì lời thoại cũ không còn đúng
    page.prompt_text_edit.setText("Lời mới")
    page.clear_reference_button.click()
    assert page.prompt_text_edit.text() == "" and not page.prompt_text_edit.isEnabled()
    assert page.style_edit.isEnabled()
    assert pool.waitForDone(2000)
    page.close()


def test_failure_marks_row_and_reenables_controls(tmp_path):
    page, _pool = _page(FakeClient(fail_at=2), tmp_path)
    page.load_text("A\nB\nC")
    page.start_button.click()
    _run(page)
    assert [page.table.item(r, COL_STATUS).text() for r in range(3)] == ["Xong", "Lỗi", ""]
    assert "dòng 2" in page.status_label.text() and "CUDA" in page.status_label.text()
    assert page.start_button.isEnabled() and page.import_button.isEnabled()
    page.close()


def test_import_file_detects_type(tmp_path, monkeypatch):
    page, _pool = _page(FakeClient(), tmp_path)
    srt_file = tmp_path / "phim.srt"
    srt_file.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(srt_file), ""))
    page.import_button.click()
    assert page.mode == "srt" and page.table.rowCount() == 2
    assert "phim.srt" in page.summary_label.text()
    txt_file = tmp_path / "doc.txt"
    txt_file.write_text("x, y", encoding="utf-8")
    monkeypatch.setattr("llvoice.ui.tts_page.QFileDialog.getOpenFileName", lambda *a, **k: (str(txt_file), ""))
    page.import_button.click()
    assert page.mode == "text" and page.table.rowCount() == 2
    page.close()


def test_srt_detection():
    assert looks_like_srt(SRT)
    assert not looks_like_srt("1. Mở đầu\nnội dung")


def test_split_options_resplit_loaded_text_and_skip_srt(tmp_path):
    from llvoice.text_segments import SPLIT_LINES
    from llvoice.ui.tts_page import PasteDialog

    client = FakeClient()
    page, _pool = _page(client, tmp_path)
    page.load_pasted("Một, hai.\nBa")
    assert page.table.rowCount() == 3
    page.split_mode_combo.setCurrentIndex(page.split_mode_combo.findData(SPLIT_LINES))
    assert [page.table.item(r, COL_CONTENT).text() for r in range(page.table.rowCount())] == ["Một, hai.", "Ba"]
    assert not page.delimiters_edit.isEnabled()
    page.merge_spin.setValue(100)
    assert page.table.rowCount() == 1
    page.start_button.click()
    _run(page)
    assert client.batch[0] == ["Một, hai. Ba"]

    dialog = PasteDialog(page.split_options(), page)
    dialog.text_edit.setPlainText("a\nb")
    assert "1 đoạn" in dialog.count_label.text()
    dialog.text_edit.setPlainText(SRT)
    assert "SRT" in dialog.count_label.text()

    page.load_pasted(SRT)
    page.merge_spin.setValue(0)
    assert page.mode == "srt" and page.table.rowCount() == 2
    page.close()


class StoppableClient(FakeClient):
    """Job 3 đoạn; sau khi nhận lệnh Dừng thì báo cancelled với 1 đoạn đã xong."""

    def __init__(self):
        super().__init__()
        self.cancelled = []

    def cancel_batch(self, job_id):
        self.cancelled.append(("batch", job_id))

    def cancel_srt(self, job_id):
        self.cancelled.append(("srt", job_id))

    def batch_status(self, job_id):
        if self.cancelled:
            return {"status": "cancelled", "completed_segments": 1, "total_segments": 3}
        return {"status": "running", "completed_segments": 1, "total_segments": 3}

    def download_batch(self, job_id):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("001.mp3", b"mp3-data")
        return stream.getvalue()


def _wait(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not condition():
        _app().processEvents()
        time.sleep(0.01)
    assert condition()


def test_stop_keeps_finished_rows_and_marks_the_rest(tmp_path, monkeypatch):
    import llvoice.ui.tts_page as tts_page

    monkeypatch.setattr(tts_page, "durations_ms", lambda files: [1200] * len(files))
    client = StoppableClient()
    page, pool = _page(client, tmp_path)
    page.load_text("Một\nHai\nBa")
    assert not page.stop_button.isEnabled()
    page.start_button.click()
    _wait(lambda: page._active_job_id is not None and page.table.item(0, COL_STATUS).text() == "Xong")
    assert page.stop_button.isEnabled()
    page.stop_button.click()
    assert not page.stop_button.isEnabled()
    _run(page)
    assert pool.waitForDone(2000)
    _app().processEvents()
    assert client.cancelled == [("batch", JOB)]
    assert (tmp_path / "001.mp3").exists() and not (tmp_path / "002.mp3").exists()
    assert [page.table.item(r, COL_STATUS).text() for r in range(3)] == ["Xong", "Đã dừng", "Đã dừng"]
    assert page.table.item(0, COL_OUTPUT).text() == "001.mp3"
    assert "Đã dừng ở 1/3" in page.status_label.text()
    # Phụ đề tự lưu chỉ gồm dòng đã tạo xong.
    assert (tmp_path / "phu_de.srt").read_text(encoding="utf-8") == "1\n00:00:00,000 --> 00:00:01,200\nMột\n"
    assert page.start_button.isEnabled() and not page.stop_button.isEnabled()
    page.close()


def test_text_generation_auto_exports_srt_and_button_never_overwrites(tmp_path, monkeypatch):
    import llvoice.ui.tts_page as tts_page

    monkeypatch.setattr(tts_page, "durations_ms", lambda files: [1500, 800][: len(files)])
    page, pool = _page(FakeClient(), tmp_path)
    page.load_text("Xin chào\nTạm biệt")
    assert not page.export_srt_button.isEnabled()
    page.start_button.click()
    _run(page)
    assert pool.waitForDone(2000)
    _app().processEvents()
    expected = "1\n00:00:00,000 --> 00:00:01,500\nXin chào\n\n2\n00:00:01,500 --> 00:00:02,300\nTạm biệt\n"
    assert (tmp_path / "phu_de.srt").read_bytes() == expected.encode("utf-8")
    assert "phu_de.srt" in page.status_label.text()
    assert page.export_srt_button.isEnabled()
    page.table.item(1, COL_CONTENT).setText("Hẹn gặp lại")
    page.export_srt_button.click()
    assert (tmp_path / "phu_de.srt").read_bytes() == expected.encode("utf-8")
    assert "Hẹn gặp lại" in (tmp_path / "phu_de_1.srt").read_text(encoding="utf-8")
    assert page.status_label.text().startswith("Đã xuất phụ đề (2 dòng)")
    page.close()


def test_srt_mode_export_uses_edited_timings(tmp_path):
    page, _pool = _page(FakeClient(), tmp_path)
    page.load_pasted(SRT)
    assert page.export_srt_button.isEnabled()
    page.table.item(1, COL_TIMING).setText("00:00:03,000 --> 00:00:06,000")
    page.export_srt_button.click()
    text = (tmp_path / "phu_de.srt").read_text(encoding="utf-8")
    assert "2\n00:00:03,000 --> 00:00:06,000\nWorld\n" in text
    page.close()
