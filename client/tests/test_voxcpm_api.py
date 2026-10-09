import httpx
import pytest

from llvoice.config import API_BASE_URL
from llvoice.voxcpm_api import VoxCPMApiError, VoxCPMClient


class MockResponse:
    def __init__(self, status_code=200, content=b"RIFFfake", headers=None, json_data=None):
        self.status_code = status_code
        self.is_success = status_code < 400
        self.content = content
        self.headers = headers or {"content-type": "audio/wav"}
        self._json_data = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=httpx.Request("GET", "http://local"), response=httpx.Response(self.status_code))

    def json(self):
        return self._json_data


def test_health_uses_local_sidecar_url(monkeypatch):
    requested = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: type("Http", (), {
        "request": lambda self, method, path, **options: requested.append((kwargs["base_url"], method, path)) or MockResponse(json_data={"status": "ready"}),
        "close": lambda self: None,
    })())
    client = VoxCPMClient()
    assert client.health() == {"status": "ready"}
    assert requested == [("http://127.0.0.1:7861", "GET", "/health")]
    assert requested[0][0] != API_BASE_URL


def test_generate_posts_text_and_style_and_returns_wav(monkeypatch):
    requested = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: type("Http", (), {
        "request": lambda self, method, path, **options: requested.append((method, path, options["json"])) or MockResponse(),
        "close": lambda self: None,
    })())
    client = VoxCPMClient()
    assert client.generate("Xin chào", "giọng ấm") == b"RIFFfake"
    assert requested == [("POST", "/generate", {"text": "Xin chào", "style": "giọng ấm"})]


def test_unavailable_sidecar_raises_actionable_error(monkeypatch):
    def fail(**kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: type("Http", (), {"request": lambda *args, **options: fail(), "close": lambda self: None})())
    with pytest.raises(VoxCPMApiError, match="Start the local VoxCPM2 service"):
        VoxCPMClient().health()


def test_non_wav_response_is_rejected(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: type("Http", (), {
        "request": lambda *args, **options: MockResponse(headers={"content-type": "application/json"}),
        "close": lambda self: None,
    })())
    with pytest.raises(VoxCPMApiError, match="valid WAV"):
        VoxCPMClient().generate("hello")


def test_timeout_is_reported_as_sidecar_error(monkeypatch):
    def fail(**kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: type("Http", (), {"request": lambda *args, **options: fail(), "close": lambda self: None})())
    with pytest.raises(VoxCPMApiError, match="timed out"):
        VoxCPMClient().generate("hello")
