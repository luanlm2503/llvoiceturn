import numpy as np
import pytest

import app as app_module
from srt_audio import SrtAudioError, apply_speed
from tests.test_batch_api import FakeModel, ready_client, run_until_done, start


def tone(seconds=1.0, sample_rate=16000):
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_apply_speed_changes_length_and_keeps_default():
    samples = tone()
    assert apply_speed(samples, 1.0) is samples
    assert abs(len(apply_speed(samples, 2.0)) - len(samples) / 2) < 400
    assert abs(len(apply_speed(samples, 0.5)) - len(samples) * 2) < 400
    with pytest.raises(SrtAudioError):
        apply_speed(samples, 3.0)


def test_batch_passes_speed_to_every_segment(monkeypatch):
    used = []
    original = app_module.apply_speed
    monkeypatch.setattr(app_module, "apply_speed", lambda wav, speed: used.append(speed) or original(wav, speed))
    with ready_client(FakeModel()) as client:
        job_id = start(client, ["one", "two"], speed="1.5")
        assert run_until_done(client, job_id)["status"] == "completed"
        default_job = start(client, ["three"])
        assert run_until_done(client, default_job)["status"] == "completed"
    assert used == [1.5, 1.5, 1.0]


@pytest.mark.parametrize("speed", ["0.1", "5", "abc"])
def test_batch_rejects_invalid_speed(speed):
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/batch", data={"segments": '["one"]', "speed": speed})
    assert response.status_code == 422
    assert model.calls == []
