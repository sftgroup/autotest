#!/usr/bin/env python3
"""env_checker — shared environment detection and auto-install for autotest-mcp tools.

Usage:
    from env_checker import ensure, check, install_tool

    ensure("forge")    # 检查 forge 是否可用，不可用返回安装命令
    check("playwright") # 检查 playwright 浏览器是否已安装
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap

# ═══════════════════════════════════════════════════════
# Tool definitions
# ═══════════════════════════════════════════════════════

TOOLS: dict[str, dict] = {
    # ── Base chain ──
    "forge": {
        "category": "evm",
        "check_cmd": ["forge", "--version"],
        "check_ok": lambda o: "forge" in o,
        "install_cmd": "curl -L https://foundry.paradigm.xyz | bash && foundryup",
        "install_note": "Requires ~/.bashrc to be sourced after install",
    },
    "cast": {
        "category": "evm",
        "check_cmd": ["cast", "--version"],
        "check_ok": lambda o: "cast" in o,
        "install_cmd": "curl -L https://foundry.paradigm.xyz | bash && foundryup",
    },
    # ── Solana ──
    "solana": {
        "category": "solana",
        "check_cmd": ["solana", "--version"],
        "check_ok": lambda o: "solana-cli" in o,
        "install_cmd": 'sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"',
        "install_note": "Add ~/.local/share/solana/install/active_release/bin to PATH",
    },
    "anchor": {
        "category": "solana",
        "check_cmd": ["anchor", "--version"],
        "check_ok": lambda o: o.startswith("anchor-cli"),
        "install_cmd": "cargo install --git https://github.com/coral-xyz/anchor anchor-cli --locked",
        "requires": ["cargo"],
    },
    # ── Security ──
    "slither": {
        "category": "security",
        "check_cmd": ["slither", "--version"],
        "check_ok": lambda o: "slither" in o.lower(),
        "install_cmd": "pip3 install slither-analyzer",
        "requires": ["pip3", "solc"],
    },
    "echidna": {
        "category": "security",
        "check_cmd": ["echidna", "--version"],
        "check_ok": lambda o: "echidna" in o,
        "install_cmd": "nix-env -iA nixpkgs.echidna 2>/dev/null || (curl -L https://github.com/crytic/echidna/releases/latest/download/echidna-ubuntu-x86_64 -o /usr/local/bin/echidna && chmod +x /usr/local/bin/echidna)",
        "requires": [],
    },
    "halmos": {
        "category": "security",
        "check_cmd": ["halmos", "--version"],
        "check_ok": lambda o: "halmos" in o,
        "install_cmd": "pipx install halmos 2>/dev/null || pip3 install halmos",
        "requires": ["pip3"],
    },
    "medusa": {
        "category": "security",
        "check_cmd": ["medusa", "--version"],
        "check_ok": lambda o: "medusa" in o,
        "install_cmd": "npm install -g @crytic/medusa 2>/dev/null || echo 'Install from source: https://github.com/crytic/medusa'",
        "requires": ["go"],
    },
    # ── API ──
    "hurl": {
        "category": "api",
        "check_cmd": ["hurl", "--version"],
        "check_ok": lambda o: "hurl" in o,
        "install_cmd": "cargo install hurl 2>/dev/null || snap install hurl 2>/dev/null || (curl -sLO https://github.com/Orange-OpenSource/hurl/releases/download/6.1.0/hurl_6.1.0_amd64.deb && dpkg -i hurl_6.1.0_amd64.deb)",
        "requires": [],
    },
    "schemathesis": {
        "category": "api",
        "check_cmd": ["schemathesis", "--version"],
        "check_ok": lambda o: "schemathesis" in o,
        "install_cmd": "pip3 install schemathesis",
        "requires": ["pip3"],
    },
    "stepci": {
        "category": "api",
        "check_cmd": ["stepci", "--version"],
        "check_ok": lambda o: "stepci" in o,
        "install_cmd": "npm install -g stepci",
        "requires": ["npm"],
    },
    "autocannon": {
        "category": "api",
        "check_cmd": ["autocannon", "--version"],
        "check_ok": lambda o: "autocannon" in o,
        "install_cmd": "npm install -g autocannon",
        "requires": ["npm"],
    },
    # ── Browser ──
    "playwright": {
        "category": "browser",
        "check_cmd": ["python3", "-c", "from playwright.async_api import async_playwright; print('OK')"],
        "check_ok": lambda o: "OK" in o,
        "install_cmd": "pip3 install playwright && python3 -m playwright install chromium",
        "requires": ["pip3"],
    },
    "lighthouse": {
        "category": "browser",
        "check_cmd": ["lighthouse", "--version"],
        "check_ok": lambda o: "Lighthouse" in o or o.strip() != "",
        "install_cmd": "npm install -g lighthouse",
        "requires": ["npm"],
    },
    "axe-core": {
        "category": "browser",
        "check_cmd": ["python3", "-c", "from playwright.async_api import async_playwright; print('axe_available')"],
        "check_ok": lambda o: "axe_available" in o,
        "install_cmd": "pip3 install playwright  # axe built into playwright test",
        "requires": ["playwright"],
    },
    "backstopjs": {
        "category": "browser",
        "check_cmd": ["backstop", "--version"],
        "check_ok": lambda o: "backstop" in o.lower() or "BackstopJS" in o,
        "install_cmd": "npm install -g backstopjs",
        "requires": ["npm"],
    },
    # ── Container security ──
    "trivy": {
        "category": "security-ops",
        "check_cmd": ["trivy", "--version"],
        "check_ok": lambda o: "trivy" in o.lower(),
        "install_cmd": "curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh",
        "requires": ["curl"],
    },
    "dockle": {
        "category": "security-ops",
        "check_cmd": ["dockle", "--version"],
        "check_ok": lambda o: "dockle" in o.lower(),
        "install_cmd": "VERSION=$(curl -s https://api.github.com/repos/goodwithtech/dockle/releases/latest | grep tag_name | cut -d'\"' -f4) && curl -L -o dockle.deb https://github.com/goodwithtech/dockle/releases/download/${VERSION}/dockle_${VERSION#v}_Linux-64bit.deb && dpkg -i dockle.deb && rm dockle.deb",
        "requires": ["curl"],
    },
    # ── Database ──
    "sqlfluff": {
        "category": "db",
        "check_cmd": ["sqlfluff", "--version"],
        "check_ok": lambda o: "sqlfluff" in o,
        "install_cmd": "pip3 install sqlfluff",
        "requires": ["pip3"],
    },
    # ── Genesis ──
    "go": {
        "category": "genesis",
        "check_cmd": ["go", "version"],
        "check_ok": lambda o: "go version" in o,
        "install_cmd": "echo 'Install Go from https://go.dev/dl/'",
    },
    "cargo": {
        "category": "genesis",
        "check_cmd": ["cargo", "--version"],
        "check_ok": lambda o: "cargo" in o,
        "install_cmd": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && source ~/.cargo/env",
    },
    "solc": {
        "category": "genesis",
        "check_cmd": ["solc", "--version"],
        "check_ok": lambda o: "solc" in o.lower(),
        "install_cmd": "pip3 install solc-select && solc-select install 0.8.28 && solc-select use 0.8.28",
        "requires": ["pip3"],
    },
    "npm": {
        "category": "genesis",
        "check_cmd": ["npm", "--version"],
        "check_ok": lambda o: o.strip().isdigit() or "." in o,
        "install_cmd": "echo 'Install Node.js: https://nodejs.org/'",
    },
    "pip3": {
        "category": "genesis",
        "check_cmd": ["pip3", "--version"],
        "check_ok": lambda o: "pip" in o,
        "install_cmd": "apt-get install -y python3-pip",
    },
    "curl": {
        "category": "genesis",
        "check_cmd": ["curl", "--version"],
        "check_ok": lambda o: "curl" in o,
        "install_cmd": "apt-get install -y curl",
    },
}


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════

def check(tool_name: str) -> dict:
    """Check if a tool is installed and working. Returns status dict."""
    if tool_name not in TOOLS:
        return {"ok": False, "error": f"Unknown tool: {tool_name}", "available": sorted(TOOLS.keys())}

    t = TOOLS[tool_name]
    try:
        r = subprocess.run(
            t["check_cmd"], capture_output=True, text=True, timeout=15,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        output = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and t["check_ok"](output)
        return {
            "ok": ok,
            "tool": tool_name,
            "category": t["category"],
            "output": output[:500],
            "install_cmd": t["install_cmd"] if not ok else None,
            "install_note": t.get("install_note", ""),
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {
            "ok": False,
            "tool": tool_name,
            "category": t["category"],
            "error": str(e),
            "install_cmd": t["install_cmd"],
            "install_note": t.get("install_note", ""),
        }


def ensure(tool_name: str) -> dict:
    """Check tool; if missing, auto-install (if safe). Returns same format as check()."""
    result = check(tool_name)
    if result["ok"]:
        return result

    # Don't auto-install genesis tools (too complex / interactive)
    if TOOLS[tool_name].get("category") == "genesis":
        result["action"] = "manual_install_required"
        return result

    install_cmd = TOOLS[tool_name].get("install_cmd", "")
    if not install_cmd:
        result["action"] = "no_install_script"
        return result

    try:
        r = subprocess.run(
            ["bash", "-c", install_cmd],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
        )
        if r.returncode == 0:
            # Re-check
            result2 = check(tool_name)
            result2["action"] = "installed" if result2["ok"] else "install_failed"
            result2["install_output"] = (r.stdout + r.stderr)[:1000]
            return result2
        else:
            result["action"] = "install_failed"
            result["install_output"] = (r.stdout + r.stderr)[:1000]
            return result
    except Exception as e:
        result["action"] = "install_error"
        result["install_error"] = str(e)
        return result


def check_category(category: str) -> dict:
    """Check all tools in a given category. Returns summary."""
    tools = {k: v for k, v in TOOLS.items() if v.get("category") == category}
    results = {}
    for name in tools:
        results[name] = check(name)
    ok_count = sum(1 for r in results.values() if r.get("ok"))
    return {
        "ok": len(results) > 0 and ok_count == len(results),
        "category": category,
        "total": len(results),
        "available": ok_count,
        "tools": results,
    }


def check_all() -> dict:
    """Check all defined tools. Returns full dependency matrix."""
    cats = {}
    for name, t in TOOLS.items():
        cat = t.get("category", "other")
        cats.setdefault(cat, {})
        cats[cat][name] = check(name)

    summary = {}
    for cat_name, tools in cats.items():
        ok_count = sum(1 for r in tools.values() if r.get("ok"))
        summary[cat_name] = {"total": len(tools), "available": ok_count}

    return {"ok": True, "summary": summary, "details": cats}


# ═══════════════════════════════════════════════════════
# CLI entry
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "ensure", "all"], default="all", nargs="?")
    ap.add_argument("--tool", help="Tool name")
    ap.add_argument("--category", help="Category filter")
    args = ap.parse_args()

    if args.action == "all":
        print(json.dumps(check_all(), indent=2))
    elif args.action == "check" and args.tool:
        print(json.dumps(check(args.tool), indent=2))
    elif args.action == "ensure" and args.tool:
        print(json.dumps(ensure(args.tool), indent=2))
    else:
        print(json.dumps(check_all(), indent=2))
