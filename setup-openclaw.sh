#!/bin/bash
# OpenClaw MCP 接入脚本
# 在每台需要接入的 OpenClaw 实例上执行
# 用法: MCP_IP=192.168.1.100 bash setup-openclaw.sh

set -e

MCP_IP=${MCP_IP:-"YOUR_MCP_SERVER_IP"}
WEB3_PORT=${WEB3_PORT:-8081}
WEB_PORT=${WEB_PORT:-8082}

echo "=== Adding autotest MCP servers ==="
echo "  MCP Server: ${MCP_IP}"
echo "  Web3 port:  ${WEB3_PORT}"
echo "  Web port:   ${WEB_PORT}"
echo ""

echo "[1/2] Adding autotest-web3..."
openclaw mcp add autotest-web3 \
  --transport sse \
  --url "http://${MCP_IP}:${WEB3_PORT}/sse"

echo "[2/2] Adding autotest-web..."
openclaw mcp add autotest-web \
  --transport sse \
  --url "http://${MCP_IP}:${WEB_PORT}/sse"

echo ""
echo "=== Verifying ==="
openclaw mcp list
echo ""
echo "=== Done ==="
echo ""
echo "To configure agent permissions, see README.md § Agent 权限配置"
echo ""
echo "Quick tester agent (full access):"
echo "  openclaw mcp tools autotest-web3 --enable"
echo "  openclaw mcp tools autotest-web --enable"
