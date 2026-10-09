"""Ghép nhiều file MP3 thành một bằng ffmpeg (concat demuxer), không ghép byte thô."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class Mp3JoinError(RuntimeError):
    pass


def available_target(directory: Path, stem: str, suffix: str = ".mp3") -> Path:
    """Tên file chưa tồn tại: stem.mp3, stem_1.mp3, stem_2.mp3…"""
    target = directory / f"{stem}{suffix}"
    counter = 1
    while target.exists():
        target = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return target


def _list_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
    return f"file '{escaped}'\n"


def durations_ms(files: list[Path], ffprobe: str | None = None) -> list[int]:
    """Độ dài từng file (ms) đo bằng ffprobe, theo đúng thứ tự."""
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise Mp3JoinError("Không tìm thấy ffprobe trong PATH. Cài ffmpeg rồi thử lại.")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = []
    for path in files:
        probe = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, creationflags=flags,
        )
        try:
            result.append(round(float(probe.stdout.decode().strip()) * 1000))
        except ValueError:
            raise Mp3JoinError(f"Không đọc được độ dài {path.name}") from None
    return result


def join_mp3(files: list[Path], destination: Path, ffmpeg: str | None = None) -> Path:
    """Ghép files theo đúng thứ tự vào destination (không bao giờ ghi đè)."""
    if len(files) < 2:
        raise Mp3JoinError("Cần ít nhất 2 file MP3 để ghép.")
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise Mp3JoinError(f"Không tìm thấy file: {', '.join(missing)}")
    if destination.exists():
        raise Mp3JoinError(f"File đã tồn tại: {destination.name}")
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise Mp3JoinError("Không tìm thấy ffmpeg trong PATH. Cài ffmpeg rồi thử lại.")
    with tempfile.TemporaryDirectory() as temp:
        playlist = Path(temp) / "list.txt"
        playlist.write_text("".join(_list_line(path) for path in files), encoding="utf-8")
        command = [
            executable, "-hide_banner", "-loglevel", "error", "-n",
            "-f", "concat", "-safe", "0", "-i", str(playlist),
            "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-ac", "1",
            str(destination),
        ]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(command, capture_output=True, creationflags=flags)
    if result.returncode != 0 or not destination.is_file():
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Mp3JoinError(detail[-1] if detail else f"ffmpeg lỗi (mã {result.returncode})")
    return destination
