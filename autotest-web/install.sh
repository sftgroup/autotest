#!/bin/bash
# autotest-web (中心化) 依赖安装
set -e
echo "=== autotest-web dependencies ==="

# Python
echo "[1] Python deps..."
pip3 install mcp playwright httpx 2>&1 | tail -1

# Playwright Chromium
echo "[2] Playwright browser..."
python3 -m playwright install chromium 2>&1 | tail -3

# Node.js tools
echo "[3] Node.js tools..."
npm install -g backstopjs 2>&1 | tail -1 || echo "  ⚠️  skip"
npm install -g lighthouse 2>&1 | tail -1 || echo "  ⚠️  skip"
npm install -g autocannon 2>&1 | tail -1 || echo "  ⚠️  skip"
npm install -g stepci 2>&1 | tail -1 || echo "  ⚠️  skip"

# hurl
echo "[4] hurl..."
if ! command -v hurl &>/dev/null; then
    if command -v cargo &>/dev/null; then
        cargo install hurl
    elif command -v snap &>/dev/null; then
        snap install hurl
    else
        HURL_VER="6.1.0"
        curl -sLO "https://github.com/Orange-OpenSource/hurl/releases/download/${HURL_VER}/hurl_${HURL_VER}_amd64.deb"
        sudo dpkg -i "hurl_${HURL_VER}_amd64.deb" 2>/dev/null || true
        rm -f "hurl_${HURL_VER}_amd64.deb"
    fi
else
    echo "  hurl: $(hurl --version 2>&1)"
fi

# schemathesis
echo "[5] schemathesis..."
pip3 install schemathesis 2>&1 | tail -1

# Trivy
echo "[6] trivy..."
if ! command -v trivy &>/dev/null; then
    curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh
else
    echo "  trivy: $(trivy --version 2>&1 | head -1)"
fi

# Dockle
echo "[7] dockle..."
if ! command -v dockle &>/dev/null; then
    DOCKLE_VER=$(curl -s https://api.github.com/repos/goodwithtech/dockle/releases/latest | grep tag_name | cut -d'"' -f4)
    curl -L -o /tmp/dockle.deb "https://github.com/goodwithtech/dockle/releases/download/${DOCKLE_VER}/dockle_${DOCKLE_VER#v}_Linux-64bit.deb"
    sudo dpkg -i /tmp/dockle.deb 2>/dev/null || true
    rm -f /tmp/dockle.deb
else
    echo "  dockle: $(dockle --version 2>&1)"
fi

# SQLFluff
echo "[8] sqlfluff..."
pip3 install sqlfluff 2>&1 | tail -1

echo "=== Done ==="
echo "Run: python3 autotest-web/server.py --sse --port 8082"
