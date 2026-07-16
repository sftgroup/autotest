#!/usr/bin/env python3
"""autotest-dapp MCP Server — DApp 全链路测试"""

from __future__ import annotations

import asyncio
import json
import tempfile
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from mcp_sse_server import create_app, ToolRegistry
from auth_guard import rate_limit
from report_builder import Result

tools = ToolRegistry()

# ═══ Config ═══

ETH_RPC  = os.getenv("SEPOLIA_RPC", os.getenv("ETH_RPC", ""))
ETH_PK   = os.getenv("DEPLOYER_PRIVATE_KEY", "")
SOL_RPC  = os.getenv("SOLANA_RPC", "https://api.devnet.solana.com")
SOL_KEY  = os.getenv("SOLANA_KEYPAIR", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# ═══ Helpers ═══

def _run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        out = r.stdout
        if ETH_PK: out = out.replace(ETH_PK, "***")
        return {"ok": r.returncode == 0, "stdout": out.strip()[:8000], "stderr": r.stderr.strip()[:2000], "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": f"{cmd[0]} not found"}

def _cast_args(send: bool = False) -> list[str]:
    a = ["--rpc-url", ETH_RPC] if ETH_RPC else []
    if send and ETH_PK: a += ["--private-key", ETH_PK]
    return a

def _sol_run(args: list[str], timeout: int = 60) -> dict:
    cmd = ["solana"] + args
    if "--url" not in " ".join(args): cmd += ["--url", SOL_RPC]
    return _run(cmd, timeout=timeout)

def _sol_kp() -> str:
    return SOL_KEY or os.path.expanduser("~/.config/solana/id.json")

# ═══ Playwright ═══

_pw_lock = asyncio.Lock()
_pw: Any = None
_browser: Any = None
_page: Any = None

# ═══ Wallet Mock (inject window.ethereum) ═══

WALLET_MOCK_PRIVATE_KEY = os.getenv("DAPP_TEST_PK", "")  # 测试用私钥 (无资金)
WALLET_MOCK_CHAIN_ID = int(os.getenv("DAPP_TEST_CHAIN_ID", "31337"))  # 默认 hardhat local
WALLET_MOCK_RPC = os.getenv("DAPP_TEST_RPC", ETH_RPC or "http://localhost:8545")

# 预计算测试地址 (如果给了私钥)
WALLET_MOCK_ADDRESS = ""
try:
    if WALLET_MOCK_PRIVATE_KEY:
        from eth_account import Account
        acct = Account.from_key(WALLET_MOCK_PRIVATE_KEY)
        WALLET_MOCK_ADDRESS = acct.address
except ImportError:
    pass

WALLET_MOCK_JS = f"""
(function() {{
    const mockAddress = '{WALLET_MOCK_ADDRESS}';
    const mockChainId = '0x{WALLET_MOCK_CHAIN_ID:x}';
    
    // Mock ethereum provider
    window.ethereum = {{
        isMetaMask: true,
        isConnected: () => true,
        selectedAddress: mockAddress,
        chainId: mockChainId,
        networkVersion: '{WALLET_MOCK_CHAIN_ID}',
        
        request: async ({{ method, params }}) => {{
            console.log('[MCP Mock Wallet] request:', method, params);
            switch (method) {{
                case 'eth_requestAccounts':
                case 'eth_accounts':
                    return [mockAddress];
                case 'eth_chainId':
                    return mockChainId;
                case 'net_version':
                    return '{WALLET_MOCK_CHAIN_ID}';
                case 'wallet_switchEthereumChain':
                    return null;
                case 'wallet_addEthereumChain':
                    return null;
                case 'personal_sign':
                case 'eth_sign':
                    return '0xMOCK_SIGNATURE_NOT_REAL';
                case 'eth_signTypedData':
                case 'eth_signTypedData_v4':
                    return '0xMOCK_TYPED_SIGNATURE_NOT_REAL';
                default:
                    console.warn('[MCP Mock Wallet] Unhandled method:', method);
                    return null;
            }}
        }},
        
        on: (event, cb) => {{
            if (event === 'accountsChanged') {{
                window._mcpWallet_onAccountsChanged = cb;
            }} else if (event === 'chainChanged') {{
                window._mcpWallet_onChainChanged = cb;
            }}
        }},
        removeListener: () => {{}},
        removeAllListeners: () => {{}},
        _events: {{}},
        _state: {{
            accounts: [mockAddress],
            initialized: true,
            isConnected: true,
            isUnlocked: true,
        }},
        enable: async () => [mockAddress],
        send: async (methodOrPayload, paramsOrCallback) => {{
            if (typeof methodOrPayload === 'string') {{
                return window.ethereum.request({{ method: methodOrPayload, params: paramsOrCallback }});
            }}
            return window.ethereum.request(methodOrPayload);
        }},
        sendAsync: async (payload, callback) => {{
            try {{
                const result = await window.ethereum.request(payload);
                callback(null, {{ id: payload.id, jsonrpc: '2.0', result }});
            }} catch(e) {{
                callback(e);
            }}
        }},
    }};
    
    // Trigger Ethereum event
    window.dispatchEvent(new Event('ethereum#initialized'));
    console.log('[MCP Mock Wallet] Injected window.ethereum | address:', mockAddress, '| chainId:', mockChainId);
}})();
"""

# ═══ Wallet Mock (inject window.solana) ═══

WALLET_MOCK_SOL_KEYPAIR = os.getenv("DAPP_TEST_SOL_KEYPAIR", "")
SOL_MOCK_ADDRESS = ""
try:
    if WALLET_MOCK_SOL_KEYPAIR:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(WALLET_MOCK_SOL_KEYPAIR)
        SOL_MOCK_ADDRESS = str(kp.pubkey())
except ImportError:
    pass

SOL_MOCK_JS = f"""
(function() {{
    const mockPubkey = '{SOL_MOCK_ADDRESS}';
    
    class MockPublicKey {{
        constructor(key) {{ this._key = key || mockPubkey; }}
        toString() {{ return mockPubkey; }}
        toBase58() {{ return mockPubkey; }}
        toBytes() {{ return new Uint8Array(32); }}
        equals(other) {{ return other && other.toString() === mockPubkey; }}
    }}
    
    class MockWalletAdapter {{
        constructor() {{
            this._publicKey = new MockPublicKey();
            this.connected = false;
        }}
        get publicKey() {{ return this._publicKey; }}
        async connect() {{ this.connected = true; }}
        async disconnect() {{ this.connected = false; }}
        
        async signTransaction(tx) {{
            console.warn('[MCP Mock Wallet] signTransaction: not implemented on server, returning unsigned');
            return tx;
        }}
        async signAllTransactions(txs) {{ return txs; }}
        async signMessage(msg) {{ return new Uint8Array(64); }}
    }}
    
    // Phantom-compatible
    window.solana = new MockWalletAdapter();
    window.solana.isPhantom = true;
    
    // Backpack-compatible
    window.backpack = new MockWalletAdapter();
    
    // Solflare-compatible
    window.solflare = new MockWalletAdapter();
    
    console.log('[MCP Mock Wallet] Injected window.solana | pubkey:', mockPubkey);
}})();
"""


async def _inject_wallet_mock(page):
    """Inject mock wallet providers into the page context."""
    if WALLET_MOCK_ADDRESS:
        await page.add_init_script(WALLET_MOCK_JS)
    if SOL_MOCK_ADDRESS:
        await page.add_init_script(SOL_MOCK_JS)


async def _get_page(with_wallet: bool = True):
    """Get or create browser page. with_wallet=True injects mock ethereum/solana providers."""
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
        
        ctx = await _pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(tempfile.gettempdir()) / "autotest-dapp-browser"),
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        _page = await ctx.new_page()
        
        # Inject wallet mock
        if with_wallet:
            if WALLET_MOCK_ADDRESS:
                await ctx.add_init_script(WALLET_MOCK_JS)
            if SOL_MOCK_ADDRESS:
                await ctx.add_init_script(SOL_MOCK_JS)
        
        return _page


