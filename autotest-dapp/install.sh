#!/bin/bash
# autotest-dapp (DApp 全链路) 依赖安装
# DApp server 同时需要链上 + 浏览器依赖
set -e
echo "=== autotest-dapp dependencies ==="

# 复用 web3 和 web 的依赖
cd "$(dirname "$0")"

echo "[1] Python deps..."
pip3 install mcp playwright 2>&1 | tail -1

echo "[2] Foundry..."
if ! command -v forge &>/dev/null; then
    curl -L https://foundry.paradigm.xyz | bash
    source ~/.bashrc 2>/dev/null || source ~/.profile 2>/dev/null || true
    foundryup
else
    echo "  forge: $(forge --version 2>&1 | head -1)"
fi

echo "[3] Playwright browser..."
python3 -m playwright install chromium 2>&1 | tail -3

echo "[4] Solana..."
if ! command -v solana &>/dev/null; then
    sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
else
    echo "  solana: $(solana --version 2>&1)"
fi

echo "=== Done ==="
echo "Run: python3 autotest-dapp/server.py --sse --port 8083"
