"""Опциональный stdio → HTTP → БД сценарий; только выделенный локальный тестовый API."""

import base64
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from remora_mcp.schemas import AgentCourseUpdate, AgentSetUpdate, CourseStructureWrite

ORIGIN = os.environ.get("REMORA_TEST_API_URL")
pytestmark = pytest.mark.skipif(not ORIGIN, reason="Set REMORA_TEST_API_URL for isolated local API")


async def test_publication_lifecycle_over_stdio():
    assert ORIGIN and ORIGIN.startswith(("http://127.0.0.1:", "http://localhost:"))
    async with httpx.AsyncClient(base_url=ORIGIN, trust_env=False) as api:
        suffix = uuid4().hex[:12]
        credentials = {"email": f"audit{suffix}@example.com", "password": uuid4().hex}
        registration = await api.post(
            "/api/v1/auth/register", json={**credentials, "username": f"audit{suffix}"}
        )
        assert registration.status_code == 201
        login = await api.post("/api/v1/auth/login", json=credentials)
        owner = {"Authorization": "Bearer " + login.json()["access_token"]}
        token_ids = []

        async def issue(scopes):
            result = await api.post(
                "/api/v1/users/me/api-tokens",
                headers=owner,
                json={"name": "MCP audit", "scopes": scopes},
            )
            assert result.status_code == 201
            token_ids.append(result.json()["id"])
            return result.json()["token"]

        token = await issue(["materials:read", "materials:write", "courses:publish"])
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "remora_mcp.server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "REMORA_API_URL": ORIGIN, "REMORA_API_TOKEN": token},
        )
        try:
            async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
                init = await session.initialize()
                assert init.instructions

                async def call(name, args, error=None):
                    result = await session.call_tool(name, args)
                    if error:
                        assert result.isError
                        text = "\n".join(c.text for c in result.content if c.type == "text")
                        assert error in text
                        return text
                    assert not result.isError, result
                    if result.structuredContent is not None:
                        return result.structuredContent.get("result", result.structuredContent)
                    if name.startswith("list_"):
                        return [json.loads(c.text) for c in result.content if c.type == "text"]
                    return json.loads(result.content[0].text)

                # Полный путь загрузки проверяется с реальным S3 тестового API.
                svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="blue"/></svg>'
                image_args = {
                    "image": {
                        "data_base64": base64.b64encode(svg).decode(),
                        "mime": "image/svg+xml",
                        "filename": "audit.svg",
                        "alt": "Схема",
                    },
                    "request_key": str(uuid4()),
                }
                image = await call("upload_image", image_args)
                assert image["mime"] == "image/svg+xml"
                assert image["width"] == image["height"] == 32
                assert await call("upload_image", image_args) == image
                assert "media:" + image["id"] in image["markdown_reference"]

                # Наборы открыты сразу; повтор записи не создаёт дубликат.
                material = {
                    "title": "Сложение",
                    "cards": [
                        {"term": "2 + 2", "definition": "4", "wrong_definition_answers": ["3", "5"]}
                    ],
                }
                args = {"material": material, "request_key": str(uuid4())}
                study_set = await call("create_set", args)
                assert study_set["visibility"] == "public"
                assert await call("create_set", args) == study_set
                await call(
                    "create_set", {**args, "material": {**material, "title": "Другое"}}, "409"
                )
                assert (
                    await api.get("/api/v1/sets/public/" + study_set["slug"])
                ).status_code == 200
                current = await call("get_set", {"set_id": study_set["id"]})
                update = AgentSetUpdate.model_validate(
                    {
                        k: v
                        for k, v in current.items()
                        if k in AgentSetUpdate.model_fields and k != "cards"
                    }
                    | {"cards": material["cards"]}
                ).model_dump(mode="json")
                update["cards"][0]["id"] = current["cards"][0]["id"]
                update["title"] = "Сложение чисел"
                edited = await call(
                    "update_set",
                    {"set_id": study_set["id"], "material": update, "request_key": str(uuid4())},
                )
                assert edited["cards"][0]["id"] == current["cards"][0]["id"]
                assert len(await call("list_sets", {})) == 1

                course_input = {
                    "title": "Арифметика",
                    "sections": [
                        {
                            "title": "Основы",
                            "articles": [
                                {
                                    "title": "Сложение",
                                    "body": "Два плюс два равно четырём.\n\n"
                                    + image["markdown_reference"],
                                    "material": material,
                                }
                            ],
                        }
                    ],
                }
                course = await call(
                    "create_course", {"course": course_input, "request_key": str(uuid4())}
                )
                cid = course["id"]
                course_args = {"course_id": cid}
                assert not course["is_published"]
                assert course["sections"][0]["articles"][0]["material"]["visibility"] == "public"
                assert len(await call("list_courses", {})) == 1
                assert (
                    await api.get("/api/v1/courses/public/" + course["slug"])
                ).status_code == 404
                fresh = await call("get_course", course_args)
                update = {**course_input, "revision": fresh["revision"]}
                update["sections"][0]["id"] = fresh["sections"][0]["id"]
                article = update["sections"][0]["articles"][0]
                original = fresh["sections"][0]["articles"][0]
                article["id"] = original["id"]
                article["material"]["cards"][0]["id"] = original["material"]["cards"][0]["id"]
                update["description"] = "Источник: тестовая арифметика"
                await call(
                    "update_course",
                    {
                        **course_args,
                        "course": AgentCourseUpdate.model_validate(update).model_dump(mode="json"),
                        "request_key": str(uuid4()),
                    },
                )
                original = (await call("get_course", course_args))["sections"][0]["articles"][0]
                structure = await call("get_course_structure", course_args)
                sections = [
                    {
                        "id": s["id"],
                        "title": s["title"],
                        "articles": [
                            {k: a[k] for k in ("id", "title", "body", "set_id")}
                            for a in s["articles"]
                        ],
                    }
                    for s in structure["sections"]
                ]
                sections[0]["articles"][0]["body"] = (
                    "## Сложение\n\n2 + 2 = 4.\n\n" + image["markdown_reference"]
                )
                change = CourseStructureWrite(
                    revision=structure["revision"], sections=sections
                ).model_dump(mode="json")
                saved = await call(
                    "update_course_structure",
                    {**course_args, "structure": change, "request_key": str(uuid4())},
                )
                assert (await call("get_course", course_args))["sections"][0]["articles"][0][
                    "material"
                ]["cards"] == original["material"]["cards"]
                await call(
                    "update_course_structure",
                    {**course_args, "structure": change, "request_key": str(uuid4())},
                    "stale_revision",
                )

                publication = {
                    **course_args,
                    "publication": {"tags": ["#Арифметика"]},
                    "request_key": str(uuid4()),
                }
                published = await call("publish_course", publication)
                assert published["is_published"]
                assert await call("publish_course", publication) == published
                assert (
                    await api.get("/api/v1/courses/public/" + course["slug"])
                ).status_code == 200
                change["revision"] = saved["revision"]
                await call(
                    "update_course_structure",
                    {**course_args, "structure": change, "request_key": str(uuid4())},
                    "409",
                )
                copied = await call(
                    "copy_course", {**course_args, "selection": {}, "request_key": str(uuid4())}
                )
                copy_full = await call("get_course", {"course_id": copied["id"]})
                assert not copy_full["is_published"]
                assert image["id"] not in copy_full["sections"][0]["articles"][0]["body"]
                assert "media:" in copy_full["sections"][0]["articles"][0]["body"]
                assert (
                    copy_full["sections"][0]["articles"][0]["material"]["cards"][0]["id"]
                    != original["material"]["cards"][0]["id"]
                )
                await call("unpublish_course", {**course_args, "request_key": str(uuid4())})
                assert (
                    await api.get("/api/v1/courses/public/" + course["slug"])
                ).status_code == 404
                nested = await call("get_set", {"set_id": original["material"]["id"]})
                assert nested["visibility"] == "public"

                structure = await call("get_course_structure", course_args)
                remaining = await call(
                    "delete_course_article",
                    {
                        **course_args,
                        "article_id": original["id"],
                        "deletion": {"revision": structure["revision"], "confirm": True},
                        "request_key": str(uuid4()),
                    },
                )
                assert not remaining["sections"][0]["articles"]
                assert (await call("get_set", {"set_id": original["material"]["id"]}))["cards"]
                await call(
                    "delete_course_section",
                    {
                        **course_args,
                        "section_id": remaining["sections"][0]["id"],
                        "deletion": {"revision": remaining["revision"], "confirm": True},
                        "request_key": str(uuid4()),
                    },
                )
                await call(
                    "publish_course",
                    {**publication, "request_key": str(uuid4())},
                    "Добавьте в курс набор карточек",
                )
                await call(
                    "upload_image",
                    {
                        "image": {"data_base64": "invalid!", "mime": "image/png"},
                        "request_key": str(uuid4()),
                    },
                    "Некорректные данные base64",
                )

                limited = await issue(["materials:read", "materials:write"])
                limited_params = params.model_copy(
                    update={"env": {**params.env, "REMORA_API_TOKEN": limited}}
                )
                async with (
                    stdio_client(limited_params) as (lr, lw),
                    ClientSession(lr, lw) as restricted,
                ):
                    await restricted.initialize()
                    denied = await restricted.call_tool(
                        "publish_course",
                        {**publication, "course_id": copied["id"], "request_key": str(uuid4())},
                    )
                    assert denied.isError
                    assert "courses:publish" in denied.content[0].text
        finally:
            for token_id in token_ids:
                assert (
                    await api.delete("/api/v1/users/me/api-tokens/" + token_id, headers=owner)
                ).status_code == 204