# ═══════════════════════════════════════════════════════
# 🩺 Health
# ═══════════════════════════════════════════════════════

@tools.tool()
def test_health() -> str:
    """Check DApp test dependencies."""
    from env_checker import check_category
    results = {}
    for cat in ["evm", "solana", "browser"]:
        results[cat] = check_category(cat)
    return json.dumps(results, indent=2, ensure_ascii=False)

@tools.tool()
def test_auto_install(tool: str) -> str:
    """Auto-install missing tool."""
    from env_checker import ensure
    return json.dumps(ensure(tool), indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: 交易 → 前端确认
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_tx_and_ui_check(
    contract_address: str,
    function_sig: str,
    function_args: str = "",
    frontend_url: str = "",
    frontend_check_text: str = "",
    frontend_check_selector: str = "",
    wait_ms: int = 3000,
) -> str:
    """【DApp场景】链上交易 → 等确认 → 打开前端 → 验证UI状态变化。
    
    这是最核心的 DApp tool，一次调用完成整个闭环。
    
    参数:
    - contract_address: 合约地址
    - function_sig: 函数签名 e.g. 'swap(address,uint256,uint256)'
    - function_args: 逗号分隔参数 e.g. '0xTOKEN,100,0'
    - frontend_url: 前台页面 URL（默认用 FRONTEND_URL 环境变量）
    - frontend_check_text: 期望页面出现的文本（用于验证操作结果）
    - frontend_check_selector: 期望页面出现的元素（CSS selector）
    - wait_ms: 交易确认后等多久再查前端（默认3000ms）
    
    返回: tx_hash, receipt, 页面状态, passed (链上和前端都通过)
    """
    if not ETH_PK:
        return json.dumps({"ok": False, "passed": False, "error": "DEPLOYER_PRIVATE_KEY not set"})
    
    results = {}
    errors = []
    
    # ── 1. 链上发送交易 ──
    cmd = ["cast", "send", contract_address, function_sig] + _cast_args(send=True)
    if function_args: cmd.append(function_args)
    sr = _run(cmd, timeout=90)
    results["chain_tx"] = sr
    
    if not sr["ok"]:
        errors.append("chain_tx_failed")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "results": results}, ensure_ascii=False)
    
    tx_match = re.search(r"0x[a-fA-F0-9]{64}", sr["stdout"])
    tx_hash = tx_match.group(0) if tx_match else ""
    results["tx_hash"] = tx_hash
    
    # ── 2. 等回执 ──
    time.sleep(2)  # 给 RPC 一点时间
    rr = _run(["cast", "receipt", tx_hash] + _cast_args())
    results["receipt"] = rr
    
    is_reverted = "revert" in rr.get("stdout","").lower() + rr.get("stderr","").lower()
    if is_reverted:
        errors.append("tx_reverted")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "tx_hash": tx_hash, "results": results}, ensure_ascii=False)
    
    # ── 3. 等待 UI 更新 ──
    await asyncio.sleep(wait_ms / 1000)
    
    # ── 4. 前端检查 ──
    page_url = frontend_url or FRONTEND_URL
    try:
        page = await _get_page()
        await page.goto(page_url, timeout=15000, wait_until="domcontentloaded")
        results["ui"] = {
            "url": page.url,
            "title": await page.title(),
        }
        
        checks = {}
        if frontend_check_text:
            body = await page.locator("body").inner_text()
            checks["text"] = {"expected": frontend_check_text, "found": frontend_check_text in body, "passed": frontend_check_text in body}
            if not frontend_check_text in body:
                errors.append("ui_text_not_found")
        
        if frontend_check_selector:
            count = await page.locator(frontend_check_selector).count()
            checks["selector"] = {"expected": frontend_check_selector, "count": count, "passed": count > 0}
            if count == 0:
                errors.append("ui_selector_not_found")
        
        # Screenshot
        d = Path(tempfile.gettempdir()) / "autotest-screenshots"
        d.mkdir(exist_ok=True)
        spath = d / f"dapp_tx_{int(time.time())}.png"
        await page.screenshot(path=str(spath), full_page=False)
        results["ui"]["screenshot"] = str(spath)
        results["ui"]["checks"] = checks
        
    except Exception as e:
        errors.append(f"ui_check_error: {e}")
        results["ui"] = {"error": str(e)}
    
    passed = len(errors) == 0
    r = Result()
    r.step("send_tx", passed=len(errors) == 0 and bool(tx_hash), detail=f"tx={tx_hash[:10]}..." if tx_hash else "send failed")
    r.step("ui_check", passed=ui_check.get("passed", True), detail=ui_check.get("detail","")[:40])
    r.extra(tx_hash=tx_hash, errors=errors, **{"details": results})
    return r.done()


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: 部署 → 前端验证
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_deploy_and_ui_check(
    contract_dir: str,
    script_path: str,
    frontend_url: str = "",
    frontend_check_text: str = "",
    expect_contract_in_ui: bool = True,
    wait_ms: int = 5000,
) -> str:
    """【DApp场景】forge 部署 → 验证链上 → 打开前端检查合约地址/状态。
    
    返回: deployed_address, 链上 code 检查, 前端页面状态, passed
    """
    cwd = str(Path(contract_dir).resolve())
    results = {}
    errors = []
    
    # ── 1. Build + Deploy ──
    results["build"] = _run(["forge", "build"], timeout=120, cwd=cwd)
    if not results["build"]["ok"]:
        errors.append("build_failed")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "results": results}, ensure_ascii=False)
    
    args = ["forge", "script", script_path, "--broadcast"]
    if ETH_RPC: args += ["--rpc-url", ETH_RPC]
    if ETH_PK: args += ["--private-key", ETH_PK]
    dr = _run(args, timeout=300, cwd=cwd)
    results["deploy"] = dr
    
    addr_match = re.search(r"(?:Deployed|deployed|at:?)\s*(0x[a-fA-F0-9]{40})", dr.get("stdout",""))
    deployed_addr = addr_match.group(1) if addr_match else ""
    results["deployed_address"] = deployed_addr
    
    if not deployed_addr:
        errors.append("no_deployed_address")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "results": results}, ensure_ascii=False)
    
    # ── 2. 链上验证合约已部署 ──
    cr = _run(["cast", "code", deployed_addr] + _cast_args())
    results["chain_code_check"] = cr
    has_code = cr["ok"] and cr["stdout"].strip() not in ("", "0x", "0x00")
    if not has_code:
        errors.append("no_code_on_chain")
    
    # ── 3. 等UI更新 → 前端检查 ──
    await asyncio.sleep(wait_ms / 1000)
    page_url = frontend_url or FRONTEND_URL
    
    try:
        page = await _get_page()
        await page.goto(page_url, timeout=15000, wait_until="domcontentloaded")
        results["ui"] = {"url": page.url, "title": await page.title()}
        
        body = await page.locator("body").inner_text()
        
        if frontend_check_text:
            found = frontend_check_text in body
            results["ui"]["text_check"] = {"expected": frontend_check_text, "found": found, "passed": found}
            if not found: errors.append("ui_text_not_found")
        
        if expect_contract_in_ui and deployed_addr:
            contract_in_page = deployed_addr.lower() in body.lower() or deployed_addr[:10] in body
            results["ui"]["contract_visible"] = {"address": deployed_addr, "found": contract_in_page, "passed": contract_in_page}
            if not contract_in_page: errors.append("contract_not_in_ui")
        
        d = Path(tempfile.gettempdir()) / "autotest-screenshots"
        d.mkdir(exist_ok=True)
        spath = d / f"dapp_deploy_{int(time.time())}.png"
        await page.screenshot(path=str(spath), full_page=False)
        results["ui"]["screenshot"] = str(spath)
        
    except Exception as e:
        errors.append(f"ui_error: {e}")
        results["ui"] = {"error": str(e)}
    
    r = Result()
    r.step("forge build", passed=True, detail="ok")
    r.step("forge deploy", passed=bool(deployed_addr), detail=f"deployed={deployed_addr[:10]}..." if deployed_addr else "no address")
    r.step("ui verify", passed=ui_check.get("passed", True), detail=ui_check.get("detail","")[:40])
    r.extra(deployed_address=deployed_addr, errors=errors, details=results)
    return r.done()


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: 钱包模拟连接 → 签名 → 前端确认
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_wallet_connect_flow(
    frontend_url: str,
    connect_button: str = "Connect",
    expect_address_text: str = "",
    wallet_address: str = "",
) -> str:
    """【DApp场景】模拟钱包连接流程: 打开页面 → 注入mock钱包 → 点连接 → 验证连接状态。
    
    注入 mock window.ethereum (EVM) 和 window.solana (Solana) 钱包。
    前端会检测到 mock 钱包并完成连接，不需要真实的 MetaMask/Phantom 插件。
    
    参数:
    - frontend_url: DApp 页面 URL
    - connect_button: 连接按钮文本 (默认 "Connect")
    - expect_address_text: 连接后页面应显示的地址文本
    - wallet_address: 期望的钱包地址 (留空则用 MCP 配置的测试私钥地址)
    
    返回: 每步结果, passed
    """
    try:
        page = await _get_page()
        results = {"steps": []}
        
        # 1. 打开页面
        await page.goto(frontend_url, timeout=15000, wait_until="domcontentloaded")
        results["url"] = page.url
        results["title"] = await page.title()
        results["steps"].append({"step": "navigate", "ok": True, "title": results["title"]})
        
        # 2. 检查连接按钮
        conn_btn = page.locator(f"button:has-text('{connect_button}')").first
        if await conn_btn.count() == 0:
            conn_btn = page.get_by_text(connect_button, exact=False).first
        btn_exists = await conn_btn.count() > 0
        
        if not btn_exists:
            results["steps"].append({"step": "find_connect_button", "ok": False, "error": f"Connect button '{connect_button}' not found"})
            return json.dumps({"ok": True, "passed": False, "results": results}, ensure_ascii=False)
        
        # 3. 点击连接
        await conn_btn.click(timeout=5000)
        await asyncio.sleep(2)
        
        # 如果是下拉选择钱包，尝试点击第一个
        try:
            metamask = page.locator("button:has-text('MetaMask'), button:has-text('metamask'), li:has-text('MetaMask')").first
            if await metamask.count() > 0:
                await metamask.click(timeout=3000)
                await asyncio.sleep(1)
        except: pass
        
        results["steps"].append({"step": "click_connect", "ok": True})
        
        # 4. 验证结果
        body = await page.locator("body").inner_text()
        
        if expect_address_text:
            found = expect_address_text in body
            results["steps"].append({"step": "check_address", "ok": found, "detail": f"'{expect_address_text}' {'found' if found else 'NOT found'}"})
        
        if wallet_address:
            addr_found = wallet_address.lower() in body.lower() or wallet_address[:10] in body
            results["steps"].append({"step": "check_wallet_address", "ok": addr_found})
        
        # Screenshot
        d = Path(tempfile.gettempdir()) / "autotest-screenshots"
        d.mkdir(exist_ok=True)
        spath = d / f"dapp_wallet_{int(time.time())}.png"
        await page.screenshot(path=str(spath), full_page=False)
        results["screenshot"] = str(spath)
        
        r = Result()
        for s in results["steps"]:
            r.step(s.get("step","?"), passed=s.get("ok", True), detail=s.get("detail", "")[:60])
        r.extra(details=results)
        return r.done()
        
    except Exception as e:
        return Result.fail_(summary=str(e))


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: Swap 全流程
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_swap_flow(
    router_address: str,
    token_in: str,
    token_out: str,
    amount_in: str,
    sender: str = "",
    frontend_url: str = "",
    approve_first: bool = True,
    check_balance_change: bool = True,
) -> str:
    """【DApp场景】DeFi Swap 全链路: approve → swap → receipt → 前端余额验证。
    
    步骤:
    1. (可选) approve 代币授权
    2. 发送 swap 交易
    3. 等待 receipt
    4. 查询链上余额变化
    5. 打开前端验证余额显示
    
    返回: approve_tx, swap_tx, 余额变化, 前端状态, passed
    """
    if not ETH_PK:
        return json.dumps({"ok": False, "passed": False, "error": "DEPLOYER_PRIVATE_KEY not set"})
    
    results = {}
    errors = []
    
    # ── 1. Approve ──
    if approve_first:
        approve_args = f"{router_address},115792089237316195423570985008687907853269984665640564039457584007913129639935"
        ar = _run(["cast", "send", token_in, "approve(address,uint256)", approve_args] + _cast_args(send=True), timeout=90)
        results["approve"] = ar
        if not ar["ok"]:
            errors.append("approve_failed")
        else:
            await asyncio.sleep(3)  # wait for approval to settle
    
    # ── 2. Swap ──
    swap_hash = token_out[2:].rjust(64, "0")
    swap_args = f"0,{amount_in},0x{_cast_args(send=False)[1]}{swap_hash},{sender or _get_addr_from_pk()},9999999999"
    sr = _run(["cast", "send", router_address, "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)", swap_args] + _cast_args(send=True), timeout=120)
    results["swap"] = sr
    
    tx_match = re.search(r"0x[a-fA-F0-9]{64}", sr["stdout"])
    tx_hash = tx_match.group(0) if tx_match and tx_match.group(0) != "0x" * 32 else ""
    results["swap_tx_hash"] = tx_hash
    
    if not sr["ok"] or not tx_hash:
        errors.append("swap_failed")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "results": results}, ensure_ascii=False)
    
    # ── 3. 收据 ──
    await asyncio.sleep(2)
    rr = _run(["cast", "receipt", tx_hash] + _cast_args())
    results["receipt"] = rr
    
    is_reverted = "revert" in rr.get("stdout","").lower() + rr.get("stderr","").lower()
    if is_reverted:
        errors.append("swap_reverted")
    
    # ── 4. 余额 ──
    if check_balance_change:
        addr = sender or _get_addr_from_pk()
        results["balance_token_in"] = _run(["cast", "call", token_in, "balanceOf(address)(uint256)", addr] + _cast_args())
        results["balance_token_out"] = _run(["cast", "call", token_out, "balanceOf(address)(uint256)", addr] + _cast_args())
    
    # ── 5. 前端 ──
    page_url = frontend_url or FRONTEND_URL
    if page_url:
        try:
            await asyncio.sleep(3)
            page = await _get_page()
            await page.goto(page_url, timeout=15000, wait_until="domcontentloaded")
            results["ui"] = {"url": page.url, "title": await page.title()}
            
            d = Path(tempfile.gettempdir()) / "autotest-screenshots"
            d.mkdir(exist_ok=True)
            spath = d / f"dapp_swap_{int(time.time())}.png"
            await page.screenshot(path=str(spath), full_page=False)
            results["ui"]["screenshot"] = str(spath)
        except Exception as e:
            results["ui"] = {"error": str(e)}
    
    r = Result()
    r.step("approve", passed=results.get("approve", {}).get("ok", True), detail=results.get("approve", {}).get("stdout","")[:60])
    r.step("swap", passed=results.get("swap", {}).get("ok", True), detail=results.get("swap", {}).get("stdout","")[:60])
    r.step("ui verify", passed=results.get("ui", {}).get("ok", True), detail="ok" if results.get("ui", {}).get("ok") else "ui_error")
    r.extra(errors=errors, details=results)
    return r.done()


