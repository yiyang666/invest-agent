"""Minimal reviewed Streamable HTTP client for Guchacha collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from typing import Mapping
from zoneinfo import ZoneInfo

import requests


PROTOCOL_VERSION = "2025-06-18"
PROVIDER_ID = "guchacha_mcp"
SOURCE_DOMAIN = "guchacha.com"


@dataclass(frozen=True)
class RawToolResponse:
    tool_name: str
    arguments: Mapping[str, object]
    fetched_at: datetime
    payload: bytes
    content_type: str


def decode_jsonrpc(payload: bytes, content_type: str) -> dict[str, object]:
    """Decode JSON or an SSE-wrapped JSON-RPC response."""

    text = payload.decode("utf-8")
    if "text/event-stream" in content_type:
        messages: list[dict[str, object]] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            value = json.loads(line[5:].strip())
            if isinstance(value, dict):
                messages.append(value)
        if not messages:
            raise ValueError("MCP SSE response contains no JSON-RPC data event")
        return messages[-1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("MCP response must be one JSON object")
    return value


def extract_tool_result(message: Mapping[str, object]) -> object:
    """Return the JSON value carried in MCP text content and reject app errors."""

    if "error" in message:
        raise ValueError(f"MCP JSON-RPC error: {message['error']}")
    result = message.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("MCP tool response has no result object")
    if result.get("isError") is True:
        raise ValueError("MCP tool returned application-level isError")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise ValueError("MCP tool response has no content")
    first = content[0]
    if not isinstance(first, Mapping) or first.get("type") != "text":
        raise ValueError("MCP tool response first content item must be text")
    text = first.get("text")
    if not isinstance(text, str):
        raise ValueError("MCP tool text content is invalid")
    value = json.loads(text)
    if isinstance(value, Mapping) and value.get("isError") is True:
        raise ValueError("MCP tool payload returned application-level isError")
    return value


class GuchachaMcpClient:
    """Call only prevalidated tools; credentials never enter results or logs."""

    def __init__(
        self,
        *,
        endpoint: str = "https://guchacha.com/mcp",
        token_environment_variable: str = "GUCHACHA_MCP_TOKEN",
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if endpoint != "https://guchacha.com/mcp":
            raise ValueError("Guchacha endpoint must use the reviewed HTTPS origin")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.token_environment_variable = token_environment_variable
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._request_id = 0
        self._session_id: str | None = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self.token_environment_variable)
        if not token:
            raise ValueError(
                f"Required credential environment variable is missing: "
                f"{self.token_environment_variable}"
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, message: Mapping[str, object]):
        response = self.session.post(
            self.endpoint,
            headers=self._headers(),
            json=message,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        return response

    def initialize(self) -> dict[str, object]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "invest-agent-market-data",
                        "version": "1.0.0",
                    },
                },
            }
        )
        message = decode_jsonrpc(
            response.content,
            response.headers.get("Content-Type", "application/json"),
        )
        if "error" in message or not isinstance(message.get("result"), Mapping):
            raise ValueError("Guchacha MCP initialize failed")
        notification = self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        if notification.status_code not in {200, 202, 204}:
            raise ValueError("Guchacha MCP initialized notification failed")
        return message

    def call_tool(self, tool_name: str, arguments: Mapping[str, object]) -> RawToolResponse:
        if self._request_id == 0:
            self.initialize()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": dict(arguments)},
            }
        )
        return RawToolResponse(
            tool_name=tool_name,
            arguments=dict(arguments),
            fetched_at=datetime.now(ZoneInfo("Asia/Shanghai")),
            payload=response.content,
            content_type=response.headers.get("Content-Type", "application/json"),
        )

    def list_tools(self) -> RawToolResponse:
        if self._request_id == 0:
            self.initialize()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        return RawToolResponse(
            tool_name="tools_list",
            arguments={},
            fetched_at=datetime.now(ZoneInfo("Asia/Shanghai")),
            payload=response.content,
            content_type=response.headers.get("Content-Type", "application/json"),
        )
