"""Safe extraction of a VoxCPM batch ZIP into a user-selected directory."""

import io
import re
import zipfile
from pathlib import Path


_EXPECTED_NAME = re.compile(r"^[0-9]{3,}\.mp3$")
_MAX_MEMBER_BYTES = 25 * 1024 * 1024


def extract_batch_mp3(archive: bytes, destination: Path, expected_count: int) -> list[Path]:
    destination = Path(destination)
    if not destination.is_dir():
        raise NotADirectoryError(f"Output directory does not exist: {destination}")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            infos = bundle.infolist()
            expected_names = [f"{index:03d}.mp3" for index in range(1, expected_count + 1)]
            names = [item.filename for item in infos]
            if len(infos) != expected_count or names != expected_names:
                raise ValueError("Batch ZIP must contain exactly the expected numbered MP3 files in order.")
            contents: list[bytes] = []
            for info in infos:
                if info.is_dir() or not _EXPECTED_NAME.fullmatch(info.filename) or info.flag_bits & 0x1:
                    raise ValueError("Batch ZIP contains an unsafe or unsupported entry.")
                if info.file_size <= 0 or info.file_size > _MAX_MEMBER_BYTES:
                    raise ValueError("Each MP3 must be non-empty and no larger than 25 MiB.")
                if (destination / info.filename).exists():
                    raise FileExistsError(f"Output file already exists: {destination / info.filename}")
                with bundle.open(info) as source:
                    data = source.read(_MAX_MEMBER_BYTES + 1)
                if len(data) != info.file_size or len(data) > _MAX_MEMBER_BYTES:
                    raise ValueError("Batch ZIP member size is invalid.")
                contents.append(data)
    except FileExistsError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Batch response is not a valid safe ZIP archive.") from exc

    written: list[Path] = []
    temporary: list[Path] = []
    try:
        for index, data in enumerate(contents, 1):
            final_path = destination / f"{index:03d}.mp3"
            temp_path = destination / f".{index:03d}.mp3.tmp"
            if final_path.exists() or temp_path.exists():
                raise FileExistsError(f"Output file already exists: {final_path}")
            with temp_path.open("xb") as output:
                output.write(data)
            temporary.append(temp_path)
        for index, temp_path in enumerate(temporary, 1):
            final_path = destination / f"{index:03d}.mp3"
            temp_path.replace(final_path)
            written.append(final_path)
        return written
    except Exception:
        for path in written + temporary:
            path.unlink(missing_ok=True)
        raise