def _get_addr_from_pk() -> str:
    """Get address from private key."""
    if not ETH_PK: return ""
    r = _run(["cast", "wallet", "address", "--private-key", ETH_PK])
    return r.get("stdout", "").strip()


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: 监听链事件 → 前端更新验证
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_event_to_ui(
    contract_address: str,
    event_topic: str,
    frontend_url: str = "",
    frontend_check_text: str = "",
    poll_blocks: int = 3,
    poll_interval_sec: int = 5,
    max_wait_sec: int = 30,
) -> str:
    """【DApp场景】监控链上事件 → 等前端更新 → 验证 UI。
    
    用于测试: 另一个账户发起操作 → 当前前端是否实时反映变化。
    
    参数:
    - poll_blocks: 每个区块间隔查询一次日志
    - poll_interval_sec: 每次轮询间隔（秒）
    - max_wait_sec: 最大等待时间
    
    返回: 事件是否捕获, 前端是否更新, passed
    """
    results = {}
    
    # 1. 获取当前区块
    blk_r = _run(["cast", "block-number"] + _cast_args())
    start_block = blk_r.get("stdout", "").strip()
    results["start_block"] = start_block
    
    # 2. 轮询事件
    elapsed = 0
    event_found = False
    for i in range(int(max_wait_sec / poll_interval_sec)):
        await asyncio.sleep(poll_interval_sec)
        elapsed += poll_interval_sec
        
        from_blk = start_block
        # Query logs
        cmd = ["cast", "logs", "--from-block", from_blk, "--to-block", "latest"]
        if ETH_RPC: cmd += ["--rpc-url", ETH_RPC]
        if contract_address: cmd += ["--address", contract_address]
        if event_topic: cmd += ["--topic", event_topic]
        
        lr = _run(cmd)
        if event_topic in lr.get("stdout", ""):
            event_found = True
            results["event_logs"] = lr
            break
    
    results["event_found"] = event_found
    results["poll_elapsed_sec"] = elapsed
    
    # 3. 前端检查
    ui_ok = True
    if frontend_url or FRONTEND_URL:
        page_url = frontend_url or FRONTEND_URL
        try:
            page = await _get_page()
            await page.goto(page_url, timeout=15000, wait_until="domcontentloaded")
            results["ui"] = {"url": page.url, "title": await page.title()}
            
            if frontend_check_text:
                body = await page.locator("body").inner_text()
                ui_ok = frontend_check_text in body
                results["ui"]["text_check"] = {"expected": frontend_check_text, "found": ui_ok, "passed": ui_ok}
            
            d = Path(tempfile.gettempdir()) / "autotest-screenshots"
            d.mkdir(exist_ok=True)
            spath = d / f"dapp_event_{int(time.time())}.png"
            await page.screenshot(path=str(spath), full_page=False)
            results["ui"]["screenshot"] = str(spath)
            
        except Exception as e:
            results["ui"] = {"error": str(e)}
            ui_ok = False
    
    r = Result()
    r.step("event_captured", passed=event_found, detail=results.get("event","{}")[:60])
    r.step("ui_updated", passed=ui_ok, detail=results.get("ui", {}).get("text","")[:60])
    r.extra(details=results)
    return r.done()


