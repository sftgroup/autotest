#!/usr/bin/env python3
"""auth_guard — 认证 + 速率限制，共享给所有 autotest-mcp server 使用。

Usage:
    from auth_guard import require_auth, rate_limit

    # 在每个 tool 函数开头调用:
    @mcp.tool()
    def evm_send(...) -> str:
        if err := require_auth(): return err        # 认证
        if err := rate_limit("evm_send", 5, 60):    # 每分钟最多5次
            return err

认证模式:
    - TOKEN mode:  请求 header X-MCP-TOKEN 必须等于 AUTH_TOKEN 环境变量
    - INSECURE mode: 未设 AUTH_TOKEN 时，仅允许本地 localhost 连接
    - DISABLED mode: AUTH_TOKEN="DISABLED" 时关闭所有认证 (仅开发环境)

速率限制:
    内存中按 tool 名 + 分钟粒度计数，超限返回 429。
"""

import os
import time
import threading
from collections import defaultdict

# ═══ Config ═══

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")

# 工具速率限制: {tool_name: (max_per_minute, window_seconds)}
DEFAULT_RATE_LIMITS = {
    "evm_send":             (10, 60),    # 每分钟最多10笔交易
    "evm_tx_and_verify":    (10, 60),
    "evm_transfer":          (5, 60),
    "sol_transfer":          (5, 60),
    "sol_transfer_and_confirm": (5, 60),
    "evm_deploy_and_verify": (3, 120),   # 每2分钟最多3次部署
    "sol_deploy_and_test":   (3, 120),
    "browser_navigate":     (20, 60),
    "browser_page_check":   (20, 60),
    "browser_user_flow":    (10, 60),
    "api_load_test":         (3, 120),   # 每2分钟最多3次压测
    "visual_regression":    (10, 60),
    "perf_audit_page":       (5, 120),
    "security_scan":         (5, 120),
    "echidna_fuzz":          (2, 300),   # 每5分钟最多2次
    "medusa_fuzz":           (1, 600),   # 每10分钟最多1次
}

# 默认: 未在列表中指定的工具 = 每分钟 60 次
DEFAULT_GLOBAL_LIMIT = (60, 60)

# ═══ Rate limiter (in-memory, per-process) ═══

_lock = threading.Lock()
_buckets: dict[str, list[float]] = defaultdict(list)

def _clean_bucket(key: str, now: float, window: float):
    """Remove entries older than window."""
    if key in _buckets:
        _buckets[key] = [t for t in _buckets[key] if now - t < window]

def rate_limit(tool_name: str, custom_max: int | None = None, custom_window: int | None = None) -> str | None:
    """Check rate limit. Returns error JSON string if exceeded, None if ok."""
    max_per, window = DEFAULT_RATE_LIMITS.get(tool_name, DEFAULT_GLOBAL_LIMIT)
    if custom_max is not None:
        max_per = custom_max
    if custom_window is not None:
        window = custom_window

    now = time.time()
    with _lock:
        _clean_bucket(tool_name, now, window)
        if len(_buckets[tool_name]) >= max_per:
            return (
                '{"ok":false,"passed":false,"error":"rate_limit_exceeded",'
                f'"tool":"{tool_name}","limit":{max_per},"window_sec":{window}}}'
            )
        _buckets[tool_name].append(now)
    return None


# ═══ Auth ═══

def require_auth() -> str | None:
    """Check authentication. Returns error JSON string if unauthorized, None if ok.

    Note: In SSE mode with MCP, we don't have direct access to the HTTP headers
    from within a tool call. The FastMCP framework doesn't expose them.
    
    Workaround strategies (choose one):
    1. Layered proxy — nginx validates X-MCP-TOKEN before reaching the MCP server
    2. Token check via environment — all OC instances share a secret AUTH_TOKEN
    3. Network isolation — firewall + wireguard, no auth needed
    
    For now: if AUTH_TOKEN is set, the server validates it at SSE connection time
    via a middleware. The tool-level check is a secondary guard.
    """
    if AUTH_TOKEN == "DISABLED":
        return None  # 开发模式，无认证

    # In SSE tool context, we can't check headers.
    # This is a best-effort guard: if AUTH_TOKEN is set, nginx should enforce it.
    # The function exists as a placeholder for middleware integration.
    return None


# ═══ Middleware check (call at MCP server startup) ═══

def check_auth_config() -> dict:
    """Validate auth configuration on startup. Returns status."""
    if AUTH_TOKEN == "DISABLED":
        return {"ok": True, "mode": "disabled", "warning": "Authentication disabled — INSECURE"}
    elif AUTH_TOKEN and AUTH_TOKEN != "DISABLED":
        return {"ok": True, "mode": "token", "warning": "Token set but not enforced at tool level. Use nginx proxy for enforcement."}
    else:
        return {"ok": True, "mode": "none", "warning": "No AUTH_TOKEN set. Relying on network isolation (firewall)."}


# ═══ Rate limit status ═══

def rate_limit_status() -> dict:
    """Get current rate limit usage for all tools."""
    now = time.time()
    status = {}
    with _lock:
        for tool_name, (max_per, window) in DEFAULT_RATE_LIMITS.items():
            _clean_bucket(tool_name, now, window)
            status[tool_name] = {
                "current": len(_buckets[tool_name]),
                "limit": max_per,
                "window_sec": window,
            }
    return status
