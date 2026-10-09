import io
import wave

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app import create_app


def wav_bytes(seconds=0.02, sample_rate=16000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * int(seconds * sample_rate))
    return stream.getvalue()


class FakeModel:
    def __init__(self, fail=False):
        self.tts_model = type("Tts", (), {"sample_rate": 16000})()
        self.fail = fail
        self.calls = []

    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None):
        self.calls.append((text, reference_wav_path))
        if self.fail:
            raise RuntimeError("inference failed")
        return np.zeros(160, dtype=np.float32)


def ready_client(model):
    app = create_app(model_loader=lambda: model)
    context = TestClient(app)
    context.__enter__()
    return context


def test_clone_wav_passes_reference_path_and_returns_wav():
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/clone", data={"text": "Xin chào", "style": "giọng ấm"}, files={"reference_audio": ("sample.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"
    assert model.calls[0][0] == "(giọng ấm)Xin chào"
    ref_path = model.calls[0][1]
    assert ref_path is not None
    assert not __import__("os").path.exists(ref_path)


def test_clone_mp3_is_decoded_and_passed_as_wav(tmp_path):
    import subprocess
    import soundfile as soundfile

    source = tmp_path / "source.wav"
    soundfile.write(source, np.zeros(1600, dtype=np.float32), 16000)
    mp3 = tmp_path / "sample.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), str(mp3)], check=True)
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("sample.mp3", mp3.read_bytes(), "audio/mpeg")})
    assert response.status_code == 200
    assert model.calls[0][1].endswith(".wav")
    assert not __import__("os").path.exists(model.calls[0][1])


def test_clone_rejects_empty_unsupported_corrupt_and_oversized_uploads():
    model = FakeModel()
    with ready_client(model) as client:
        empty = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("empty.wav", b"", "audio/wav")})
        unsupported = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("sample.txt", b"data", "text/plain")})
        corrupt = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("broken.wav", b"not wav", "audio/wav")})
        oversized = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("large.wav", b"x" * (25 * 1024 * 1024 + 1), "audio/wav")})
    assert [r.status_code for r in (empty, unsupported, corrupt, oversized)] == [422, 422, 422, 413]
    assert model.calls == []


def test_clone_rejects_duration_over_30_seconds():
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("long.wav", wav_bytes(seconds=30.1), "audio/wav")})
    assert response.status_code == 422
    assert model.calls == []


def test_clone_temp_files_are_removed_after_inference_failure():
    model = FakeModel(fail=True)
    with ready_client(model) as client:
        response = client.post("/clone", data={"text": "hello"}, files={"reference_audio": ("sample.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "generation_failed"
    assert not __import__("os").path.exists(model.calls[0][1])


def test_clone_rejects_missing_multipart_fields():
    model = FakeModel()
    with ready_client(model) as client:
        missing_text = client.post("/clone", files={"reference_audio": ("sample.wav", wav_bytes(), "audio/wav")})
        missing_file = client.post("/clone", data={"text": "hello"})
    assert missing_text.status_code == 422
    assert missing_file.status_code == 422
    assert model.calls == []


def test_clone_preserves_plain_generate_without_reference():
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/generate", json={"text": "plain TTS"})
    assert response.status_code == 200
    assert model.calls == [("plain TTS", None)]
