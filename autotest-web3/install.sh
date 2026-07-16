#!/bin/bash
# autotest-web3 (去中心化) 依赖安装
set -e
echo "=== autotest-web3 dependencies ==="

# Python
echo "[1] Python deps..."
pip3 install mcp 2>&1 | tail -1

# Foundry
echo "[2] Foundry..."
if ! command -v forge &>/dev/null; then
    curl -L https://foundry.paradigm.xyz | bash
    source ~/.bashrc 2>/dev/null || source ~/.profile 2>/dev/null || true
    foundryup
else
    echo "  forge: $(forge --version 2>&1 | head -1)"
fi

# Solana
echo "[3] Solana..."
if ! command -v solana &>/dev/null; then
    sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
    echo 'export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"' >> ~/.bashrc
else
    echo "  solana: $(solana --version 2>&1)"
fi

# Anchor
echo "[4] Anchor..."
if ! command -v anchor &>/dev/null; then
    if command -v cargo &>/dev/null; then
        cargo install --git https://github.com/coral-xyz/anchor anchor-cli --locked
    else
        echo "  ⚠️  cargo not found. Install Rust first: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    fi
else
    echo "  anchor: $(anchor --version 2>&1)"
fi

# Security tools
echo "[5] Security tools..."
pip3 install slither-analyzer 2>&1 | tail -1 || echo "  ⚠️  slither install skipped"
pip3 install halmos 2>&1 | tail -1 || echo "  ⚠️  halmos install skipped"
# echidna: manual install if missing
if ! command -v echidna &>/dev/null; then
    echo "  ℹ️  echidna requires nix or manual install"
fi

echo "=== Done ==="
echo "Run: python3 autotest-web3/server.py --sse --port 8081"
