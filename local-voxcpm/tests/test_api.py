import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app import create_app


class FakeModel:
    def __init__(self, fail=False, delay=0):
        self.tts_model = type("TtsModel", (), {"sample_rate": 48000})()
        self.calls = []
        self.fail = fail
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()

    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(text)
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("inference failed")
            return np.zeros(480, dtype=np.float32)
        finally:
            with self.guard:
                self.active -= 1


def test_health_loading_then_ready():
    model = FakeModel()
    app = create_app(model_loader=lambda: model)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ready", "detail": None}


def test_generate_returns_wav_bytes_and_forwards_style():
    model = FakeModel()
    app = create_app(model_loader=lambda: model)
    with TestClient(app) as client:
        response = client.post("/generate", json={"text": "Xin chào", "style": "giọng ấm"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"
    assert model.calls == ["(giọng ấm)Xin chào"]
    audio, sample_rate = sf.read(io.BytesIO(response.content))
    assert sample_rate == 48000
    assert len(audio) == 480


def test_generate_rejects_empty_and_over_limit_text():
    model = FakeModel()
    app = create_app(model_loader=lambda: model)
    with TestClient(app) as client:
        empty = client.post("/generate", json={"text": "   "})
        long = client.post("/generate", json={"text": "x" * 4001})
    assert empty.status_code == 422
    assert long.status_code == 422
    assert model.calls == []


def test_generate_returns_not_ready_when_model_load_failed():
    app = create_app(model_loader=lambda: (_ for _ in ()).throw(RuntimeError("load failed")))
    with TestClient(app) as client:
        response = client.post("/generate", json={"text": "hello"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model_not_ready"


def test_generate_serializes_concurrent_calls():
    model = FakeModel(delay=0.05)
    app = create_app(model_loader=lambda: model)
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda text: client.post("/generate", json={"text": text}), ["one", "two"]))
    assert [response.status_code for response in results] == [200, 200]
    assert model.max_active == 1


def test_generate_maps_model_exception_to_structured_error():
    app = create_app(model_loader=lambda: FakeModel(fail=True))
    with TestClient(app) as client:
        response = client.post("/generate", json={"text": "hello"})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "generation_failed"
