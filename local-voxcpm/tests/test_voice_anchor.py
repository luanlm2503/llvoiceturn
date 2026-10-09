import json

import numpy as np
import soundfile as sf

from tests.test_batch_api import FakeModel, ready_client, run_until_done, start, wav_bytes


class AnchorModel(FakeModel):
    def generate(self, *, text, cfg_value=2.0, inference_timesteps=10, reference_wav_path=None):
        anchor = None
        if reference_wav_path is not None:
            anchor, _ = sf.read(reference_wav_path, dtype="float32")
        self.calls.append((text, reference_wav_path, anchor))
        return np.full(8000, 0.1 * len(self.calls), dtype=np.float32)


def test_without_reference_later_segments_reuse_first_voice():
    model = AnchorModel()
    cues = [{"index": 1, "start_ms": 0, "end_ms": 1000, "text": "c1"}, {"index": 2, "start_ms": 1000, "end_ms": 2000, "text": "c2"}]
    with ready_client(model) as client:
        job = start(client, ["một", "hai", "ba"], style="giọng ấm")
        assert run_until_done(client, job)["status"] == "completed"
        response = client.post("/srt", data={"cues": json.dumps(cues), "output_format": "wav"})
        assert response.status_code == 202, response.text
        srt_id = response.json()["job_id"]
        for _ in range(500):
            if client.get(f"/srt/{srt_id}").json()["status"] in {"completed", "failed"}:
                break
    (first, first_ref, _), second, third, (_, srt_first_ref, _), srt_second = model.calls
    assert first == "(giọng ấm)một" and first_ref is None
    # Đoạn sau lấy chính âm thanh đoạn đầu (trước khi chỉnh tốc độ/âm lượng) làm giọng mẫu.
    for _, ref, anchor in (second, third):
        assert ref is not None and np.allclose(anchor, 0.1, atol=1e-3)
    assert second[1] == third[1]
    assert srt_first_ref is None and np.allclose(srt_second[2], 0.4, atol=1e-3)
    assert second[1] != srt_second[1]  # mỗi job có giọng mẫu riêng


def test_user_reference_is_never_replaced():
    model = AnchorModel()
    with ready_client(model) as client:
        job = start(client, ["một", "hai"], reference_audio=("voice.wav", wav_bytes(), "audio/wav"))
        assert run_until_done(client, job)["status"] == "completed"
    assert model.calls[0][1] == model.calls[1][1]
    assert np.allclose(model.calls[1][2], 0.0)  # vẫn là audio mẫu (im lặng) của người dùng
