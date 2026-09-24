import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Header, Request
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])


async def test_stdio_handshake_and_schemas() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "remora_mcp.server"],
        cwd=PACKAGE_ROOT,
        env={**os.environ, "REMORA_API_TOKEN": "rmr_" + "x" * 64},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert len(tools) == 16
        create = next(t for t in tools if t.name == "create_course")
        assert "course" in create.inputSchema["properties"]
        upload = next(t for t in tools if t.name == "upload_image")
        assert "image" in upload.inputSchema["properties"]


def _stub_agent_api() -> FastAPI:
    """Мини-заглушка агентского API: проверяет заголовки и эхом отдаёт тело запроса."""
    app = FastAPI()

    @app.post("/api/v1/agent/sets")
    async def create_set(
        request: Request,
        authorization: str = Header(...),
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
    ) -> dict:
        assert authorization == "Bearer rmr_" + "x" * 64
        assert idempotency_key == "stub-key"
        body = await request.json()
        return {"id": "11111111-1111-1111-1111-111111111111", "revision": "r" * 64, **body}

    @app.get("/api/v1/agent/sets/{set_id}")
    async def fail(set_id: str) -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409)

    return app


@pytest.mark.parametrize("stdio_encoding", ["utf-8", "cp1251"])
async def test_create_set_round_trip_and_error_mapping(stdio_encoding: str) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(_stub_agent_api(), lifespan="off", log_level="error")
        )
        running = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "remora_mcp.server"],
                cwd=PACKAGE_ROOT,
                env={
                    **os.environ,
                    "REMORA_API_TOKEN": "rmr_" + "x" * 64,
                    "REMORA_API_URL": f"http://127.0.0.1:{port}",
                    "PYTHONIOENCODING": stdio_encoding,
                },
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                material = {
                    "title": "0.5 Git: установка и настройка — ё, 中文, 😀",
                    "description": "Проверка кириллицы: Юпитер, Python и русский текст.",
                    "cards": [
                        {
                            "term": "pip install <package>",
                            "definition": "Установить пакет",
                            "hint": "Команда оболочки",
                            "alt_answers": ["Установка пакета"],
                            "content_type": "code",
                            "code_language": "bash",
                        }
                    ],
                }
                result = await session.call_tool(
                    "create_set",
                    {"material": material, "request_key": "stub-key"},
                )
                assert not result.isError
                data = result.structuredContent or json.loads(result.content[0].text)
                assert data["title"] == material["title"]
                assert data["description"] == material["description"]
                for key, value in material["cards"][0].items():
                    assert data["cards"][0][key] == value

                error = await session.call_tool(
                    "get_set", {"set_id": "22222222-2222-2222-2222-222222222222"}
                )
                assert error.isError
        finally:
            server.should_exit = True
            await running
