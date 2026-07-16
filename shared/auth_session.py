#!/usr/bin/env python3
"""
auth_session — MCP 服务器端认证会话管理

功能:
  1. 从环境变量加载测试账号 (TEST_USER / TEST_PASS / ADMIN_USER / ADMIN_PASS)
  2. 自动登录目标 API 服务器，获取 JWT / cookie
  3. 缓存 token，过期前自动刷新
  4. 为 api_get / api_post 等工具提供认证 header

环境变量:
  TEST_USER      — 普通测试账号用户名 (默认: test)
  TEST_PASS      — 普通测试账号密码   (默认: test12345)
  ADMIN_USER     — 管理员账号         (默认: admin)
  ADMIN_PASS     — 管理员密码         (默认: admin12345)
  AUTH_LOGIN_URL — 登录端点 (默认: {API_BASE}/api/auth/login)
  AUTH_METHOD    — 认证方式: "bearer" (JWT) | "cookie" (session cookie)
  AUTH_TOKEN_KEY — response body 中 token 的 JSON path (默认: token)
                  支持 "data.token", "access_token", "token" 等

Usage:
    from auth_session import get_auth, login, get_auth_headers

    # 获取认证 header (自动登录+缓存):
    headers_json = get_auth_headers("test")  # 或 "admin"

    # 在 tool 中使用:
    headers = json.loads(get_auth_headers("test"))
    # 然后用带 token 的 headers 发 HTTP 请求
"""

import json
import os
import ssl
import time
import threading
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# ═══ Config from env ═══

API_BASE = os.getenv("API_BASE", "http://localhost:3000")

TEST_USER = os.getenv("TEST_USER", "test")
TEST_PASS = os.getenv("TEST_PASS", "test12345")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin12345")

# 认证端点 & 方式
AUTH_LOGIN_URL = os.getenv("AUTH_LOGIN_URL", f"{API_BASE}/api/auth/login")
AUTH_REGISTER_URL = os.getenv("AUTH_REGISTER_URL", f"{API_BASE}/api/auth/register")
AUTH_METHOD = os.getenv("AUTH_METHOD", "bearer")       # "bearer" | "cookie"
AUTH_TOKEN_KEY = os.getenv("AUTH_TOKEN_KEY", "token")  # JSON path for token
AUTH_TOKEN_PREFIX = os.getenv("AUTH_TOKEN_PREFIX", "Bearer ")  # prefix in Authorization header

# 缓存有效期 (token 过期前 5 分钟刷新)
CACHE_TTL_SEC = int(os.getenv("AUTH_CACHE_TTL", "3300"))  # 55 minutes default

# ═══ Token cache ═══

_cache: dict[str, dict] = {}   # {"test": {"token": "...", "expires_at": 1234567890}}
_lock = threading.Lock()

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_post(url: str, body: dict, headers: dict | None = None) -> dict:
    """Simple HTTP POST returning parsed JSON."""
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
            return {
                "ok": True,
                "status": resp.status,
                "body": resp.read().decode("utf-8"),
                "headers": dict(resp.headers),
            }
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except:
            pass
        return {
            "ok": False,
            "status": e.code,
            "error": f"HTTP Error {e.code}",
            "body": body_text,
        }
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def _extract_token(body: dict, key_path: str) -> str | None:
    """Extract token from response body by JSON path.
    
    Examples: "token" → body["token"]
              "data.token" → body["data"]["token"]
              "access_token" → body["access_token"]
    """
    value = body
    for part in key_path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return str(value) if value else None


def login(account_type: str = "test") -> dict:
    """Attempt login. Returns {"ok": True, "token": "...", ...} or {"ok": False, "error": "..."}.
    
    account_type: "test" | "admin"
    """
    if account_type == "admin":
        user, pwd = ADMIN_USER, ADMIN_PASS
    else:
        user, pwd = TEST_USER, TEST_PASS
    
    result = _http_post(AUTH_LOGIN_URL, {
        "username": user,
        "password": pwd,
    })
    
    if not result["ok"] or result["status"] != 200:
        return {"ok": False, "error": result.get("error", f"Login failed: HTTP {result.get('status')}")}
    
    try:
        resp_body = json.loads(result["body"])
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Invalid JSON response: {result['body'][:200]}"}
    
    token = _extract_token(resp_body, AUTH_TOKEN_KEY)
    if not token:
        # 可能是 cookie 模式，返回 cookie
        cookie = result.get("headers", {}).get("set-cookie", "")
        if cookie:
            return {"ok": True, "token": "", "cookie": cookie, "method": "cookie"}
        return {"ok": False, "error": f"Token not found at path '{AUTH_TOKEN_KEY}' in response"}
    
    return {
        "ok": True,
        "token": token,
        "method": "bearer",
        "raw_body": resp_body,
    }


def _try_register_then_login(account_type: str) -> dict:
    """Try register if login returns 404, then login again."""
    if account_type == "admin":
        user, pwd = ADMIN_USER, ADMIN_PASS
    else:
        user, pwd = TEST_USER, TEST_PASS
    
    register_result = _http_post(AUTH_REGISTER_URL, {
        "username": user,
        "password": pwd,
        "email": f"{user}@test.local",
    })
    
    # Whether register succeeds or not (e.g. "already exists"), try login again
    return login(account_type)