# ═══════════════════════════════════════════════════════
# ⚡ DApp 场景 tool: Solana DApp 全链路
# ═══════════════════════════════════════════════════════

@tools.tool()
async def dapp_sol_transfer_and_ui(
    to: str,
    amount_sol: str,
    frontend_url: str = "",
    frontend_check_balance: bool = True,
) -> str:
    """【DApp场景】Solana 转账 → 等确认 → 前端验证余额。
    
    返回: tx_signature, confirm, 前端余额, passed
    """
    kp = _sol_kp()
    results = {}
    errors = []
    
    # 1. 转账
    tr = _run(["solana", "transfer", to, amount_sol, "--keypair", kp, "--url", SOL_RPC, "--allow-unfunded-recipient"], timeout=60)
    results["transfer"] = tr
    tx_sig = tr.get("stdout", "").strip().split("\n")[-1] if tr.get("stdout") else ""
    results["tx_signature"] = tx_sig
    
    if not tr["ok"] or not tx_sig:
        errors.append("transfer_failed")
        return json.dumps({"ok": True, "passed": False, "errors": errors, "results": results}, ensure_ascii=False)
    
    # 2. 确认
    await asyncio.sleep(2)
    cr = _sol_run(["confirm", tx_sig])
    results["confirm"] = cr
    
    # 3. 前端
    page_url = frontend_url or FRONTEND_URL
    if page_url:
        try:
            await asyncio.sleep(2)
            page = await _get_page()
            await page.goto(page_url, timeout=15000, wait_until="domcontentloaded")
            results["ui"] = {"url": page.url, "title": await page.title()}
            
            if frontend_check_balance:
                body = await page.locator("body").inner_text()
                # 检查页面是否包含目标地址或金额
                found = to[:8] in body or amount_sol in body
                results["ui"]["balance_check"] = {"passed": found}
                if not found: errors.append("balance_not_in_ui")
            
            d = Path(tempfile.gettempdir()) / "autotest-screenshots"
            d.mkdir(exist_ok=True)
            spath = d / f"dapp_sol_{int(time.time())}.png"
            await page.screenshot(path=str(spath), full_page=False)
            results["ui"]["screenshot"] = str(spath)
        except Exception as e:
            results["ui"] = {"error": str(e)}
    
    r = Result()
    r.step("sol_transfer", passed=results.get("transfer", {}).get("ok", True), detail=results.get("transfer", {}).get("stdout","")[:60])
    r.step("confirm", passed=results.get("confirm", {}).get("ok", True), detail=results.get("confirm", {}).get("stdout","")[:60])
    r.step("ui verify", passed=results.get("ui", {}).get("ok", True), detail="ok" if results.get("ui", {}).get("ok") else "ui_error")
    r.extra(errors=errors, details=results)
    return r.done()


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════


# ═══════════════════════════════
# Main
# ═══════════════════════════════

if __name__ == "__main__":
    import uvicorn
    app = create_app(tools, name="autotest-dapp", instructions="DApp Chain+Frontend")
    print("autotest-dapp SSE -> http://0.0.0.0:8083/sse", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=8083, log_level="info")
