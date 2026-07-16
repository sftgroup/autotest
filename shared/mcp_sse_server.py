#!/usr/bin/env python3
"""
mcp_sse_server — 标准 MCP Server transport 层

同时支持两种 transport：
  - SSE:       GET /sse + POST /message?session_id=xxx
  - Streamable HTTP: POST /mcp  (支持 session header 或 JSON-RPC 内的 sessionId)

替代 FastMCP，确保与 OpenClaw MCP 客户端兼容。

用法:
    from mcp_sse_server import create_app, ToolRegistry
    
    tools = ToolRegistry()
    
    @tools.tool()
    def my_tool(x: str) -> str:
        '''简介'''
        return json.dumps({"ok": True})
    
    app = create_app(tools, name="my-server")
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse
import anyio

from mcp.types import Tool, CallToolResult, Implementation

log = logging.getLogger(__name__)


# ═══ Tool Registry ═══

class ToolRegistry:
    """收集 @tool() 注册的函数."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def tool(self, name: str | None = None, description: str | None = None):
        """装饰器：注册一个 tool"""
        def decorator(fn: Callable):
            t_name = name or fn.__name__
            t_desc = description or (fn.__doc__ or "").strip()

            import inspect
            sig = inspect.signature(fn)
            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                ptype = "string"
                if param.annotation is int:
                    ptype = "integer"
                elif param.annotation is float:
                    ptype = "number"
                elif param.annotation is bool:
                    ptype = "boolean"
                properties[pname] = {"type": ptype, "description": pname}
                if param.default is inspect.Parameter.empty:
                    required.append(pname)

            self._tools[t_name] = {
                "fn": fn,
                "name": t_name,
                "description": t_desc,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
            return fn
        return decorator

    def list_tools(self) -> list[Tool]:
        result = []
        for info in self._tools.values():
            result.append(Tool(
                name=info["name"],
                description=info["description"],
                inputSchema=info["inputSchema"],
            ))
        return result

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        info = self._tools.get(name)
        if not info:
            return CallToolResult(
                content=[{"type": "text", "text": json.dumps({"error": f"unknown tool: {name}"})}],
                isError=True,
            )
        try:
            fn = info["fn"]
            import inspect
            if inspect.iscoroutinefunction(fn):
                result = await fn(**arguments)
            else:
                result = fn(**arguments)
            return CallToolResult(
                content=[{"type": "text", "text": str(result)}]
            )
        except Exception as e:
            log.exception(f"Tool {name} failed")
            return CallToolResult(
                content=[{"type": "text", "text": json.dumps({"error": str(e)})}],
                isError=True,
            )


# ═══ Session Store ═══

_session_store: dict[str, dict] = {}


# ═══ Message Handler (shared by both transports) ═══

# ── Auto Report Wrapper ──

def _auto_wrap_report(tool_name: str, result: dict) -> dict:
    """将原子 tool 的原始 JSON 返回自动包装为统一 report 格式。
    
    判定逻辑：
    - result 有 "ok" 字段 → ok=true 则 PASS
    - result 有 "passed" 字段 → 直接取 passed
    - result 有 "exit_code" 字段 → exit_code=0 则 PASS
    - 其他 → UNKNOWN
    """
    # 解析 verdict
    if "passed" in result:
        passed = result["passed"]
    elif "ok" in result:
        passed = result["ok"]
    elif "exit_code" in result:
        passed = result.get("exit_code", -1) == 0
    else:
        passed = None
    
    verdict = "PASS" if passed else ("FAIL" if passed is False else "INFO")
    
    # 构建 summary
    parts = [f"{'🟢' if passed else '🔴' if passed is False else 'ℹ️'} {tool_name}"]
    if "tx_hash" in result:
        parts.append(f"tx={result['tx_hash'][:12]}...")
    if "status" in result:
        parts.append(f"HTTP {result['status']}")
    if "stdout" in result and result["stdout"]:
        out = str(result["stdout"])[:80].replace("\n", " ")
        parts.append(out)
    if "error" in result and result["error"]:
        parts.append(f"err={str(result['error'])[:60]}")
    summary = " | ".join(parts)
    
    # 构建 checks
    checks = []
    if isinstance(passed, bool):
        checks.append({"step": tool_name, "passed": passed, "status": "✅" if passed else "🔴"})
    
    return {
        "verdict": verdict,
        "summary": summary,
        "checks": checks,
        "passed": passed,
    }

async def _get_tools_response(tools: ToolRegistry) -> dict:
    """Build tools/list response."""
    tool_list = tools.list_tools()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
            for t in tool_list
        ]
    }


