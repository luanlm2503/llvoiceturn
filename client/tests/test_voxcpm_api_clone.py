import httpx
import pytest

from llvoice.config import API_BASE_URL
from llvoice.voxcpm_api import VoxCPMApiError, VoxCPMClient


class Response:
    def __init__(self, content=b"RIFFfake", content_type="audio/wav", status=200):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status
        self.is_success = status < 400

    def json(self):
        return {"detail": {"code": "invalid_audio", "message": "Choose WAV or MP3."}}


def test_clone_posts_multipart_to_loopback_with_text_and_style(monkeypatch, tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake mp3 data")
    captured = {}

    def request(self, method, path, **kwargs):
        captured.update(base_url=self.base_url, method=method, path=path, **kwargs)
        return Response()

    monkeypatch.setattr(httpx.Client, "request", request)
    result = VoxCPMClient().clone("hello", audio, "warm voice")
    assert result == b"RIFFfake"
    assert captured["base_url"] == "http://127.0.0.1:7861"
    assert captured["base_url"] != API_BASE_URL
    assert (captured["method"], captured["path"]) == ("POST", "/clone")
    assert captured["data"] == {"text": "hello", "style": "warm voice"}
    assert captured["files"]["reference_audio"][0] == "voice.mp3"
    assert captured["files"]["reference_audio"][1].closed


def test_clone_rejects_non_wav_response(monkeypatch, tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFFsample")
    monkeypatch.setattr(httpx.Client, "request", lambda *args, **kwargs: Response(content=b"{}", content_type="application/json"))
    with pytest.raises(VoxCPMApiError, match="valid WAV"):
        VoxCPMClient().clone("hello", audio)


def test_clone_unavailable_sidecar_has_start_instruction(monkeypatch, tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFFsample")
    def request(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx.Client, "request", request)
    with pytest.raises(VoxCPMApiError, match="Start the local VoxCPM2 service"):
        VoxCPMClient().clone("hello", audio)


def test_clone_keeps_plain_generate_json_contract(monkeypatch):
    captured = {}
    def request(self, method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return Response()
    monkeypatch.setattr(httpx.Client, "request", request)
    assert VoxCPMClient().generate("plain", "style") == b"RIFFfake"
    assert captured["json"] == {"text": "plain", "style": "style"}
    assert "files" not in captured
