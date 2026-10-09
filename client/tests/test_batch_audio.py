import io
import zipfile

import pytest

from llvoice.batch_audio import extract_batch_mp3


def make_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


def test_extract_valid_numbered_mp3_files(tmp_path):
    archive = make_zip([("001.mp3", b"mp3-one"), ("002.mp3", b"mp3-two")])
    paths = extract_batch_mp3(archive, tmp_path, 2)
    assert [path.name for path in paths] == ["001.mp3", "002.mp3"]
    assert [path.read_bytes() for path in paths] == [b"mp3-one", b"mp3-two"]


@pytest.mark.parametrize("entries", [
    [("../escape.mp3", b"x")],
    [("C:/escape.mp3", b"x")],
    [("001.wav", b"x")],
    [("001.mp3", b"x"), ("001.mp3", b"y")],
    [("001.mp3", b"x"), ("extra.mp3", b"y")],
    [("001.mp3", b"")],
])
def test_invalid_archive_is_rejected_without_partial_writes(tmp_path, entries):
    with pytest.raises(ValueError):
        extract_batch_mp3(make_zip(entries), tmp_path, 1)
    assert list(tmp_path.iterdir()) == []


def test_existing_destination_collision_rejected_without_overwrite(tmp_path):
    (tmp_path / "001.mp3").write_bytes(b"user data")
    with pytest.raises(FileExistsError):
        extract_batch_mp3(make_zip([("001.mp3", b"generated")]), tmp_path, 1)
    assert (tmp_path / "001.mp3").read_bytes() == b"user data"


def test_invalid_zip_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        extract_batch_mp3(b"not a zip", tmp_path, 1)
    assert list(tmp_path.iterdir()) == []
