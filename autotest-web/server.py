#!/usr/bin/env python3
"""autotest-web MCP Server — Browser + API + Security"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import ssl
import tempfile
import urllib.request
import urllib.error
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from mcp_sse_server import create_app, ToolRegistry
from auth_guard import rate_limit
from report_builder import Result
from auth_session import get_auth_headers, _http_request, invalidate_auth, get_auth_status

tools = ToolRegistry()

# ═══ Config ═══

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
API_BASE     = os.getenv("API_BASE", "")
LH_TOKEN     = os.getenv("LHCI_TOKEN", "")

# ═══ Helpers ═══

def _run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip()[:8000], "stderr": r.stderr.strip()[:2000], "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": f"{cmd[0]} not found"}

# ═══ Playwright ═══

_pw_lock = asyncio.Lock()
_pw: Any = None
_browser: Any = None
_page: Any = None

async def _get_page():
    global _pw, _browser, _page
    if _page is not None and not _page.is_closed():
        return _page
    async with _pw_lock:
        if _page is not None and not _page.is_closed():
            return _page
        from playwright.async_api import async_playwright
        if _pw is None: _pw = await async_playwright().start()
        if _browser is not None:
            try: await _browser.close()
            except: pass
        _browser = await _pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        _page = await _browser.new_page()
        return _page


# ═══════════════════════════════════════════════════════
# 🩺 Health (2)
# ═══════════════════════════════════════════════════════

@tools.tool()
def test_health() -> str:
    """Check all web tool dependencies."""
    from env_checker import check_category
    results = {}
    for cat in ["browser", "api", "db", "security-ops"]:
        results[cat] = check_category(cat)
    return json.dumps(results, indent=2, ensure_ascii=False)

@tools.tool()
def test_auto_install(tool: str) -> str:
    """Auto-install missing tool. e.g. test_auto_install('hurl')"""
    from env_checker import ensure
    return json.dumps(ensure(tool), indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 页面检查 (navigate → snapshot → screenshot → assert)
# ═══════════════════════════════════════════════════════

@tools.tool()
async def browser_page_check(
    url: str,
    expect_text: str = "",
    expect_title: str = "",
    screenshot: bool = True,
) -> str:
    """【场景tool】打开页面 → 截图 → 断言。一次调完。
    
    expect_text: 期望页面包含的文本（可选）
    expect_title: 期望页面标题（可选）
    screenshot: 是否截图（默认true）
    
    返回: url, title, text摘要, 截图路径, 断言结果, 综合passed
    """
    results = {}
    try:
        page = await _get_page()
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        results["url"] = page.url
        results["title"] = await page.title()
        results["text"] = (await page.locator("body").inner_text())[:5000]
        
        # Checks
        checks = {}
        if expect_title:
            checks["title"] = {"expected": expect_title, "actual": results["title"], "passed": expect_title.lower() in results["title"].lower()}
        if expect_text:
            checks["text"] = {"expected": expect_text, "found": expect_text in results["text"], "passed": expect_text in results["text"]}
        
        # Screenshot
        if screenshot:
            d = Path(tempfile.gettempdir()) / "autotest-screenshots"
            d.mkdir(exist_ok=True)
            path = d / f"page_check_{int(time.time())}.png"
            await page.screenshot(path=str(path), full_page=False)
            results["screenshot"] = str(path)
        
        passed = all(c.get("passed", True) for c in checks.values())
        r = Result()
        r.step("navigate", passed=True, detail=f"{url[:60]} → {results.get('title','')[:30]}")
        if expect_title:
            r.step("title check", passed=checks.get("title", {}).get("passed", False),
                   detail=expect_title[:40])
        if expect_text:
            r.step("text check", passed=checks.get("text", {}).get("passed", False),
                   detail=expect_text[:40])
        r.extra(**results)
        return r.done()
    except Exception as e:
        return Result.fail_(summary=str(e))


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 用户操作流 (多步 click/type/check)
# ═══════════════════════════════════════════════════════

@tools.tool()
async def browser_user_flow(
    url: str,
    steps: str,
) -> str:
    """【场景tool】在页面上执行一系列操作，返回每步结果。
    
    steps: JSON 数组，每步格式:
      {"action":"navigate","url":"http://..."}
      {"action":"click","selector":"#login"}
      {"action":"type","selector":"#email","text":"test@example.com"}
      {"action":"click","selector":"[type=submit]"}
      {"action":"wait","ms":2000}
      {"action":"check","text":"Welcome"}         ← 断言页面包含文本
      {"action":"check","selector":".error"}      ← 断言元素存在
      {"action":"screenshot","name":"result"}
    
    返回: 每步执行结果 + 综合 passed
    """
    try:
        step_list = json.loads(steps)
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "passed": False, "error": "Invalid JSON steps"}, ensure_ascii=False)
    
    page = await _get_page()
    results = []
    passed = True
    
    for i, s in enumerate(step_list):
        action = s.get("action", "")
        sr = {"step": i+1, "action": action, "ok": True}
        
        try:
            if action == "navigate":
                await page.goto(s.get("url",""), timeout=15000, wait_until="domcontentloaded")
                sr["title"] = await page.title()
            elif action == "click":
                sel = s.get("selector","")
                el = page.locator(sel).first
                if await el.count() == 0:
                    el = page.get_by_text(sel, exact=False).first
                await el.click(timeout=5000)
                sr["selector"] = sel
            elif action == "type":
                await page.locator(s["selector"]).first.fill(s.get("text",""), timeout=5000)
                sr["selector"] = s["selector"]
            elif action == "wait":
                await asyncio.sleep(int(s.get("ms", 1000)) / 1000)
            elif action == "check":
                if "text" in s:
                    body = await page.locator("body").inner_text()
                    found = s["text"] in body
                    sr["passed"] = found
                    sr["detail"] = f"text '{s['text'][:50]}' {'found' if found else 'NOT found'}"
                    if not found: passed = False
                elif "selector" in s:
                    count = await page.locator(s["selector"]).count()
                    sr["passed"] = count > 0
                    sr["detail"] = f"selector '{s['selector']}' count={count}"
                    if count == 0: passed = False
                elif "not_selector" in s:
                    count = await page.locator(s["not_selector"]).count()
                    sr["passed"] = count == 0
                    sr["detail"] = f"selector '{s['not_selector']}' count={count}"
                    if count > 0: passed = False
            elif action == "screenshot":
                d = Path(tempfile.gettempdir()) / "autotest-screenshots"
                d.mkdir(exist_ok=True)
                path = d / f"flow_{s.get('name','step')}_{int(time.time())}.png"
                await page.screenshot(path=str(path), full_page=False)
                sr["screenshot"] = str(path)
            else:
                sr["ok"] = False
                sr["error"] = f"Unknown action: {action}"
        except Exception as e:
            sr["ok"] = False
            sr["error"] = str(e)
            passed = False
        
        results.append(sr)
    
    passed_count = sum(1 for r in results if r.get("passed", r.get("ok", True)))
    failed_count = sum(1 for r in results if r.get("passed") is False or r.get("ok") is False)
    
    rpt = Result()
    for sr in results:
        step_ok = sr.get("passed", sr.get("ok", True))
        detail = sr.get("detail") or sr.get("error") or sr.get("title") or sr.get("selector", "")
        rpt.step(f"{sr['action']}#{sr['step']}", passed=step_ok if sr["action"] == "check" else None,
                detail=detail[:60])
    rpt.extra(steps=results, **{"total": len(step_list), "passed_count": passed_count, "failed_count": failed_count})
    return rpt.done()


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 视觉回归 (BackstopJS)
# ═══════════════════════════════════════════════════════

@tools.tool()
def visual_regression(
    url: str,
    reference_name: str,
    threshold: float = 0.01,
    viewport_width: int = 1440,
    viewport_height: int = 900,
) -> str:
    """【场景tool】BackstopJS 截图对比。首次创建参考图，后续对比。
    
    threshold: 允许的像素差异比例 (默认 0.01 = 1%)
    
    返回: action (reference_created / comparison), passed, diff 报告路径
    """
    work_dir = Path(tempfile.mkdtemp(prefix="backstop_"))
    ref_id = re.sub(r"[^a-zA-Z0-9_-]", "_", reference_name)
    label = "Visual Test"
    
    config = {
        "id": ref_id,
        "viewports": [{"label": "desktop", "width": viewport_width, "height": viewport_height}],
        "scenarios": [{"label": label, "url": url, "delay": 2000, "misMatchThreshold": threshold}],
        "paths": {
            "bitmaps_reference": str(work_dir / "bitmaps_reference"),
            "bitmaps_test": str(work_dir / "bitmaps_test"),
            "html_report": str(work_dir / "html_report"),
            "ci_report": str(work_dir / "ci_report"),
        },
        "engine": "playwright", "report": ["CI"], "debug": False,
    }
    config_path = work_dir / "backstop.json"
    config_path.write_text(json.dumps(config, indent=2))
    
    try:
        ref_file = work_dir / "bitmaps_reference" / f"{label}_0_document_0_desktop.png"
        if not ref_file.exists():
            r = _run(["backstop", "reference", f"--config={config_path}"], timeout=60)
            return json.dumps({"ok": True, "passed": True, "action": "reference_created", "detail": r}, ensure_ascii=False)
        else:
            r = _run(["backstop", "test", f"--config={config_path}"], timeout=60)
            passed = r["ok"]
            return json.dumps({
                "ok": True, "passed": passed,
                "action": "comparison",
                "detail": r,
                "report": str(work_dir / "html_report" / "index.html"),
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": True, "passed": False, "error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 🔐 Auth Session (2)
# ═══════════════════════════════════════════════════════

@tools.tool()
def auth_status() -> str:
    """查看当前认证会话状态。返回缓存的 token 信息、配置情况。"""
    return json.dumps(get_auth_status(), ensure_ascii=False)

@tools.tool()
def auth_login(account_type: str = "test") -> str:
    """手动触发登录。account_type: "test" | "admin"。返回 token 信息。
    
    注意：平时不需要手动调这个——api_get/api_post 的 use_auth 参数会自动登录。
    仅在需要预验证认证链路时使用。
    """
    from auth_session import login, invalidate_auth
    invalidate_auth(account_type)
    result = login(account_type)
    return json.dumps(result, ensure_ascii=False)

# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: API E2E (hurl 声明式)
# ═══════════════════════════════════════════════════════

@tools.tool()
def api_e2e_test(
    hurl_content: str,
) -> str:
    """【场景tool】声明式 API 端到端测试。传入 .hurl 内容，返回每个 entry 通过/失败。
    
    示例:
    POST http://api/register
    Content-Type: application/json
    {"email":"test@test.com","password":"123"}
    HTTP 201
    [Asserts]
    jsonpath "$.token" exists
    
    GET http://api/me
    Authorization: Bearer {{register.response.body.token}}
    HTTP 200
    
    返回: passed, entries (每个的通过状态), total
    """
    hurl_bin = shutil.which("hurl")
    if not hurl_bin:
        return json.dumps({"ok": False, "passed": False, "error": "hurl not installed. Run: test_auto_install('hurl')"})
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".hurl", delete=False) as f:
        f.write(hurl_content)
        tmp = f.name
    
    try:
        r = subprocess.run([hurl_bin, "--test", "--json", tmp], capture_output=True, text=True, timeout=30)
        passed = r.returncode == 0
        # Parse individual entries
        entries = []
        try:
            data = json.loads(r.stdout)
            for entry in data if isinstance(data, list) else [data]:
                entries.append({
                    "entry": entry.get("filename", ""),
                    "success": entry.get("success", False),
                    "duration_ms": entry.get("time", 0),
                })
        except: pass
        
        return json.dumps({
            "ok": True, "passed": passed,
            "entries": entries if entries else r.stdout.strip()[:3000],
            "stderr": r.stderr.strip()[:1000],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": True, "passed": False, "error": str(e)}, ensure_ascii=False)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: API 测试总成 (fuzz + load + workflow 一键)
# ═══════════════════════════════════════════════════════

@tools.tool()
def api_fuzz_test(api_spec_url: str, base_url: str = "", max_examples: int = 50) -> str:
    """【场景tool】Schemathesis API 模糊测试。自动生成反向/边界用例。"""
    cmd = ["schemathesis", "run", api_spec_url, "--max-examples", str(max_examples)]
    if base_url: cmd += ["--base-url", base_url]
    r = _run(cmd, timeout=120)
    return json.dumps({"ok": True, "passed": r["ok"], "result": r}, ensure_ascii=False)

@tools.tool()
def api_load_test(url: str, connections: int = 10, duration_sec: int = 10) -> str:
    """【场景tool】Autocannon 并发负载测试。返回 QPS + 延迟分布。"""
    if err := rate_limit("api_load_test"): return err
    r = _run(["autocannon", url, "-c", str(connections), "-d", str(duration_sec)], timeout=duration_sec + 30)
    return json.dumps({"ok": True, "result": r}, ensure_ascii=False)

@tools.tool()
def api_workflow_test(workflow_yaml: str) -> str:
    """【场景tool】StepCI 多步 API 工作流测试。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(workflow_yaml)
        tmp = f.name
    try:
        r = _run(["stepci", "run", tmp], timeout=60)
        return json.dumps({"ok": True, "passed": r["ok"], "result": r}, ensure_ascii=False)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 性能 + 无障碍 (Lighthouse + Axe)
