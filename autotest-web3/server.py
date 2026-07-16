#!/usr/bin/env python3
"""autotest-web3 MCP Server — EVM + Solana + Security"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import sys
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

# ═══ Helpers ═══

def _run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> dict:
    """Run subprocess, auto-redact ETH_PK."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        out = r.stdout; err = r.stderr
        if ETH_PK:
            out = out.replace(ETH_PK, "***")
        return {"ok": r.returncode == 0, "stdout": out.strip()[:8000], "stderr": err.strip()[:2000], "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": f"{cmd[0]} not found"}

def _cast_call_args() -> list[str]:
    return ["--rpc-url", ETH_RPC] if ETH_RPC else []

def _cast_send_args() -> list[str]:
    a = _cast_call_args()
    if ETH_PK: a += ["--private-key", ETH_PK]
    return a

def _sol_run(args: list[str], timeout: int = 60) -> dict:
    cmd = ["solana"] + args
    if "--url" not in " ".join(args): cmd += ["--url", SOL_RPC]
    return _run(cmd, timeout=timeout)

def _sol_kp() -> str:
    return SOL_KEY or os.path.expanduser("~/.config/solana/id.json")


# ═══════════════════════════════════════════════════════
# 🩺 Health (2)
# ═══════════════════════════════════════════════════════

@tools.tool()
def test_health() -> str:
    """Check all web3 tool dependencies are installed and working."""
    from env_checker import check_category
    results = {}
    for cat in ["evm", "solana", "security"]:
        results[cat] = check_category(cat)
    return json.dumps(results, indent=2, ensure_ascii=False)