def get_auth(account_type: str = "test") -> dict:
    """Get authentication token (cached). Returns {"ok": True, "token": "...", "method": "bearer"}.
    
    On first call: login and cache.
    On subsequent calls: return cached token if not expired.
    On expired: re-login.
    On 404 (no auth endpoint): return {"ok": False, "error": "no_auth_endpoint"} — caller handles gracefully.
    """
    now = time.time()
    
    with _lock:
        cached = _cache.get(account_type)
        if cached and cached.get("expires_at", 0) > now:
            return {"ok": True, **{k: v for k, v in cached.items() if k != "expires_at"}}
    
    # Need to login
    result = login(account_type)
    
    if not result["ok"]:
        # If login returned 404, the tested server may not have auth — that's fine
        if "404" in result.get("error", ""):
            return {"ok": False, "error": "no_auth_endpoint"}
        # Try register + login as fallback
        fallback = _try_register_then_login(account_type)
        if fallback["ok"]:
            result = fallback
        else:
            return {"ok": False, "error": result.get("error") or fallback.get("error", "auth failed")}
    
    with _lock:
        _cache[account_type] = {
            "token": result.get("token", ""),
            "method": result.get("method", "bearer"),
            "cookie": result.get("cookie", ""),
            "expires_at": now + CACHE_TTL_SEC,
        }
    
    return {"ok": True, "token": result.get("token", ""), "method": result.get("method", "bearer"), "cookie": result.get("cookie", "")}


def get_auth_headers(account_type: str = "test") -> str:
    """Get auth headers as JSON string for tool consumption.
    
    Returns: '{"Authorization": "Bearer xxx"}' or '{"Cookie": "session=xxx"}'
    On no-auth-endpoint: '{}'
    """
    auth = get_auth(account_type)
    if not auth.get("ok"):
        # No auth available — return empty headers (tests will get 401 which is expected)
        return "{}"
    
    headers = {}
    if auth.get("cookie"):
        headers["Cookie"] = auth["cookie"]
    if auth.get("token"):
        headers["Authorization"] = AUTH_TOKEN_PREFIX + auth["token"]
    
    return json.dumps(headers)


def invalidate_auth(account_type: str | None = None):
    """Clear cached auth tokens. Pass None to clear all."""
    with _lock:
        if account_type is None:
            _cache.clear()
        elif account_type in _cache:
            del _cache[account_type]


def get_auth_status() -> dict:
    """Get current auth session status for debugging."""
    now = time.time()
    with _lock:
        status = {}
        for acct, data in _cache.items():
            status[acct] = {
                "has_token": bool(data.get("token")),
                "method": data.get("method", "unknown"),
                "expires_in_sec": max(0, int(data.get("expires_at", 0) - now)),
                "cookie_len": len(data.get("cookie", "")),
            }
        return {
            "ok": True,
            "config": {
                "api_base": API_BASE,
                "login_url": AUTH_LOGIN_URL,
                "register_url": AUTH_REGISTER_URL,
                "method": AUTH_METHOD,
                "token_key": AUTH_TOKEN_KEY,
                "test_user": TEST_USER,
                "admin_user": ADMIN_USER,
                "cache_ttl_sec": CACHE_TTL_SEC,
            },
            "sessions": status,
        }


# ═══ API HTTP helpers — used by api_get / api_post tools ═══

def _http_request(method: str, url: str, body: str | None = None, headers_json: str = "{}", timeout: int = 30) -> dict:
    """HTTP request with built-in auth retry.
    
    headers_json: JSON string like '{"Authorization": "Bearer xxx"}'
                  If contains auth and request returns 401, auto re-login and retry once.
    """
    try:
        headers = json.loads(headers_json) if headers_json and headers_json != "{}" else {}
    except json.JSONDecodeError:
        headers = {}
    
    if "Content-Type" not in headers and body:
        headers["Content-Type"] = "application/json"
    
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            resp_body = resp.read().decode("utf-8")
            return {
                "ok": True,
                "status": resp.status,
                "headers": dict(resp.headers),
                "body": resp_body[:50000],  # truncate large responses
            }
    except urllib.error.HTTPError as e:
        # If 401 and we have auth, try re-login once
        if e.code == 401:
            # Check if headers contain Authorization
            auth_header = headers.get("Authorization", "") or headers.get("authorization", "")
            if auth_header:
                # Determine account type from the token prefix
                invalidate_auth()
                # Re-get auth
                new_headers_json = get_auth_headers("test")
                try:
                    new_headers = json.loads(new_headers_json)
                except json.JSONDecodeError:
                    new_headers = {}
                
                if new_headers:
                    # Retry with fresh token
                    retry_req = urllib.request.Request(url, data=data, headers=new_headers, method=method.upper())
                    try:
                        with urllib.request.urlopen(retry_req, timeout=timeout, context=SSL_CTX) as resp:
                            resp_body = resp.read().decode("utf-8")
                            return {
                                "ok": True,
                                "status": resp.status,
                                "headers": dict(resp.headers),
                                "body": resp_body[:50000],
                                "_auth_retried": True,
                            }
                    except urllib.error.HTTPError as e2:
                        body_text = ""
                        try:
                            body_text = e2.read().decode("utf-8")
                        except:
                            pass
                        return {
                            "ok": False,
                            "status": e2.code,
                            "error": f"HTTP Error {e2.code}",
                            "body": body_text[:10000],
                        }
        
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except:
            pass
        return {
            "ok": False,
            "status": e.code,
            "error": f"HTTP Error {e.code}: {e.reason}",
            "body": body_text[:10000],
        }
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}