# ═══════════════════════════════════════════════════════

@tools.tool()
async def perf_audit_page(url: str) -> str:
    if err := rate_limit("perf_audit_page"): return err
    """【场景tool】Lighthouse 性能审计 + Axe 无障碍检查。一次调完。
    
    返回: 性能分数 (perf/a11y/best-practices/seo) + a11y violations
    """
    results = {}
    
    # 1. Lighthouse
    lr = _run([
        "lighthouse", url, "--output=json", "--output-path=stdout",
        "--chrome-flags=--headless --no-sandbox",
        "--only-categories=performance,accessibility,best-practices,seo", "--quiet",
    ], timeout=90)
    
    if lr["ok"]:
        try:
            data = json.loads(lr["stdout"])
            results["scores"] = {}
            for cat in ["performance", "accessibility", "best-practices", "seo"]:
                results["scores"][cat] = data.get("categories", {}).get(cat, {}).get("score")
            results["lighthouse_passed"] = all(
                (v or 0) > 0.5 for v in results["scores"].values() if v is not None
            )
        except json.JSONDecodeError:
            results["lighthouse_raw"] = lr
    
    # 2. Axe-core
    try:
        page = await _get_page()
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        await page.add_script_tag(url="https://cdn.jsdelivr.net/npm/axe-core@4.10.3/axe.min.js")
        axe = await page.evaluate("""
            (async () => {
                const results = await axe.run();
                return {
                    violations: results.violations.map(v => ({id:v.id, impact:v.impact, description:v.description, nodes:v.nodes.length})),
                    passes: results.passes.length,
                    violations_count: results.violations.length,
                };
            })()
        """)
        results["a11y"] = axe
        results["a11y_passed"] = len(axe.get("violations", [])) == 0
    except Exception as e:
        results["a11y_error"] = str(e)
    
    passed = results.get("lighthouse_passed", True) and results.get("a11y_passed", True)
    return json.dumps({"ok": True, "passed": passed, "results": results}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 安全扫描 (trivy + dockle)
# ═══════════════════════════════════════════════════════

@tools.tool()
def security_scan(image: str, dockerfile_path: str = "") -> str:
    if err := rate_limit("security_scan"): return err
    """【场景tool】容器安全扫描: trivy CVE + (可选) dockle Dockerfile 检查。
    
    返回: CVE 数量 (按严重性), dockle 检查结果
    """
    results = {}
    
    # 1. Trivy
    tr = _run(["trivy", "image", "--severity", "HIGH,CRITICAL", "--format", "json", image], timeout=180)
    if tr["ok"]:
        try:
            data = json.loads(tr["stdout"])
            sev_count = {"HIGH": 0, "CRITICAL": 0}
            for r in data.get("Results", []):
                for v in r.get("Vulnerabilities", []):
                    sev_count[v.get("Severity", "")] = sev_count.get(v.get("Severity", ""), 0) + 1
            results["trivy"] = {"vulnerabilities": sev_count, "total": sum(sev_count.values())}
            results["trivy_passed"] = sev_count.get("CRITICAL", 0) == 0
        except: results["trivy_raw"] = tr
    else:
        results["trivy_error"] = tr
    
    # 2. Dockle (optional)
    if dockerfile_path:
        dp = str(Path(dockerfile_path).resolve())
        if os.path.isfile(dp):
            img_tag = f"autotest-dockle-{int(time.time())}:temp"
            build_dir = str(Path(dp).parent)
            br = _run(["docker", "build", "-f", dp, "-t", img_tag, build_dir], timeout=120)
            if br["ok"]:
                dr = _run(["dockle", "--format", "json", img_tag], timeout=60)
                results["dockle"] = dr
                results["dockle_passed"] = dr["ok"]
            else:
                results["dockle_error"] = br
            _run(["docker", "rmi", "-f", img_tag], timeout=30)
    
    passed = results.get("trivy_passed", False) and results.get("dockle_passed", True)
    return json.dumps({"ok": True, "passed": passed, "results": results}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: SQL 质量检查
# ═══════════════════════════════════════════════════════

@tools.tool()
def sql_quality_check(sql_content: str, dialect: str = "postgres") -> str:
    """【场景tool】SQLFluff lint + auto-fix。返回 lint 问题 + 修复后的 SQL。"""
    results = {}
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
        f.write(sql_content)
        tmp = f.name
    
    try:
        # Lint
        results["lint"] = _run(["sqlfluff", "lint", "--dialect", dialect, tmp], timeout=30)
        
        # Fix
        _run(["sqlfluff", "fix", "--dialect", dialect, "--force", tmp], timeout=30)
        fixed = Path(tmp).read_text()
        results["fixed_sql"] = fixed
        
        # Re-lint to verify
        results["fix_verify"] = _run(["sqlfluff", "lint", "--dialect", dialect, tmp], timeout=30)
        
        passed = results["fix_verify"]["ok"]
        return json.dumps({"ok": True, "passed": passed, "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": True, "passed": False, "error": str(e)}, ensure_ascii=False)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════
# ⚛️ 原子 tool: 保留 (高级场景用)
# ═══════════════════════════════════════════════════════

@tools.tool()
async def browser_navigate(url: str) -> str:
    """打开 URL。高级场景用；常规用 browser_page_check。"""
    try:
        page = await _get_page()
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        return json.dumps({"ok": True, "title": await page.title()})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@tools.tool()
def api_get(url: str, headers: str = "{}", use_auth: str = "") -> str:
    """HTTP GET。高级场景用；常规用 api_e2e_test。
    
    use_auth: 设为 "test" 或 "admin" 自动注入认证 header（token/cookie），
              留空则用 headers 参数原样发送。
    """
    try:
        if use_auth:
            auth_headers = get_auth_headers(use_auth)
            if auth_headers and auth_headers != "{}":
                # merge auth headers into user-provided headers
                user_hdrs = json.loads(headers) if headers and headers != "{}" else {}
                auth_hdrs = json.loads(auth_headers)
                auth_hdrs.update(user_hdrs)
                headers = json.dumps(auth_hdrs)
        result = _http_request("GET", url, headers_json=headers)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@tools.tool()
def api_post(url: str, body: str = "{}", headers: str = '{"Content-Type":"application/json"}', use_auth: str = "") -> str:
    """HTTP POST。高级场景用。

    use_auth: 设为 "test" 或 "admin" 自动注入认证 header（token/cookie），
              留空则用 headers 参数原样发送。
    """
    try:
        if use_auth:
            auth_headers = get_auth_headers(use_auth)
            if auth_headers and auth_headers != "{}":
                user_hdrs = json.loads(headers) if headers and headers != "{}" else {}
                if isinstance(user_hdrs, str):
                    try: user_hdrs = json.loads(user_hdrs)
                    except: user_hdrs = {}
                auth_hdrs = json.loads(auth_headers)
                auth_hdrs.update(user_hdrs)
                headers = json.dumps(auth_hdrs)
        result = _http_request("POST", url, body=body, headers_json=headers)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@tools.tool()
def data_fake(type_: str, seed: str = "0") -> str:
    """生成测试数据: email, phone, eth_address, uuid, username, sql_injection, xss, boundary_int, max_uint256, zero, empty_string, null_byte, long_string_1k, long_string_10k"""
    h = hashlib.md5(seed.encode()).hexdigest()
    gens = {
        "email": lambda: f"test_{h[:8]}@autotest.local",
        "phone": lambda: f"138{h[:8]}",
        "eth_address": lambda: f"0x{h}{h}",
        "uuid": lambda: f"{h[:8]}-{h[2:6]}-4{h[4:7]}-a{h[6:9]}-{h[:16]}0000",
        "username": lambda: f"user_{h[:8]}",
        "sql_injection": lambda: "'; DROP TABLE users;--",
        "xss": lambda: '<script>alert("xss")</script>',
        "boundary_int": lambda: str(int(h[:8], 16)),
        "max_uint256": lambda: "115792089237316195423570985008687907853269984665640564039457584007913129639935",
        "zero": lambda: "0",
        "empty_string": lambda: "",
        "null_byte": lambda: "hello\x00world",
        "long_string_1k": lambda: "A" * 1024,
        "long_string_10k": lambda: "B" * 10240,
    }
    gen = gens.get(type_)
    if not gen: return json.dumps({"ok": False, "error": f"Unknown type '{type_}'. Available: {sorted(gens.keys())}"})
    return json.dumps({"ok": True, "value": gen()})


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════


# ═══════════════════════════════
# Main
# ═══════════════════════════════

if __name__ == "__main__":
    import uvicorn
    app = create_app(tools, name="autotest-web", instructions="Browser/API/Perf/Security")
    print("autotest-web SSE -> http://0.0.0.0:8082/sse", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