async def _handle_message(
    msg: dict,
    session_data: dict | None,
    tools: ToolRegistry,
    server_info: Implementation,
) -> dict | None:
    """Handle a single JSON-RPC message. Returns response or None."""
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": server_info.name,
                    "version": server_info.version,
                },
            }
        }

    elif method == "notifications/initialized":
        if session_data:
            session_data["initialized"] = True
        return None

    elif method == "tools/list":
        tool_list = await _get_tools_response(tools)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": tool_list,
        }

    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        result = await tools.call_tool(tool_name, arguments)
        content_text = ""
        for c in result.content:
            content_text = c.text
        
        # ── 统一报告分层 ──
        # 如果工具已返回 verdict 格式 (report_builder.Result)，直接使用
        # 否则自动包装为 report 格式
        try:
            content_obj = json.loads(content_text)
        except Exception:
            content_obj = {"text": content_text}
        
        # 检查是否已经是 verdict 格式
        if isinstance(content_obj, dict) and "verdict" in content_obj:
            report = content_obj
        else:
            report = _auto_wrap_report(tool_name, content_obj)
        
        # 分层返回：summary + checks + details
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "structuredContent": {
                    "verdict": report.get("verdict", "UNKNOWN"),
                    "summary": report.get("summary", ""),
                    "checks": report.get("checks", []),
                    "details": json.dumps(content_obj, ensure_ascii=False, default=str)[:8000],
                },
                "isError": result.isError,
            }
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


async def _process_messages(
    body: bytes,
    session_data: dict | None,
    tools: ToolRegistry,
    server_info: Implementation,
) -> list[dict]:
    """Process one or more JSON-RPC messages, return responses."""
    try:
        msgs = json.loads(body)
    except Exception:
        return [{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}]

    if not isinstance(msgs, list):
        msgs = [msgs]

    responses = []
    for msg in msgs:
        resp = await _handle_message(msg, session_data, tools, server_info)
        if resp:
            responses.append(resp)
    return responses


# ═══ App Factory ═══

def create_app(
    tools: ToolRegistry,
    name: str = "mcp-server",
    instructions: str = "",
    version: str = "1.0.0",
) -> Starlette:
    """创建标准 MCP Server (支持 SSE + Streamable HTTP)"""

    server_info = Implementation(name=name, version=version)

    # ── SSE Transport ──

    # SSE: GET /sse → POST /message

    async def sse_endpoint(request: Request):
        """GET /sse — SSE 长连接"""
        session_id = uuid.uuid4().hex
        send_queue = anyio.create_memory_object_stream(max_buffer_size=100)
        _session_store[session_id] = {
            "queue": send_queue[0],
            "initialized": False,
        }

        async def event_generator():
            try:
                yield {
                    "event": "endpoint",
                    "data": f"/message?session_id={session_id}"
                }
                async with send_queue[1]:
                    async for item in send_queue[1]:
                        yield {"data": json.dumps(item)}
            except Exception:
                pass
            finally:
                _session_store.pop(session_id, None)

        return EventSourceResponse(event_generator())

    async def sse_messages_endpoint(request: Request):
        """POST /message — SSE transport 的消息入口"""
        session_id = request.query_params.get("session_id", "")
        session_data = _session_store.get(session_id)

        if not session_data:
            return Response("Session not found", status_code=404)

        body = await request.body()
        responses = await _process_messages(body, session_data, tools, server_info)

        if responses:
            try:
                await session_data["queue"].send(responses if len(responses) > 1 else responses[0])
            except Exception:
                pass

        return Response("", status_code=202, media_type="text/plain")

        """POST /mcp — Streamable HTTP transport 入口 (MCP 标准)"""
        body = await request.body()
        session_id = request.headers.get("Mcp-Session-Id", "")
        session_data = _session_store.get(session_id) if session_id else None
        responses = await _process_messages(body, session_data, tools, server_info)
        if not responses:
            return Response("", status_code=202)
        result = responses[0] if len(responses) == 1 else responses
        try:
            msgs = json.loads(body)
            if not isinstance(msgs, list):
                msgs = [msgs]
        except Exception:
            msgs = []
        headers = {"Content-Type": "application/json"}
        new_session_id = None
        for msg in msgs:
            if msg.get("method") == "initialize":
                new_session_id = uuid.uuid4().hex
                send_queue = anyio.create_memory_object_stream(max_buffer_size=100)
                _session_store[new_session_id] = {
                    "queue": send_queue[0],
                    "initialized": True,
                }
                headers["Mcp-Session-Id"] = new_session_id
                break
        return Response(
            json.dumps(result),
            status_code=200,
            headers=headers,
        )

    async def mcp_delete_endpoint(request: Request):
        """DELETE /mcp — 关闭 session"""
        session_id = request.headers.get("Mcp-Session-Id", "")
        if session_id in _session_store:
            del _session_store[session_id]
        return Response("", status_code=200)

    # ── Health ──

    async def health_endpoint(request: Request):
        return Response(json.dumps({"status": "ok", "name": name, "version": version}),
                        media_type="application/json")

    app = Starlette(
        debug=False,
        routes=[
            Route("/sse", sse_endpoint),
            Route("/message", sse_messages_endpoint, methods=["POST"]),
        ],
    )

    return app
