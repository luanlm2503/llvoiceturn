import json

import numpy as np
import pytest

from tests.test_batch_api import FakeModel, ready_client, run_until_done, start
from voice_settings import VoiceSettings, VoiceSettingsError


class RecordingModel(FakeModel):
    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None):
        self.kwargs = getattr(self, "kwargs", [])
        self.kwargs.append({"text": text, "cfg_value": cfg_value, "inference_timesteps": inference_timesteps})
        super().generate(text=text, reference_wav_path=reference_wav_path)
        return np.full(160, 0.1, dtype=np.float32)


def test_defaults_match_original_generation():
    voice = VoiceSettings()
    assert voice.model_kwargs() == {"cfg_value": 2.0, "inference_timesteps": 10}
    assert voice.full_text("Xin chào", None) == "Xin chào"
    assert voice.full_text("Xin chào", " giọng ấm ") == "(giọng ấm)Xin chào"
    wav = np.full(10, 0.1, dtype=np.float32)
    assert voice.boost(wav) is wav


def test_mapping_endpoints_and_style_and_boost():
    assert VoiceSettings(stability=0, similarity=0).model_kwargs() == {"cfg_value": 1.0, "inference_timesteps": 4}
    assert VoiceSettings(stability=100, similarity=100).model_kwargs() == {"cfg_value": 3.0, "inference_timesteps": 20}
    styled = VoiceSettings(style_exaggeration=80)
    assert styled.full_text("A", "giọng nữ") == "(giọng nữ, very expressive, strong emotion)A"
    assert styled.full_text("A", "") == "(very expressive, strong emotion)A"
    boosted = VoiceSettings(speaker_boost=True).boost(np.array([0.1, -0.2], dtype=np.float32))
    assert np.isclose(np.max(np.abs(boosted)), 0.89)
    assert VoiceSettings(speaker_boost=True).boost(np.zeros(4, dtype=np.float32)).max() == 0


@pytest.mark.parametrize("field", ["stability", "similarity", "style_exaggeration"])
def test_rejects_out_of_range(field):
    with pytest.raises(VoiceSettingsError):
        VoiceSettings(**{field: 101})


def test_batch_and_srt_use_voice_settings():
    model = RecordingModel()
    with ready_client(model) as client:
        job_id = start(client, ["one"], stability="100", similarity="0", style_exaggeration="50", speaker_boost="true", style="ấm")
        assert run_until_done(client, job_id)["status"] == "completed"
        default_job = start(client, ["two"])
        assert run_until_done(client, default_job)["status"] == "completed"
        cues = [{"index": 1, "start_ms": 0, "end_ms": 2000, "text": "three"}]
        response = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav", "stability": "0"})
        assert response.status_code == 202, response.text
        srt_id = response.json()["job_id"]
        for _ in range(500):
            if client.get(f"/srt/{srt_id}").json()["status"] in {"completed", "failed"}:
                break
    assert model.kwargs[0] == {"text": "(ấm, expressive, lively intonation)one", "cfg_value": 1.0, "inference_timesteps": 20}
    assert model.kwargs[1] == {"text": "two", "cfg_value": 2.0, "inference_timesteps": 10}
    assert model.kwargs[2]["inference_timesteps"] == 4


@pytest.mark.parametrize("data", [{"stability": "-1"}, {"similarity": "150"}, {"style_exaggeration": "x"}])
def test_batch_rejects_invalid_voice_settings(data):
    model = FakeModel()
    with ready_client(model) as client:
        response = client.post("/batch", data={"segments": '["one"]', **data})
    assert response.status_code == 422
    assert model.calls == []
