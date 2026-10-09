import io
import json
import zipfile

import httpx
import pytest

from llvoice.voxcpm_api import VoxCPMApiError, VoxCPMClient


class Response:
    def __init__(self, content=b"", content_type="application/json", status=200, payload=None):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status
        self.is_success = status < 400
        self.payload = payload or {}

    def json(self):
        return self.payload


def test_start_batch_posts_multipart_to_loopback(monkeypatch, tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"wav-data")
    captured = {}
    def request(self, method, path, **kwargs):
        captured.update(base_url=self.base_url, method=method, path=path, **kwargs)
        return Response(payload={"job_id": "123e4567-e89b-12d3-a456-426614174000"}, status=202)
    monkeypatch.setattr(httpx.Client, "request", request)
    job_id = VoxCPMClient().start_batch(["one", "two"], "warm", audio)
    assert job_id == "123e4567-e89b-12d3-a456-426614174000"
    assert captured["base_url"] == "http://127.0.0.1:7861"
    assert captured["path"] == "/batch"
    assert json.loads(captured["data"]["segments"]) == ["one", "two"]
    assert captured["data"]["style"] == "warm"
    assert captured["files"]["reference_audio"][0] == "voice.wav"


def test_batch_status_and_zip_download(monkeypatch):
    calls = []
    def request(self, method, path, **kwargs):
        calls.append((method, path))
        if path.endswith("/download"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("001.mp3", b"mp3")
            return Response(stream.getvalue(), "application/zip")
        return Response(payload={"status": "running", "completed_segments": 1, "total_segments": 2})
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    assert client.batch_status("123e4567-e89b-12d3-a456-426614174000")["completed_segments"] == 1
    assert client.download_batch("123e4567-e89b-12d3-a456-426614174000").startswith(b"PK")
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    assert calls == [("GET", f"/batch/{job_id}"), ("GET", f"/batch/{job_id}/download")]


def test_batch_rejects_invalid_job_id_and_non_zip(monkeypatch):
    with pytest.raises(VoxCPMApiError, match="job"):
        VoxCPMClient().batch_status("../escape")
    monkeypatch.setattr(httpx.Client, "request", lambda *args, **kwargs: Response(b"oops", "application/json"))
    with pytest.raises(VoxCPMApiError, match="ZIP"):
        VoxCPMClient().download_batch("123e4567-e89b-12d3-a456-426614174000")

def test_voice_settings_sent_only_when_changed(monkeypatch):
    from llvoice.voxcpm_api import VoiceSettings

    sent = []
    def request(self, method, path, **kwargs):
        sent.append(kwargs["data"])
        return Response(payload={"job_id": "123e4567-e89b-12d3-a456-426614174000"}, status=202)
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    client.start_batch(["one"], voice=VoiceSettings())
    client.start_batch(["one"], voice=VoiceSettings(stability=80, similarity=75, style_exaggeration=30, speaker_boost=True))
    voice_fields = {"stability", "similarity", "style_exaggeration", "speaker_boost"}
    assert not voice_fields & set(sent[0])
    assert {key: sent[1][key] for key in voice_fields & set(sent[1])} == {"stability": "80", "style_exaggeration": "30", "speaker_boost": "true"}
    with pytest.raises(VoxCPMApiError):
        client.start_batch(["one"], voice=VoiceSettings(similarity=120))

def test_prompt_text_sent_with_reference_only(monkeypatch, tmp_path):
    from llvoice.srt import SrtCue

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"wav-data")
    sent = []
    def request(self, method, path, **kwargs):
        sent.append(kwargs["data"])
        return Response(payload={"job_id": "123e4567-e89b-12d3-a456-426614174000"}, status=202)
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    client.start_batch(["one"], None, audio, prompt_text="  Xin chào  ")
    client.start_srt([SrtCue(1, 0, 1000, "one")], None, "mp3", audio, prompt_text="Xin chào")
    client.start_batch(["one"], None, audio, prompt_text="   ")
    assert sent[0]["prompt_text"] == "Xin chào" and sent[1]["prompt_text"] == "Xin chào"
    assert "prompt_text" not in sent[2]
    with pytest.raises(VoxCPMApiError, match="reference"):
        client.start_batch(["one"], prompt_text="Xin chào")
    with pytest.raises(VoxCPMApiError, match="1000"):
        client.start_batch(["one"], None, audio, prompt_text="a" * 1001)


def test_cancel_posts_to_job_and_cancelled_status_is_valid(monkeypatch):
    calls = []
    def request(self, method, path, **kwargs):
        calls.append((method, path))
        return Response(payload={"status": "cancelled", "completed_segments": 1, "total_segments": 3})
    monkeypatch.setattr(httpx.Client, "request", request)
    client = VoxCPMClient()
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    client.cancel_batch(job_id)
    client.cancel_srt(job_id)
    assert client.batch_status(job_id)["status"] == "cancelled"
    assert client.srt_status(job_id)["status"] == "cancelled"
    assert calls[:2] == [("POST", f"/batch/{job_id}/cancel"), ("POST", f"/srt/{job_id}/cancel")]
    with pytest.raises(VoxCPMApiError, match="job"):
        client.cancel_batch("../escape")
