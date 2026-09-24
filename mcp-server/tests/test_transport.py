import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from remora_mcp import server
from remora_mcp.schemas import CardWrite, CourseCopyRequest, CoursePublication, CourseStructureWrite


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    monkeypatch.setenv("REMORA_API_URL", "https://remora.test")
    monkeypatch.setenv("REMORA_API_TOKEN", "rmr_" + "x" * 64)
    requests = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(server.httpx, "AsyncClient", client)
    return requests


ID = str(uuid4())
KEY = str(uuid4())
SET = {"title": "Набор", "cards": [{"term": "2 + 2", "definition": "4"}]}
COURSE = {"title": "Курс", "sections": []}
CASES = [
    ("list_sets", {}, "GET", "/sets"),
    ("get_set", {"set_id": ID}, "GET", f"/sets/{ID}"),
    ("create_set", {"material": SET}, "POST", "/sets"),
    ("update_set", {"set_id": ID, "material": {**SET, "revision": "a" * 64}}, "PUT", f"/sets/{ID}"),
    ("list_courses", {}, "GET", "/courses"),
    ("get_course", {"course_id": ID}, "GET", f"/courses/{ID}"),
    ("create_course", {"course": COURSE}, "POST", "/courses"),
    (
        "update_course",
        {"course_id": ID, "course": {**COURSE, "revision": "a" * 64}},
        "PUT",
        f"/courses/{ID}",
    ),
    (
        "publish_course",
        {"course_id": ID, "publication": {"tags": ["#Алгебра"]}},
        "POST",
        f"/courses/{ID}/publish",
    ),
    ("unpublish_course", {"course_id": ID}, "POST", f"/courses/{ID}/unpublish"),
    ("get_course_structure", {"course_id": ID}, "GET", f"/courses/{ID}/structure"),
    (
        "update_course_structure",
        {"course_id": ID, "structure": {"revision": "a" * 64, "sections": []}},
        "PUT",
        f"/courses/{ID}/structure",
    ),
    ("copy_course", {"course_id": ID, "selection": {}}, "POST", f"/courses/{ID}/copy"),
    (
        "delete_course_article",
        {"course_id": ID, "article_id": ID, "deletion": {"revision": "a" * 64, "confirm": True}},
        "POST",
        f"/courses/{ID}/articles/{ID}/delete",
    ),
    (
        "delete_course_section",
        {"course_id": ID, "section_id": ID, "deletion": {"revision": "a" * 64, "confirm": True}},
        "POST",
        f"/courses/{ID}/sections/{ID}/delete",
    ),
    ("upload_image", {"image": {"data_base64": "YWJj", "mime": "image/png"}}, "POST", "/media"),
]


@pytest.mark.parametrize("name,arguments,method,path", CASES)
async def test_all_tools_route_and_serialize(transport, name, arguments, method, path):
    args = dict(arguments)
    if method != "GET":
        args["request_key"] = KEY
    await server.mcp.call_tool(name, args)
    assert len(transport) == 1
    request = transport[0]
    assert request.method == method
    assert request.url.path == "/api/v1/agent" + path
    assert request.headers["Authorization"] == "Bearer rmr_" + "x" * 64
    if method != "GET":
        assert request.headers["Idempotency-Key"] == KEY
    if name == "publish_course":
        assert json.loads(request.content) == {"tags": ["алгебра"]}
    if name.startswith("list_"):
        assert dict(request.url.params) == {"offset": "0", "limit": "20"}
    if name == "unpublish_course":
        assert request.content == b""


@pytest.mark.parametrize("arguments", [{"offset": -1}, {"limit": 0}, {"limit": 101}])
async def test_invalid_pagination_does_not_reach_network(transport, arguments):
    with pytest.raises(ToolError):
        await server.mcp.call_tool("list_sets", arguments)
    assert transport == []


@pytest.mark.parametrize("key", ["", "x" * 129, "bad\nheader", "ключ"])
async def test_invalid_key_does_not_reach_network(transport, key):
    with pytest.raises(ToolError):
        await server.mcp.call_tool("create_set", {"material": SET, "request_key": key})
    assert transport == []


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (409, "CONFLICT"),
        (422, "VALIDATION_ERROR"),
        (429, "RATE_LIMITED"),
        (402, "LIMIT_EXCEEDED"),
        (500, "INTERNAL_ERROR"),
    ],
)
def test_actionable_error_is_preserved_without_secret_inputs(status, code):
    token = "rmr_" + "x" * 64
    response = httpx.Response(
        status,
        json={
            "code": code,
            "message": "Каждая статья должна содержать карточки " + token,
            "details": {
                "required_scope": "courses:publish",
                "reason": "stale_revision",
                "limit_key": "media_storage",
                "secret": token,
                "errors": [
                    {
                        "loc": ["body", "tags", 0],
                        "type": "value_error",
                        "input": "sensitive source",
                        "ctx": {"secret": token},
                    }
                ],
            },
        },
        headers={"Retry-After": "60"},
    )
    message = server.api_error(response, token)
    assert code in message and "Каждая статья" in message
    assert "courses:publish" in message and "stale_revision" in message
    assert token not in message and "sensitive source" not in message and "ctx" not in message
    assert "retry_after_seconds" in message


@pytest.mark.parametrize("status", [301, 302, 307, 308, 502, 503])
def test_proxy_response_is_not_reflected(status):
    assert "proxy-secret" not in server.api_error(
        httpx.Response(status, text="proxy-secret"), "token"
    )
    assert "proxy-secret" not in server.api_error(
        httpx.Response(status, json={"message": "proxy-secret"}), "token"
    )


@pytest.mark.parametrize(
    "model,body",
    [
        (CardWrite, {"term": "a", "definition": "b", "audio": "ignored-before"}),
        (CourseCopyRequest, {"article": "wrong-field"}),
        (CoursePublication, {"visibility": "public"}),
        (
            CourseStructureWrite,
            {
                "revision": "a" * 64,
                "sections": [{"title": "A", "articles": [{"title": "B", "material": SET}]}],
            },
        ),
    ],
)
def test_unknown_fields_are_not_silently_lost(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "redirect"])
async def test_request_failure_does_not_retry_or_leak(transport, monkeypatch, failure):
    # Фикстура уже заменила публичный конструктор HTTP-клиента.
    from httpx._client import AsyncClient

    real_client = AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("rmr_" + "x" * 64, request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.test/private"})
        return httpx.Response(200, text="proxy-secret")

    monkeypatch.setattr(
        server.httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ValueError) as error:
        await server.request("POST", "/sets", key=KEY)
    assert len(calls) == 1
    assert "rmr_" not in str(error.value) and "proxy-secret" not in str(error.value)
    if failure != "redirect":
        assert "request_key" in str(error.value)


async def test_nested_placeholder_reports_location_without_http(transport):
    course = {
        "title": "Курс",
        "sections": [
            {
                "title": "Раздел",
                "articles": [
                    {
                        "title": "Установка",
                        "material": {
                            "title": "Команды",
                            "cards": [
                                {
                                    "term": "pip install <package>",
                                    "definition": "Установка пакета",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    with pytest.raises(ToolError, match="code_language") as error:
        await server.mcp.call_tool("create_course", {"course": course, "request_key": KEY})
    assert "sections.0.articles.0.material.cards.0" in str(error.value)
    assert not transport
