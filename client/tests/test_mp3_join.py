import shutil
import subprocess

import pytest

from llvoice.mp3_join import Mp3JoinError, available_target, durations_ms, join_mp3

FFMPEG = shutil.which("ffmpeg")


def _tone(path, seconds):
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", "-ar", "44100", "-ac", "1", str(path)],
        check=True,
    )


def _duration(path):
    output = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(output.stdout.strip())


def test_available_target_never_overwrites(tmp_path):
    assert available_target(tmp_path, "joined") == tmp_path / "joined.mp3"
    (tmp_path / "joined.mp3").write_bytes(b"x")
    (tmp_path / "joined_1.mp3").write_bytes(b"x")
    assert available_target(tmp_path, "joined") == tmp_path / "joined_2.mp3"


def test_rejects_bad_input(tmp_path):
    first = tmp_path / "001.mp3"
    first.write_bytes(b"x")
    with pytest.raises(Mp3JoinError):
        join_mp3([first], tmp_path / "out.mp3")
    with pytest.raises(Mp3JoinError, match="002.mp3"):
        join_mp3([first, tmp_path / "002.mp3"], tmp_path / "out.mp3")
    (tmp_path / "002.mp3").write_bytes(b"x")
    (tmp_path / "out.mp3").write_bytes(b"keep")
    with pytest.raises(Mp3JoinError):
        join_mp3([first, tmp_path / "002.mp3"], tmp_path / "out.mp3")
    assert (tmp_path / "out.mp3").read_bytes() == b"keep"


def test_missing_ffmpeg_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    files = [tmp_path / "001.mp3", tmp_path / "002.mp3"]
    for path in files:
        path.write_bytes(b"x")
    with pytest.raises(Mp3JoinError, match="ffmpeg"):
        join_mp3(files, tmp_path / "out.mp3")


@pytest.mark.skipif(FFMPEG is None or shutil.which("ffprobe") is None, reason="cần ffmpeg")
def test_joins_in_order_with_combined_duration(tmp_path):
    folder = tmp_path / "có dấu 'x'"
    folder.mkdir()
    files = [folder / "001.mp3", folder / "002.mp3"]
    _tone(files[0], 1)
    _tone(files[1], 2)
    measured = durations_ms(files)
    assert abs(measured[0] - 1000) < 100 and abs(measured[1] - 2000) < 100
    result = join_mp3(files, folder / "joined.mp3")
    assert result == folder / "joined.mp3"
    assert abs(_duration(result) - 3.0) < 0.2

def test_available_target_supports_other_suffix(tmp_path):
    (tmp_path / "phu_de.srt").write_text("x")
    assert available_target(tmp_path, "phu_de", ".srt") == tmp_path / "phu_de_1.srt"
