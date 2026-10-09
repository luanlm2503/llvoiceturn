import json

import numpy as np

from tests.test_batch_api import FakeModel, ready_client, run_until_done, start, wav_bytes


class PromptModel(FakeModel):
    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None, prompt_wav_path=None, prompt_text=None):
        self.calls.append({"text": text, "reference": reference_wav_path, "prompt_wav": prompt_wav_path, "prompt_text": prompt_text})
        return np.full(8000, 0.05, dtype=np.float32)


def reference():
    return ("mau.wav", wav_bytes(), "audio/wav")


def wait_srt(client, job_id):
    for _ in range(500):
        state = client.get(f"/srt/{job_id}").json()
        if state["status"] in {"completed", "failed"}:
            return state
    raise AssertionError("srt job did not finish")


def test_batch_and_srt_use_continuation_mode_with_prompt_text():
    model = PromptModel()
    cues = [{"index": 1, "start_ms": 0, "end_ms": 2000, "text": "ba"}]
    with ready_client(model) as client:
        job = start(client, ["một"], style="giọng ấm", prompt_text="  Xin chào các bạn  ", reference_audio=reference())
        assert run_until_done(client, job)["status"] == "completed"
        response = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav", "prompt_text": "Xin chào"}, files={"reference_audio": reference()})
        assert response.status_code == 202, response.text
        assert wait_srt(client, response.json()["job_id"])["status"] == "completed"
        plain = start(client, ["hai"], style="giọng ấm", prompt_text="   ", reference_audio=reference())
        assert run_until_done(client, plain)["status"] == "completed"
    first, srt_call, plain_call = model.calls
    # Mô tả giọng bị bỏ: ở chế độ nối tiếp nó sẽ bị đọc lẫn sau lời thoại mẫu.
    assert first["text"] == "một" and first["prompt_text"] == "Xin chào các bạn"
    assert first["prompt_wav"] == first["reference"] and first["reference"]
    assert srt_call["text"] == "ba" and srt_call["prompt_text"] == "Xin chào"
    assert plain_call == {"text": "(giọng ấm)hai", "reference": plain_call["reference"], "prompt_wav": None, "prompt_text": None}


def test_prompt_text_without_reference_is_rejected():
    model = PromptModel()
    cues = [{"index": 1, "start_ms": 0, "end_ms": 2000, "text": "ba"}]
    with ready_client(model) as client:
        batch = client.post("/batch", data={"segments": json.dumps(["một"]), "prompt_text": "Xin chào"})
        srt = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav", "prompt_text": "Xin chào"})
        too_long = client.post("/batch", data={"segments": json.dumps(["một"]), "prompt_text": "a" * 1001}, files={"reference_audio": reference()})
    assert batch.status_code == srt.status_code == too_long.status_code == 422
    assert batch.json()["detail"]["code"] == "invalid_prompt_text"
    assert model.calls == []
