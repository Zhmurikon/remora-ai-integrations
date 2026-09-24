import pytest

from remora_mcp import server


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?token=x",
        "https://example.com#frag",
    ],
)
def test_rejects_unsafe_origin(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMORA_API_URL", origin)
    monkeypatch.setenv("REMORA_API_TOKEN", "rmr_" + "x" * 64)
    with pytest.raises(ValueError):
        server.configuration()


@pytest.mark.parametrize("origin", ["http://localhost:8000", "https://remora.com.ru"])
def test_accepts_safe_origin(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMORA_API_URL", origin)
    monkeypatch.setenv("REMORA_API_TOKEN", "rmr_" + "x" * 64)
    resolved_origin, token = server.configuration()
    assert resolved_origin == origin
    assert token == "rmr_" + "x" * 64


@pytest.mark.parametrize("token", ["", "wrong_prefix" + "x" * 60, "rmr_short"])
def test_rejects_bad_token(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMORA_API_URL", "http://localhost:8000")
    monkeypatch.setenv("REMORA_API_TOKEN", token)
    with pytest.raises(ValueError):
        server.configuration()


@pytest.mark.parametrize(
    "token", ["rmr_" + "x" * 63 + "\n", "rmr_" + "я" * 64, "rmr_" + "x" * 63 + " "]
)
def test_token_must_be_urlsafe(token, monkeypatch):
    monkeypatch.setenv("REMORA_API_URL", "https://remora.com.ru")
    monkeypatch.setenv("REMORA_API_TOKEN", token)
    with pytest.raises(ValueError):
        server.configuration()


@pytest.mark.parametrize("origin", ["https://remora.com.ru:bad", "https://remora.com.ru:99999"])
def test_origin_port_is_validated(origin, monkeypatch):
    monkeypatch.setenv("REMORA_API_URL", origin)
    monkeypatch.setenv("REMORA_API_TOKEN", "rmr_" + "x" * 64)
    with pytest.raises(ValueError):
        server.configuration()