@tools.tool()
def test_auto_install(tool: str) -> str:
    """Auto-install a missing tool. e.g. test_auto_install('solana')"""
    from env_checker import ensure
    return json.dumps(ensure(tool), indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: EVM 交易全流程 (send → receipt → logs → assert)
# ═══════════════════════════════════════════════════════

@tools.tool()
def evm_tx_and_verify(
    address: str,
    signature: str,
    args: str = "",
    value_wei: str = "0",
    expect_event: str = "",
    expect_revert: bool = False,
) -> str:
    """【场景tool】发送交易 → 等待回执 → 验证结果。一次调完。
    
    expect_event: 期望的事件 topic hash（可选）
    expect_revert: 期望交易回滚（默认false）
    
    返回值包含: tx_hash, receipt, status, event_check, 综合结论 passed
    """
    if err := rate_limit("evm_tx_and_verify"): return err
    if not ETH_PK:
        return json.dumps({"ok": False, "passed": False, "error": "DEPLOYER_PRIVATE_KEY not set"})
    
    # 1. Send
    cmd = ["cast", "send", address, signature] + _cast_send_args()
    if args: cmd.append(args)
    if value_wei and value_wei != "0": cmd += ["--value", value_wei]
    sr = _run(cmd, timeout=90)
    
    if not sr["ok"]:
        return json.dumps({"ok": True, "passed": False, "step": "send", "error": sr.get("stderr") or sr.get("error"), "detail": sr}, ensure_ascii=False)
    
    # Extract tx hash
    tx_match = re.search(r"0x[a-fA-F0-9]{64}", sr["stdout"])
    tx_hash = tx_match.group(0) if tx_match else ""
    
    # 2. Receipt
    rr = _run(["cast", "receipt", tx_hash] + _cast_call_args())
    
    # 3. Verify
    checks = {"tx_hash": tx_hash, "sent": sr["ok"]}
    
    # Check revert
    if expect_revert:
        is_reverted = "reverted" in rr.get("stdout","").lower() or "revert" in rr.get("stderr","").lower()
        if '"status"' in rr.get("stdout",""):
            is_reverted = '"status": "0x0"' in rr["stdout"] or '"status":"0x0"' in rr["stdout"]
        checks["revert_check"] = {"expected": True, "actual": is_reverted, "passed": is_reverted}
    else:
        checks["revert_check"] = {"expected": False, "passed": rr["ok"]}
    
    # Check event
    if expect_event:
        ev_passed = expect_event in rr.get("stdout", "")
        checks["event_check"] = {"topic": expect_event, "found": ev_passed, "passed": ev_passed}
    
    # 4. Logs (optional - only if event check needed)
    if expect_event and not checks.get("event_check", {}).get("passed", True):
        lr = _run(["cast", "logs", "--from-block", "latest", "--to-block", "latest", "--address", address] + _cast_call_args())
        checks["logs_fallback"] = lr
    
    passed = all(
        checks.get(k, {}).get("passed", True) if isinstance(checks.get(k), dict) else bool(checks.get(k, True))
        for k in ["revert_check", "event_check"]
    )
    
    r = Result()
    r.step("send_tx", passed=sr["ok"], detail=f"tx={tx_hash[:10]}...")
    if expect_revert:
        r.step("expect_revert", passed=checks.get("revert_check", {}).get("passed", True),
               detail="reverted" if checks.get("revert_check", {}).get("actual") else "NOT reverted")
    else:
        r.step("tx_confirmed", passed=rr["ok"], detail="receipt ok" if rr["ok"] else rr.get("stderr","")[:60])
    if expect_event:
        r.step("event_check", passed=checks.get("event_check", {}).get("passed", True),
               detail=expect_event[:20])
    r.extra(tx_hash=tx_hash, checks=checks, receipt_summary=rr.get("stdout", "")[:2000])
    return r.done()


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: EVM 合约部署 + 验证
# ═══════════════════════════════════════════════════════

@tools.tool()
def evm_contract_test(
    contract_dir: str,
    match_contract: str = "",
    run_slither: bool = False,
) -> str:
    """【场景tool】一键合约测试: forge build → forge test → (可选) slither 审计。
    
    返回值: build 结果, test 结果 (pass/fail + 数量), slither 结果
    """
    cwd = str(Path(contract_dir).resolve())
    results = {}
    
    # 1. build
    results["build"] = _run(["forge", "build"], timeout=120, cwd=cwd)
    
    # 2. test
    test_args = ["forge", "test", "-vvv"]
    if match_contract:
        test_args += ["--match-contract", match_contract]
    tr = _run(test_args, timeout=180, cwd=cwd)
    # Parse test summary
    passed_match = re.search(r"Test result:.*?(\d+)\s+passed.*?(\d+)\s+failed", tr.get("stdout",""), re.DOTALL)
    results["test"] = tr
    if passed_match:
        results["test_summary"] = {"passed": int(passed_match.group(1)), "failed": int(passed_match.group(2))}
    
    # 3. slither (optional)
    if run_slither:
        sr = _run(["slither", "."], timeout=120, cwd=cwd)
        slither_issues = len(re.findall(r"^\S+\.sol:\d+", sr.get("stdout",""), re.MULTILINE))
        results["slither"] = sr
        results["slither_summary"] = {"issues_found": slither_issues}
    
    passed = results["build"]["ok"] and results["test"]["ok"]
    
    r = Result()
    r.step("forge build", passed=results["build"]["ok"],
           detail=results["build"].get("stderr", "")[:80] if not results["build"]["ok"] else "ok")
    ts = results.get("test_summary", {})
    test_label = f"{ts.get('passed',0)}P/{ts.get('failed',0)}F" if ts else "unknown"
    r.step("forge test", passed=results["test"]["ok"], detail=test_label)
    if run_slither:
        ss = results.get("slither_summary", {})
        r.step("slither audit", passed=ss.get("issues_found", 0) == 0,
               detail=f"{ss.get('issues_found',0)} issues")
    r.extra(**{"details": results})
    return r.done()


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: Forge 部署 + 验证
# ═══════════════════════════════════════════════════════

@tools.tool()
def evm_deploy_and_verify(
    contract_dir: str,
    script_path: str,
    contract_name: str = "",
    etherscan_key: str = "",
) -> str:
    """【场景tool】forge build → forge script broadcast → (可选) forge verify。
    
    返回: build 结果, deploy 结果 (合约地址), verify 结果
    """
    cwd = str(Path(contract_dir).resolve())
    results = {}
    
    # 1. Build
    results["build"] = _run(["forge", "build"], timeout=120, cwd=cwd)
    if not results["build"]["ok"]:
        return json.dumps({"ok": True, "passed": False, "step": "build", "results": results}, ensure_ascii=False)
    
    # 2. Deploy
    args = ["forge", "script", script_path, "--broadcast"]
    if ETH_RPC: args += ["--rpc-url", ETH_RPC]
    if ETH_PK: args += ["--private-key", ETH_PK]
    dr = _run(args, timeout=300, cwd=cwd)
    results["deploy"] = dr
    
    # Extract deployed address
    addr_match = re.search(r"(?:Deployed|deployed|at:?)\s*(0x[a-fA-F0-9]{40})", dr.get("stdout",""))
    deployed_addr = addr_match.group(1) if addr_match else ""
    results["deployed_address"] = deployed_addr
    
    # 3. Verify (optional)
    if contract_name and deployed_addr:
        key = etherscan_key or os.getenv("ETHERSCAN_KEY", "")
        vargs = ["forge", "verify-contract", deployed_addr, contract_name]
        if key: vargs += ["--etherscan-api-key", key]
        if ETH_RPC: vargs += ["--rpc-url", ETH_RPC]
        results["verify"] = _run(vargs, timeout=60, cwd=cwd)
    
    passed = results["build"]["ok"] and results["deploy"]["ok"] and bool(deployed_addr)
    
    r = Result()
    r.step("forge build", passed=results["build"]["ok"], detail="ok" if results["build"]["ok"] else results["build"].get("stderr","")[:80])
    r.step("forge deploy", passed=results["deploy"]["ok"] and bool(deployed_addr),
           detail=f"deployed={deployed_addr[:10]}..." if deployed_addr else "no address found")
    if contract_name and deployed_addr:
        v = results.get("verify", {})
        r.step("verify", passed=v.get("ok", False), detail=v.get("stdout","")[:100])
    r.extra(deployed_address=deployed_addr, **{"details": results})
    return r.done()


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: Solana 全流程
# ═══════════════════════════════════════════════════════

@tools.tool()
def sol_deploy_and_test(project_dir: str) -> str:
    """【场景tool】anchor build → anchor test → deploy。一次调完。"""
    cwd = str(Path(project_dir).resolve())
    results = {}
    
    results["build"] = _run(["anchor", "build"], timeout=120, cwd=cwd)
    results["test"] = _run(["anchor", "test"], timeout=300, cwd=cwd)
    
    # Try deploy (may fail on localnet, that's ok)
    results["deploy"] = _run(["solana", "program", "deploy", f"{cwd}/target/deploy/*.so", "--keypair", _sol_kp(), "--url", SOL_RPC], timeout=120)
    
    passed = results["build"]["ok"] and results["test"]["ok"]
    
    r = Result()
    r.step("anchor build", passed=results["build"]["ok"], detail="ok" if results["build"]["ok"] else results["build"].get("stderr","")[:80])
    r.step("anchor test", passed=results["test"]["ok"], detail=results["test"].get("stdout","")[:200])
    d = results.get("deploy", {})
    r.step("deploy", passed=d.get("ok", False), detail=d.get("stdout","")[:100] if d.get("ok") else d.get("stderr","")[:100])
    r.extra(**{"details": results})
    return r.done()


@tools.tool()
def sol_transfer_and_confirm(to: str, amount_sol: str) -> str:
    """【场景tool】转账 SOL → 确认交易。返回 tx 签名 + 确认状态。"""
    kp = _sol_kp()
    
    # Transfer
    tr = _run(["solana", "transfer", to, amount_sol, "--keypair", kp, "--url", SOL_RPC, "--allow-unfunded-recipient"], timeout=60)
    if not tr["ok"]:
        return json.dumps({"ok": True, "passed": False, "step": "transfer", "detail": tr}, ensure_ascii=False)
    
    tx_sig = tr["stdout"].strip().split("\n")[-1] if tr["stdout"] else ""
    
    # Confirm
    cr = _sol_run(["confirm", tx_sig]) if tx_sig else {"ok": False, "error": "no signature"}
    
    passed = tr["ok"] and cr.get("ok", False)
    
    r = Result()
    r.step("transfer", passed=tr["ok"], detail=f"{amount_sol} SOL → {to[:8]}...")
    r.step("confirm", passed=cr.get("ok", False), detail=tx_sig[:12]+"..." if tx_sig else "no sig")
    r.extra(tx_signature=tx_sig, **{"details": {"transfer": tr, "confirm": cr}})
    return r.done()


# ═══════════════════════════════════════════════════════
# 🎯 场景 tool: 安全审计全流程
# ═══════════════════════════════════════════════════════

@tools.tool()
def security_audit(
    contract_dir: str,
    run_fuzz: bool = False,
    fuzz_contract: str = "",
    fuzz_limit: int = 5000,
) -> str:
    """【场景tool】一键安全审计: slither 静态分析 → (可选) echidna 模糊测试 → (可选) halmos 符号执行。
    
    返回: slither 问题数 + 严重性分布, fuzz 结果, halmos 结果
    """
    cwd = str(Path(contract_dir).resolve())
    results = {}
    
    # 1. Slither
    sr = _run(["slither", "."], timeout=180, cwd=cwd)
    results["slither"] = sr
    # Count findings by severity
    findings = {"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0}
    for sev in findings:
        findings[sev] = len(re.findall(rf"^\S+\.sol:\d+.*?{sev}", sr.get("stdout",""), re.MULTILINE))
    results["slither_summary"] = {"total_findings": sum(findings.values()), "by_severity": findings}
    
    # 2. Echidna (optional)
    if run_fuzz:
        eargs = ["echidna", "."]
        if fuzz_contract: eargs += ["--contract", fuzz_contract]
        eargs += ["--test-limit", str(fuzz_limit)]
        er = _run(eargs, timeout=600, cwd=cwd)
        results["echidna"] = er
        results["echidna_summary"] = {"broken": "💥" in er.get("stdout","") + er.get("stderr",""), "test_limit": fuzz_limit}
    
    # 3. Halmos
    if run_fuzz:
        hargs = ["halmos"]
        if fuzz_contract: hargs += ["--contract", fuzz_contract]
        hr = _run(hargs, timeout=300, cwd=cwd)
        results["halmos"] = hr
        h_issues = len(re.findall(r"(?:Counterexample|Violation|Assertion)", hr.get("stdout","") + hr.get("stderr","")))
        results["halmos_summary"] = {"issues": h_issues}
    
    passed = findings.get("High", 0) == 0 and findings.get("Medium", 0) == 0
    
    r = Result()
    sev_str = "/".join(f"{k[0]}{v}" for k, v in findings.items() if v > 0) or "0 issues"
    r.step("slither", passed=passed, detail=sev_str)
    if run_fuzz:
        es = results.get("echidna_summary", {})
        r.step("echidna fuzz", passed=not es.get("broken", False),
               detail=f"broken={'yes' if es.get('broken') else 'no'}, limit={es.get('test_limit')}")
    if run_fuzz:
        hs = results.get("halmos_summary", {})
        r.step("halmos", passed=hs.get("issues", 0) == 0, detail=f"{hs.get('issues',0)} issues")
    r.extra(**{"details": results})
    return r.done()


# ═══════════════════════════════════════════════════════
# ⚛️ 原子 tool: EVM 只读 (保留 — 高级场景用)
# ═══════════════════════════════════════════════════════

@tools.tool()
def evm_call(address: str, signature: str, args: str = "") -> str:
    """只读合约调用。高级场景用；常规场景用 evm_contract_test。"""
    cmd = ["cast", "call", address, signature] + _cast_call_args()
    if args: cmd.append(args)
    return json.dumps(_run(cmd), ensure_ascii=False)

@tools.tool()
def evm_balance(address: str) -> str:
    """ETH 余额 (wei)。"""
    return json.dumps(_run(["cast", "balance", address] + _cast_call_args()), ensure_ascii=False)

@tools.tool()
def evm_code(address: str) -> str:
    """合约字节码。空 = 非合约地址。"""
    return json.dumps(_run(["cast", "code", address] + _cast_call_args()), ensure_ascii=False)

@tools.tool()
def evm_block() -> str:
    """当前区块号。"""
    return json.dumps(_run(["cast", "block-number"] + _cast_call_args()), ensure_ascii=False)

@tools.tool()
def evm_storage(address: str, slot: str) -> str:
    """读取存储槽。"""
    return json.dumps(_run(["cast", "storage", address, slot] + _cast_call_args()), ensure_ascii=False)

@tools.tool()
def evm_receipt(tx_hash: str) -> str:
    """交易回执（含 logs + status）。"""
    return json.dumps(_run(["cast", "receipt", tx_hash] + _cast_call_args()), ensure_ascii=False)

@tools.tool()
def evm_logs(address: str = "", topics: str = "", from_block: str = "latest", to_block: str = "latest") -> str:
    """查询事件日志。topics = 逗号分隔的 topic hash。"""
    cmd = ["cast", "logs", "--from-block", from_block, "--to-block", to_block] + _cast_call_args()
    if address: cmd += ["--address", address]
    if topics:
        for t in topics.split(","): cmd += ["--topic", t.strip()]
    r = _run(cmd)
    if len(r.get("stdout", "")) > 8000: r["stdout"] = r["stdout"][:8000] + "\n...[truncated]"
    return json.dumps(r, ensure_ascii=False)

@tools.tool()
def evm_trace(tx_hash: str) -> str:
    """交易执行追踪。需要 archive node。"""
    r = _run(["cast", "run", tx_hash] + _cast_call_args(), timeout=90)
    if len(r.get("stdout", "")) > 8000: r["stdout"] = r["stdout"][:8000] + "\n...[truncated]"
    return json.dumps(r, ensure_ascii=False)

@tools.tool()
def evm_send(address: str, signature: str, args: str = "", value_wei: str = "0") -> str:
    """发送交易。⚠️ 常规场景用 evm_tx_and_verify（自动验证）。"""
    if err := rate_limit("evm_send"): return err
    if not ETH_PK:
        return json.dumps({"ok": False, "error": "DEPLOYER_PRIVATE_KEY not set"})
    cmd = ["cast", "send", address, signature] + _cast_send_args()
    if args: cmd.append(args)
    if value_wei and value_wei != "0": cmd += ["--value", value_wei]
    return json.dumps(_run(cmd, timeout=90), ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# ⚛️ 原子 tool: Solana (保留)
# ═══════════════════════════════════════════════════════

@tools.tool()
def sol_balance(address: str = "") -> str:
    """SOL 余额。"""
    if address: return json.dumps(_sol_run(["balance", address]), ensure_ascii=False)
    return json.dumps(_sol_run(["balance", "--keypair", _sol_kp()]), ensure_ascii=False)

@tools.tool()
def sol_account(address: str) -> str:
    """账户完整信息。"""
    return json.dumps(_sol_run(["account", address]), ensure_ascii=False)

@tools.tool()
def sol_transfer(to: str, amount_sol: str) -> str:
    """转账 SOL。⚠️ 常规用 sol_transfer_and_confirm（自动确认）。"""
    return json.dumps(_run(["solana", "transfer", to, amount_sol, "--keypair", _sol_kp(), "--url", SOL_RPC, "--allow-unfunded-recipient"], timeout=60), ensure_ascii=False)

@tools.tool()
def sol_program_deploy(program_path: str) -> str:
    """部署 Solana 程序。⚠️ 常规用 sol_deploy_and_test。"""
    return json.dumps(_run(["solana", "program", "deploy", program_path, "--keypair", _sol_kp(), "--url", SOL_RPC], timeout=120), ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════


# ═══════════════════════════════
# Main
# ═══════════════════════════════

if __name__ == "__main__":
    import uvicorn
    app = create_app(tools, name="autotest-web3", instructions="EVM/Solana/Security")
    print("autotest-web3 SSE -> http://0.0.0.0:8081/sse", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
