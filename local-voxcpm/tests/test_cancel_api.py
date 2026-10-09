import io
import json
import threading
import time
import zipfile

import numpy as np
from fastapi.testclient import TestClient

from app import create_app


class GatedModel:
    """Đoạn đầu tiên chờ test bấm Dừng rồi mới trả kết quả."""

    def __init__(self, samples=160):
        self.tts_model = type("Tts", (), {"sample_rate": 16000})()
        self.calls = []
        self.samples = samples
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None):
        self.calls.append(text)
        if len(self.calls) == 1:
            self.started.set()
            assert self.release.wait(10)
        return np.ones(self.samples, dtype=np.float32) * 0.05


def wait_status(client, path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(path).json()
        if state["status"] in {"completed", "failed", "cancelled"}:
            return state
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_batch_cancel_keeps_finished_segments_and_skips_the_rest():
    model = GatedModel()
    with TestClient(create_app(model_loader=lambda: model)) as client:
        job_id = client.post("/batch", data={"segments": json.dumps(["one", "two", "three"])}).json()["job_id"]
        assert model.started.wait(10)
        cancel = client.post(f"/batch/{job_id}/cancel")
        model.release.set()
        state = wait_status(client, f"/batch/{job_id}")
        download = client.get(f"/batch/{job_id}/download")
    assert cancel.status_code == 200 and cancel.json()["cancel_requested"] is True
    assert state["status"] == "cancelled" and state["completed_segments"] == 1
    assert model.calls == ["one"]
    assert download.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(download.content)).namelist() == ["001.mp3"]


def test_srt_cancel_renders_only_finished_cues():
    model = GatedModel(samples=8000)
    cues = [
        {"index": 1, "start_ms": 0, "end_ms": 1000, "text": "one"},
        {"index": 2, "start_ms": 1500, "end_ms": 2500, "text": "two"},
    ]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        job_id = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav"}).json()["job_id"]
        assert model.started.wait(10)
        client.post(f"/srt/{job_id}/cancel")
        model.release.set()
        state = wait_status(client, f"/srt/{job_id}")
        download = client.get(f"/srt/{job_id}/download")
    assert state["status"] == "cancelled" and state["completed_cues"] == 1
    assert model.calls == ["one"]
    assert download.status_code == 200 and download.content.startswith(b"RIFF")


def test_cancel_unknown_or_finished_job():
    model = GatedModel()
    model.release.set()
    with TestClient(create_app(model_loader=lambda: model)) as client:
        assert client.post("/batch/nope/cancel").status_code == 404
        assert client.post("/srt/nope/cancel").status_code == 404
        job_id = client.post("/batch", data={"segments": json.dumps(["one"])}).json()["job_id"]
        wait_status(client, f"/batch/{job_id}")
        late = client.post(f"/batch/{job_id}/cancel").json()
        state = client.get(f"/batch/{job_id}").json()
    assert late == {"status": "completed", "cancel_requested": False}
    assert state["status"] == "completed"


def test_finished_jobs_do_not_block_new_jobs():
    model = GatedModel(samples=8000)
    model.release.set()
    cue = [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "one"}]
    with TestClient(create_app(model_loader=lambda: model)) as client:
        for round_number in range(12):
            if round_number % 2:
                response = client.post("/srt", data={"cues": json.dumps(cue), "output_format": "wav"})
                path = "/srt"
            else:
                response = client.post("/batch", data={"segments": json.dumps(["one"])})
                path = "/batch"
            assert response.status_code == 202, response.json()
            state = wait_status(client, f"{path}/{response.json()['job_id']}")
            assert state["status"] == "completed"
