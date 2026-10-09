import json
from pathlib import Path

import httpx
import pytest

from llvoice.srt import SrtCue
from llvoice.voxcpm_api import VoxCPMApiError, VoxCPMClient


class Response:
    def __init__(self, payload=None, content=b"", content_type="application/json", status=200):
        self._payload = payload
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status
        self.is_success = status < 400
    def json(self): return self._payload


def test_start_srt_sends_loopback_cues_format_style_and_reference(tmp_path, monkeypatch):
    path = tmp_path / "voice.wav"; path.write_bytes(b"wav")
    captured = {}
    def request(self, method, url, **kwargs):
        captured.update(base_url=self.base_url, url=url, **kwargs)
        return Response({"job_id": "123e4567-e89b-12d3-a456-426614174000"}, status=202)
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    job_id = client.start_srt([SrtCue(4, 100, 1000, "Xin chào")], "ấm", "mp3", path)
    assert job_id == "123e4567-e89b-12d3-a456-426614174000"
    assert captured["base_url"] == "http://127.0.0.1:7861" and captured["url"] == "/srt"
    assert json.loads(captured["data"]["cues"]) == [{"index": 4, "start_ms": 100, "end_ms": 1000, "text": "Xin chào"}]
    assert captured["data"]["output_format"] == "mp3" and captured["data"]["style"] == "ấm"
    assert captured["files"]["reference_audio"][0] == "voice.wav"


def test_status_and_format_specific_download(monkeypatch):
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    def request(self, method, url, **kwargs):
        if url.endswith("/download"):
            return Response(content=b"ID3" + b"x" * 200, content_type="audio/mpeg")
        return Response({"status": "running", "completed_cues": 2, "total_cues": 3})
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    assert client.srt_status(job_id)["completed_cues"] == 2
    assert client.download_srt(job_id, "mp3").startswith(b"ID3")


def test_srt_client_rejects_invalid_job_ids_and_wrong_result_format(monkeypatch):
    client = VoxCPMClient()
    with pytest.raises(VoxCPMApiError, match="job"):
        client.srt_status("../bad")
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: Response(content=b"RIFF" + b"x" * 20, content_type="audio/wav"))
    with pytest.raises(VoxCPMApiError, match="MP3"):
        client.download_srt("123e4567-e89b-12d3-a456-426614174000", "mp3")
