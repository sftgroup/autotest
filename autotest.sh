#!/bin/bash
# autotest v2.0 — AutoOps 通用自动化测试引擎
# 
# 用法:
#   autotest run --project <path> [--scope ct|at|ft|all]
#   autotest {browser|chain|db|data|assert} <cmd> [args]
#   autotest selfcheck --project <path>
#
# Chain 高级命令 (v1.2): mint / approve / transfer / swap / addLiquidity / removeLiquidity / createPool / transferNFT
# 场景文件需声明 ## contracts 和 ## tokens 段，然后用 chain 语义化命令

set -o pipefail

AUTOTEST_VERSION="1.4"
CMD="${1:-help}"
shift 2>/dev/null || true

# ── 配置解析 ──────────────────────────────────
parse_opts() {
    PROJECT="" SCOPE="all" RPC="" FRONTEND="" DB_HOST="" DB_PORT="5432" DB_USER="" DB_PASS="" DB_NAME="" WALLET="deployer"
    while [ $# -gt 0 ]; do
        case "$1" in
            --project)   PROJECT="$2"; shift 2 ;;
            --scope)     SCOPE="$2"; shift 2 ;;
            --rpc)       RPC="$2"; shift 2 ;;
            --frontend)  FRONTEND="$2"; shift 2 ;;
            --db-host)   DB_HOST="$2"; shift 2 ;;
            --db-port)   DB_PORT="$2"; shift 2 ;;
            --db-user)   DB_USER="$2"; shift 2 ;;
            --db-pass)   DB_PASS="$2"; shift 2 ;;
            --db-name)   DB_NAME="$2"; shift 2 ;;
            --wallet)    WALLET="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
}

# ── 自检 ──────────────────────────────────────
selfcheck() {
    parse_opts "$@"
    local issues=0
    echo "━━━ autotest selfcheck ━━━"

    # 1. 前端 URL
    local f="${FRONTEND:-${FRONTEND_URL:-}}"
    if [ -z "$f" ]; then
        echo "❌ 阻塞: 前端 URL 缺失 (--frontend 或 \$FRONTEND_URL)"
        issues=$((issues+1))
    else
        echo "✅ 前端: $f"
    fi

    # 2. RPC
    local r="${RPC:-${SEPOLIA_RPC:-}}"
    if [ -z "$r" ]; then
        echo "❌ 阻塞: 链 RPC 缺失 (--rpc 或 \$SEPOLIA_RPC)"
        issues=$((issues+1))
    else
        echo "✅ RPC: ${r:0:40}..."
    fi

    # 3. 私钥
    local sk="${DEPLOYER_PRIVATE_KEY:-}"
    if [ -z "$sk" ]; then
        echo "❌ 阻塞: 私钥缺失 (source .sepolia.env?)"
        issues=$((issues+1))
    else
        echo "✅ 私钥: 已加载"
    fi

    # 4. 数据库
    local dbh="${DB_HOST:-${PGHOST:-}}"
    if [ -z "$dbh" ]; then
        echo "⚠️  数据库连接缺失 — DB 测试将跳过"
    else
        echo "✅ DB: ${dbh}:${DB_PORT:-5432}/${DB_NAME:-?}"
    fi

    # 5. autotest
    echo "✅ autotest: v${AUTOTEST_VERSION}"

    # 6. 工具
    forge --version >/dev/null 2>&1 && echo "✅ forge: $(forge --version | head -1)" || echo "⚠️ forge 不可用"
    cast --version >/dev/null 2>&1 && echo "✅ cast: $(cast --version | head -1)" || echo "⚠️ cast 不可用"
    agent-browser --version >/dev/null 2>&1 && echo "✅ agent-browser: $(agent-browser --version 2>&1 | head -1)" || echo "⚠️ agent-browser 不可用"
    curl --version >/dev/null 2>&1 && echo "✅ curl: OK" || echo "❌ curl 不可用"

    # 7. 多链 RPC
    local ct_tmp; ct_tmp=$(ls "${PROJECT}/test-reports/TEST_SCENARIOS_CT.md" 2>/dev/null || echo "")
    if [ -n "$ct_tmp" ]; then
        local chain_rpc; chain_rpc=$(get_chain_rpc "$ct_tmp")
        [ -n "$chain_rpc" ] && echo "✅ CT链RPC: $(echo "$chain_rpc" | cut -c1-40)..." || echo "⚠️  未声明链，默认 Sepolia"
    else
        echo "ℹ️  无 CT 场景文件"
    fi
    echo "━━━━━━━━━━━━━━━━━━━━"
    if [ "$issues" -gt 0 ]; then
        echo "❌ $issues 项阻塞 — 向架构师提问补充配置"
        return 1
    else
        echo "✅ 自检通过"
        return 0
    fi
}

# ── 数据工厂 ──────────────────────────────────
data() {
    local type="${1:-}" tag="${2:-}"
    # 生成确定性随机（同 tag 同值）
    local seed="${tag:-$RANDOM}"
    local hash=$(echo -n "$seed" | md5sum | cut -c1-8)

    case "$type" in
        email)    echo "test_${hash}@autotest.local" ;;
        phone)    echo "138$(printf '%08d' "$((0x${hash:0:8} % 100000000))" | head -c8)" ;;
        username) echo "user_${hash}" ;;
        eth-address) echo "0x${hash}$(echo -n "$seed" | md5sum | cut -c1-32)" ;;
        uuid)     echo "${hash:0:8}-${hash:0:4}-4${hash:4:3}-a${hash:6:3}-${hash}000000" ;;
        long-string)
            local n="${2:-255}"
            python3 -c "print('x'*${n})" 2>/dev/null || printf 'x%.0s' $(seq 1 "$n")
            ;;
        special-chars)
            printf "'; DROP TABLE users;--\n<script>alert(1)</script>\n\0\n\t\n"
            ;;
        empty)    echo '""' ;;
        null)     echo "null" ;;
        *)        echo "ERROR: unknown data type '$type'. Try: email phone username eth-address uuid long-string special-chars empty null" ;;
    esac
}

# ── 浏览器 ────────────────────────────────────
browser() {
    local action="${1:-}" u="${FRONTEND:-${FRONTEND_URL:-http://localhost:4000}}"
    shift 2>/dev/null || true
    case "$action" in
        open)    agent-browser open "${1:-$u}" 2>&1 | tail -1 ;;
        snapshot) sleep 2; agent-browser snapshot -i --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
items=d if isinstance(d,list) else d.get('children',d.get('items',[]))
print(f'DOM_OK:{len(items)} elements')
for i,item in enumerate(items[:30]):
    t=str(item)[:120].replace(chr(10),' ')
    print(f'  [{i+1}] {t}')
" 2>/dev/null || echo "DOM_EMPTY" ;;
        elements) sleep 1; agent-browser snapshot -i --json 2>/dev/null | python3 -c "
import json,sys,re
d=json.load(sys.stdin)
items=d if isinstance(d,list) else d.get('children',d.get('items',[]))
btns,ins,lnks=[],[],[]
for i,item in enumerate(items):
    t=str(item)
    if re.search(r'button|btn',t,re.I): btns.append(f'  [{i+1}] {t[:80]}')
    if re.search(r'input|textbox|textfield',t,re.I): ins.append(f'  [{i+1}] {t[:80]}')
    if re.search(r'link|<a ',t,re.I): lnks.append(f'  [{i+1}] {t[:80]}')
print(f'BUTTONS:{len(btns)}'); [print(b) for b in btns[:15]]
print(f'INPUTS:{len(ins)}'); [print(i) for i in ins[:10]]
print(f'LINKS:{len(lnks)}'); [print(l) for l in lnks[:10]]
" 2>/dev/null || echo "NO_ELEMENTS" ;;
        content) agent-browser get markdown 2>/dev/null | head -200 ;;
        click)  agent-browser click "${1:?need index}" 2>&1 | tail -1 ;;
        type)   agent-browser type "${1:?need index}" "${2:?need text}" 2>&1 | tail -1 ;;
        hover)  agent-browser hover "${1:?need index}" 2>&1 | tail -1 ;;
        scroll) agent-browser scroll "${1:-down}" 2>&1 | tail -1 ;;
        wait)   agent-browser wait stable "${2:-5000}" 2>&1 | tail -1 ;;
        screenshot) local p="${1:-/tmp/autotest-screenshot.png}"; agent-browser screenshot "$p" 2>&1 | tail -1; echo "SCREENSHOT:$p" ;;
        close)  agent-browser close 2>/dev/null || true; echo "CLOSED" ;;
        *) echo "Usage: autotest browser {open|snapshot|elements|content|click|type|hover|scroll|wait|screenshot|close} [args]" ;;
    esac
}


# ── 多链 RPC 映射 (v1.3) ─────────────────────
declare -A CHAIN_RPC
CHAIN_RPC["sepolia"]="${SEPOLIA_RPC:-}"
CHAIN_RPC["sepolia_2"]="${SEPOLIA_RPC_2:-}"
CHAIN_RPC["mainnet"]="${ETH_RPC:-}"
CHAIN_RPC["arbitrum"]="${ARB_RPC:-}"
CHAIN_RPC["base"]="${BASE_RPC:-}"
CHAIN_RPC["monad"]="${MONAD_RPC:-}"
CHAIN_RPC["bsc"]="${BSC_RPC:-}"
CHAIN_RPC["polygon"]="${POLYGON_RPC:-}"

# 从 ## chains 声明段解析当前 CT 文件的链配置
parse_chain_config() {
    local file="$1"
    awk '/^## chains/{f=1; next} /^## (contracts|tokens|scenarios|declarations)/{f=0; exit} f && /^\|/' "$file" 2>/dev/null |         grep -v -- '------' | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$3);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$4); if($2!~/链名|名称/) printf "%s|%s|%s\n",$2,$3,$4}'
}

# 获取当前场景文件的 RPC (v2.0 扩展从 ## chains 读 gas_price_gwei)
get_chain_rpc() {
    local file="$1"
    local chain_name; chain_name=$(parse_chain_config "$file" | head -1 | cut -d'|' -f1)
    [ -z "$chain_name" ] && chain_name="sepolia"
    local rpc; rpc=$(parse_chain_config "$file" | grep "^${chain_name}|" | head -1 | cut -d'|' -f2)
    # 展开环境变量: $SEPOLIA_RPC → 实际值
    rpc=$(eval echo "$rpc" 2>/dev/null)
    [ -z "$rpc" ] && rpc="${CHAIN_RPC[$chain_name]:-}"
    [ -z "$rpc" ] && rpc="${SEPOLIA_RPC:-}"
    echo "$rpc"
}

# 从 ## chains 表读 gas_price_gwei (v2.0 新增)
get_chain_gas_price() {
    local file="$1"
    local chain_name; chain_name=$(parse_chain_config "$file" | head -1 | cut -d'|' -f1)
    [ -z "$chain_name" ] && chain_name="sepolia"
    local g; g=$(parse_chain_config "$file" | grep "^${chain_name}|" | head -1 | cut -d'|' -f3)
    [ -n "$g" ] && [ "$g" != " " ] && echo "$g"
}

# ── 链上 ──────────────────────────────────────
chain() {
    local action="${1:-}" r="${RPC:-${SEPOLIA_RPC:-}}" sk="${DEPLOYER_PRIVATE_KEY:-}"
    shift 2>/dev/null || true
    [ -z "$r" ] && { echo "ERROR: no RPC"; return 1; }
    case "$action" in
        call)    cast call "${1:?addr}" "${2:?sig}" ${3:+"$3"} --rpc-url "$r" 2>&1 ;;
        send)
            [ -z "$sk" ] && { echo "ERROR: no private key"; return 1; }
            local tx; tx=$(cast send "${1:?addr}" "${2:?sig}" ${3:+"$3"} --rpc-url "$r" --private-key "${DEPLOYER_PRIVATE_KEY:-}" $gas_opt --legacy 2>&1 | sed "s/${DEPLOYER_PRIVATE_KEY//\//\\\\/}/***/g")
            echo "$tx"
            echo "$tx" | grep -oP '0x[a-fA-F0-9]{64}' | head -1 | xargs -I{} echo "TX_HASH:{}"
            ;;
        receipt) cast receipt "${1:?txHash}" --rpc-url "$r" 2>&1 ;;
        balance)
            local bal; bal=$(cast balance "${1:?addr}" --rpc-url "$r" 2>&1)
            echo "$bal"
            local eth; eth=$(cast to-unit "$bal" ether 2>/dev/null || echo "?")
            echo "ETH:$eth"
            ;;
        code)    cast code "${1:?addr}" --rpc-url "$r" 2>&1 | head -20 ;;
        block)   cast block-number --rpc-url "$r" 2>&1 ;;
        *) echo "Usage: autotest chain {call|send|receipt|balance|code|block} [args]" ;;
    esac
}

# ── 数据库 ────────────────────────────────────
db() {
    local action="${1:-}" h="${DB_HOST:-${PGHOST:-localhost}}" p="${DB_PORT:-5432}" u="${DB_USER:-postgres}" n="${DB_NAME:-}"
    shift 2>/dev/null || true
    [ -z "$n" ] && { echo "ERROR: no DB_NAME"; return 1; }
    case "$action" in
        psql)
            PGPASSWORD="***" psql -h "$h" -p "$p" -U "$u" -d "$n" -c "${1:?query}" 2>&1 | head -50
            ;;
        redis)
            redis-cli -h "${REDIS_HOST:-$h}" "${1:?cmd}" 2>&1 | head -20
            ;;
        *) echo "Usage: autotest db {psql|redis} [args]" ;;
    esac
}

# ── 断言 ──────────────────────────────────────
assert() {
    local a="${1:-}" r="${RPC:-${SEPOLIA_RPC:-}}"
    shift 2>/dev/null || true
    case "$a" in
        status)
            [ "${1:-}" = "${2:-}" ] && echo "ASSERT_PASS: status $2 == $1" || echo "ASSERT_FAIL: status $2 != $1"
            ;;
        tx-confirmed)
            local rcpt; rcpt=$(cast receipt "${1:?txHash}" --rpc-url "$r" 2>/dev/null || echo "")
            echo "$rcpt" | grep -qi "status.*1\|blockHash" && echo "ASSERT_PASS: tx ${1:0:10}... confirmed" || echo "ASSERT_FAIL: tx ${1:0:10}... not confirmed"
            ;;
        page-contains)
            local content; content=$(agent-browser get markdown 2>/dev/null || echo "")
            echo "$content" | grep -qi "${1:?text}" && echo "ASSERT_PASS: page contains '$1'" || echo "ASSERT_FAIL: page does NOT contain '$1'"
            ;;
        page-not-contains)
            local content; content=$(agent-browser get markdown 2>/dev/null || echo "")
            echo "$content" | grep -qi "${1:?text}" && echo "ASSERT_FAIL: page contains '$1'" || echo "ASSERT_PASS: page does NOT contain '$1'"
            ;;
        page-title)
            local t; t=$(agent-browser snapshot -i --json 2>/dev/null | python3 -c "import json,sys,re; m=re.search(r'title[^w]*([^\"'\''>]+)',str(json.load(sys.stdin)),re.I); print(m.group(1) if m else '')" 2>/dev/null || echo "")
            echo "$t" | grep -qi "${1:?title}" && echo "ASSERT_PASS: title contains '$1'" || echo "ASSERT_FAIL: title '$t' != '$1'"
            ;;
        element-count)
            local cnt; cnt=$(agent-browser snapshot -i --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('children',[]); print(len(items))" 2>/dev/null || echo "0")
            [ "$cnt" -ge "${1:?N}" ] 2>/dev/null && echo "ASSERT_PASS: $cnt elements >= $1" || echo "ASSERT_FAIL: $cnt elements < $1"
            ;;
        balance)
            local bal; bal=$(cast balance "${1:?addr}" --rpc-url "$r" 2>/dev/null || echo "0")
            local exp="${2:-0}"
            [ "$bal" = "$exp" ] || echo "$bal" | grep -q "$exp" 2>/dev/null && echo "ASSERT_PASS: balance $bal" || echo "ASSERT_FAIL: balance $bal != $exp"
            ;;
        db-row)
            local h="${DB_HOST:-${PGHOST:-localhost}}" u2="${DB_USER:-postgres}" n="${DB_NAME:-}"
            [ -z "$n" ] && { echo "ASSERT_SKIP: no DB_NAME"; return 0; }
            local cnt; cnt=$(PGPASSWORD="***" psql -h "$h" -U "$u2" -d "$n" -t -c "SELECT COUNT(*) FROM ${1:?table} WHERE ${2:?condition}" 2>/dev/null | xargs || echo "0")
            [ "$cnt" -gt 0 ] 2>/dev/null && echo "ASSERT_PASS: ${1} has $cnt matching rows" || echo "ASSERT_FAIL: ${1} has no matching rows"
            ;;
        event-emitted)
            local logs; logs=$(cast logs --address "${2:?addr}" --rpc-url "$r" 2>/dev/null | head -20 || echo "")
            echo "$logs" | grep -qi "${1:?event}" && echo "ASSERT_PASS: event $1 found" || echo "ASSERT_FAIL: event $1 not found"
            ;;
        notification)
            # 检查最近消息是否包含指定内容（飞书/邮件日志）
            echo "ASSERT_SKIP: notification check requires channel integration (v1.1)"
            ;;
        file-exists)
            [ -f "${1:?path}" ] && echo "ASSERT_PASS: $1 exists" || echo "ASSERT_FAIL: $1 not found"
            ;;
        file-contains)
            grep -qi "${2:?text}" "${1:?path}" 2>/dev/null && echo "ASSERT_PASS: $1 contains '$2'" || echo "ASSERT_FAIL: $1 does NOT contain '$2'"
            ;;
        response-time)
            local url="${1:?url}" threshold="${2:-500}"
            local start; start=$(date +%s%N)
            curl -s -o /dev/null "$url" --connect-timeout 5 2>/dev/null || true
            local end; end=$(date +%s%N)
            local ms=$(( (end - start) / 1000000 ))
            [ "$ms" -lt "$threshold" ] && echo "ASSERT_PASS: ${ms}ms < ${threshold}ms" || echo "ASSERT_FAIL: ${ms}ms >= ${threshold}ms"
            ;;
        screenshot-match)
            echo "ASSERT_SKIP: pixel-diff requires image comparison tool (v1.1)"
            ;;
        *) echo "Usage: autotest assert {status|tx-confirmed|page-contains|page-not-contains|page-title|element-count|balance|db-row|event-emitted|notification|file-exists|file-contains|response-time|screenshot-match} [args]" ;;
    esac
}

# ── 合约/代币声明解析 ─────────────────────────
parse_declarations() {
    local file="$1"
    # 解析 ## contracts 段
    awk '/^## contracts/{f=1; next} /^## tokens/{f=0; exit} f && /^\|/' "$file" 2>/dev/null | \
        grep -v -- '------' | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$3);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$4); if($2!~/合约名|名称/) printf "%s|%s|%s\n",$2,$3,$4}'
}

parse_tokens() {
    local file="$1"
    # 解析 ## tokens 段
    awk '/^## tokens/{f=1; next} /^## scenarios/{f=0; exit} f && /^\|/' "$file" 2>/dev/null | \
        grep -v -- '------' | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$3);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$4); if($2!~/别名/) printf "%s|%s|%s\n",$2,$3,$4}'
}

# ── 环境声明解析 (v2.0) ────────────────────────
parse_declarations_env() {
    local file="$1"
    awk '/^## declarations/{f=1; next} /^## (contracts|tokens|scenarios|chains)/{f=0; exit} f && /^\|/' "$file" 2>/dev/null | \
        grep -v -- '------' | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2);gsub(/^[[:space:]]+|[[:space:]]+$/,"",$3); if($2!~/键/) printf "%s|%s\n",$2,$3}'
}

expand_vars() {
    # 展开 ${FRONTEND} ${RELAY_API} 等模板变量
    local text="$1" file="$2"
    local frontend relay_api
    frontend=$(parse_declarations_env "$file" | grep '^frontend|' | cut -d'|' -f2)
    relay_api=$(parse_declarations_env "$file" | grep '^relay_api|' | cut -d'|' -f2)
    [ -z "$frontend" ] && frontend="${FRONTEND:-${FRONTEND_URL:-}}"
    [ -z "$relay_api" ] && relay_api="$frontend/api"
    local result="$text"
    result="${result//\$\{FRONTEND\}/$frontend}"
    result="${result//\$\{RELAY_API\}/$relay_api}"
    echo "$result"
}

# 查声明表：get_contract "Router" → 0xF...  get_token_decimals "USDC" → 6
get_contract()  { parse_declarations "$AT_CT_FILE" | grep "^${1}|" | head -1 | cut -d'|' -f2; }
get_contract_type() { parse_declarations "$AT_CT_FILE" | grep "^${1}|" | head -1 | cut -d'|' -f3; }
get_token_addr() { parse_tokens "$AT_CT_FILE" | grep "^${1}|" | head -1 | cut -d'|' -f2; }
get_token_decimals() { local d; d=$(parse_tokens "$AT_CT_FILE" | grep "^${1}|" | head -1 | cut -d'|' -f3); echo "${d:-18}"; }

# 从 .test-wallets.env 读取指定钱包私钥
load_wallet() {
    local wn="${1:-test-1}"
    local val
    # 特殊别名: deployer → DEPLOYER_PRIVATE_KEY, owner → OWNER_PRIVATE_KEY
    case "$wn" in
        deployer) val="${DEPLOYER_PRIVATE_KEY:-}" ;;
        owner) val="${OWNER_PRIVATE_KEY:-}" ;;
        wallet-*)
            local vname=$(echo "$wn" | sed 's/wallet-/TEST_/' | tr 'a-z' 'A-Z')_SK
            local envf="${TEST_WALLETS_ENV:-${HOME}/.openclaw/workspace/.test-wallets.env}"
            [ -f "$envf" ] && source "$envf" 2>/dev/null
            val="${!vname:-${DEPLOYER_PRIVATE_KEY:-}}" ;;
        *) val="${DEPLOYER_PRIVATE_KEY:-}" ;;
    esac
    [ -n "$val" ] && echo "${val:0:6}...${val: -4}" || echo ""
}

# 获取钱包完整私钥(用于实际cast发送, 非日志)
load_wallet_full() {
    local wn="${1:-test-1}"
    case "$wn" in
        deployer) echo "${DEPLOYER_PRIVATE_KEY:-}" ;;
        owner) echo "${OWNER_PRIVATE_KEY:-}" ;;
        wallet-*)
            local vname=$(echo "$wn" | sed 's/wallet-/TEST_/' | tr 'a-z' 'A-Z')_SK
            local envf="${TEST_WALLETS_ENV:-${HOME}/.openclaw/workspace/.test-wallets.env}"
            [ -f "$envf" ] && source "$envf" 2>/dev/null
            echo "${!vname:-${DEPLOYER_PRIVATE_KEY:-}}" ;;
        *) echo "${DEPLOYER_PRIVATE_KEY:-}" ;;
    esac
}

# ── Chain 高级命令分发器 ────────────────────────
run_chain_cmd() {
    # Helper: 过滤命令行中的敏感值(私钥)防止泄露到报告
    safe_filter() { local sk="$1"; shift; "$@" 2>&1 | sed "s/${sk//\//\\/}/\*\*\*/g"; }
    local cmd="$1" token="$2" rpc="${RPC:-${SEPOLIA_RPC:-}}" gas_price="${GAS_PRICE_GWEI:-}"
    local gas_opt=""
    [ -n "$gas_price" ] && gas_opt="--gas-price ${gas_price}000000000"
    shift 2 2>/dev/null || true

    case "$cmd" in
        mint)
            local wallet="${1:-deployer}" amount="${2:-1}"
            local sk; sk=$(load_wallet_full "$wallet")
            local addr; addr=$(get_contract "$token")
            [ -z "$addr" ] && addr="$token"  # fallback: 直接当地址
            local ctype; ctype=$(get_contract_type "$token" 2>/dev/null || echo "")
            echo "--- chain mint: $token → $wallet ($amount)"
            if echo "$ctype" | grep -qi 'nft\|erc721'; then
                # NFT: parameterless mint()
                cast send "$addr" "mint()" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            else
                # ERC20/other: mint(address,uint256)
                cast send "$addr" "mint(address,uint256)" "$(echo "$sk" | xargs -I{} cast wallet address --private-key {} 2>/dev/null || echo "0xunknown")" "${amount:-1}" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            fi
            ;;
        approve)
            local spender="$1" amount="${2:-max}" wallet="$3"
            local sk; sk=$(load_wallet_full "$wallet")
            local addr; addr=$(get_token_addr "$token")
            local decimals; decimals=$(get_token_decimals "$token")
            local spender_addr; spender_addr=$(get_contract "$spender")
            [ -z "$spender_addr" ] && spender_addr="$spender"  # fallback: 直接当地址
            local amt_wei
            if [ "$amount" = "max" ]; then
                amt_wei="115792089237316195423570985008687907853269984665640564039457584007913129639935" # uint256 max
            else
                amt_wei=$(echo "$amount * 10^$decimals" | bc 2>/dev/null || echo "$amount")
            fi
            echo "--- chain approve: $token → $spender ($amount)"
            cast send "$addr" "approve(address,uint256)" "$spender_addr" "$amt_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            ;;
        transfer)
            local to="$3" wallet="$4" amount="$5"
            local sk; sk=$(load_wallet_full "$wallet")
            local addr; addr=$(get_token_addr "$token")
            local decimals; decimals=$(get_token_decimals "$token")
            [ -z "$addr" ] && addr="$token"  # ETH转账
            local amt_wei; amt_wei=$(echo "$amount * 10^$decimals" | bc 2>/dev/null || echo "$amount")
            local to_addr; to_addr=$(load_wallet_full "$to" 2>/dev/null && cast wallet address --private-key "$(load_wallet_full "$to")" 2>/dev/null || echo "$to")
            echo "--- chain transfer: $token → $to ($amount)"
            if [ "$addr" = "$token" ]; then
                cast send --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy "$to_addr" --value "$amt_wei" 2>&1 | sed "s/${sk//\//\\\\/}/***/g"
            else
                cast send "$addr" "transfer(address,uint256)" "$to_addr" "$amt_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            fi
            ;;
        transferNFT)
            local wallet="${*: -1:1}" tokenId="${*: -2:1}" to="${*: -3:1}"
            local sk; sk=$(load_wallet_full "$wallet")
            local addr; addr=$(get_token_addr "$token")
            local to_addr; to_addr=$(load_wallet_full "$to" 2>/dev/null && cast wallet address --private-key "$(load_wallet_full "$to")" 2>/dev/null || echo "$to")
            local from_addr; from_addr=$(cast wallet address --private-key "$sk" 2>/dev/null || echo "0xunknown")
            echo "--- chain transferNFT: $token #${tokenId} → $to"
            cast send "$addr" "safeTransferFrom(address,address,uint256)" "$from_addr" "$to_addr" "$tokenId" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            ;;
        balanceOf)
            local wallet="$3"
            local addr; addr=$(get_token_addr "$token")
            local wallet_addr; wallet_addr=$(load_wallet_full "$wallet" 2>/dev/null && cast wallet address --private-key "$(load_wallet_full "$wallet")" 2>/dev/null || echo "$wallet")
            echo "--- chain balanceOf: $token → $wallet"
            cast call "$addr" "balanceOf(address)(uint256)" "$wallet_addr" --rpc-url "$rpc" 2>&1
            ;;
        swap)
            local router="$1" tokenIn="$2" tokenOut="$3" amountIn="$4" wallet="$5"
            local sk; sk=$(load_wallet_full "$wallet")
            local r_addr; r_addr=$(get_contract "$router"); [ -z "$r_addr" ] && r_addr="$router"
            local tIn_addr; tIn_addr=$(get_token_addr "$tokenIn"); [ -z "$tIn_addr" ] && tIn_addr="$tokenIn"
            local tOut_addr; tOut_addr=$(get_token_addr "$tokenOut"); [ -z "$tOut_addr" ] && tOut_addr="$tokenOut"
            local in_dec; in_dec=$(get_token_decimals "$tokenIn")
            local amtIn_wei; amtIn_wei=$(echo "$amountIn * 10^$in_dec" | bc 2>/dev/null || echo "$amountIn")
            echo "--- chain swap: $tokenIn → $tokenOut ($amountIn)"
            # 1. approve router
            cast send "$tIn_addr" "approve(address,uint256)" "$r_addr" "$amtIn_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g" | tail -1
            # 2. 查询最少输出
            local minOut; minOut=$(cast call "$r_addr" "getAmountsOut(uint256,address[])(uint256[])" "$amtIn_wei" "[$tIn_addr,$tOut_addr]" --rpc-url "$rpc" 2>/dev/null | python3 -c "import sys; s=sys.stdin.read(); print(s.split('\n')[0].strip() if s else '1')" 2>/dev/null || echo "1")
            minOut=$(echo "$minOut * 95 / 100" | bc 2>/dev/null || echo "$minOut")  # 5% slippage
            local deadline; deadline=$(($(date +%s) + 600))
            local me; me=$(cast wallet address --private-key "$sk" 2>/dev/null)
            # 3. swap
            cast send "$r_addr" "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)" "$amtIn_wei" "$minOut" "[$tIn_addr,$tOut_addr]" "$me" "$deadline" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            ;;
        addLiquidity)
            local router="$1" tokenA="$2" amountA="$3" tokenB="$4" amountB="$5" wallet="$6"
            local sk; sk=$(load_wallet_full "$wallet")
            local r_addr; r_addr=$(get_contract "$router"); [ -z "$r_addr" ] && r_addr="$router"
            local tA; tA=$(get_token_addr "$tokenA"); [ -z "$tA" ] && tA="$tokenA"
            local tB; tB=$(get_token_addr "$tokenB"); [ -z "$tB" ] && tB="$tokenB"
            local da; da=$(get_token_decimals "$tokenA")
            local db; db=$(get_token_decimals "$tokenB")
            local amtA_wei; amtA_wei=$(echo "$amountA * 10^$da" | bc 2>/dev/null)
            local amtB_wei; amtB_wei=$(echo "$amountB * 10^$db" | bc 2>/dev/null)
            echo "--- chain addLiquidity: $tokenA ($amountA) + $tokenB ($amountB)"
            # approve both
            cast send "$tA" "approve(address,uint256)" "$r_addr" "$amtA_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g" | tail -1
            cast send "$tB" "approve(address,uint256)" "$r_addr" "$amtB_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g" | tail -1
            local deadline; deadline=$(($(date +%s) + 600))
            local me; me=$(cast wallet address --private-key "$sk" 2>/dev/null)
            cast send "$r_addr" "addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)" "$tA" "$tB" "$amtA_wei" "$amtB_wei" "$(echo "$amtA_wei * 95 / 100" | bc)" "$(echo "$amtB_wei * 95 / 100" | bc)" "$me" "$deadline" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            ;;
        removeLiquidity)
            local router="$1" tokenA="$2" tokenB="$3" lpAmount="$4" wallet="$5"
            local sk; sk=$(load_wallet_full "$wallet")
            local r_addr; r_addr=$(get_contract "$router"); [ -z "$r_addr" ] && r_addr="$router"
            local tA; tA=$(get_token_addr "$tokenA"); [ -z "$tA" ] && tA="$tokenA"
            local tB; tB=$(get_token_addr "$tokenB"); [ -z "$tB" ] && tB="$tokenB"
            local pair_addr; pair_addr=$(get_token_addr "LP-${tokenA}-${tokenB}" 2>/dev/null)
            [ -z "$pair_addr" ] && pair_addr=$(get_token_addr "LP-${tokenB}-${tokenA}" 2>/dev/null)
            [ -z "$pair_addr" ] && { local fa; fa=$(get_contract "Factory"); fa=${fa:-0xFA}; pair_addr=$(cast call "$fa" "getPair(address,address)(address)" "$tA" "$tB" --rpc-url "$rpc" 2>/dev/null | tr -d '\n' | xargs || echo "0xunknown"); }
            local lp_dec; lp_dec=$(get_token_decimals "LP-${tokenA}-${tokenB}")
            local lp_wei; lp_wei=$(echo "$lpAmount * 10^$lp_dec" | bc 2>/dev/null || echo "$lpAmount")
            echo "--- chain removeLiquidity: LP ($lpAmount) → $tokenA + $tokenB"
            # approve LP token
            cast send "$pair_addr" "approve(address,uint256)" "$r_addr" "$lp_wei" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g" | tail -1
            local deadline; deadline=$(($(date +%s) + 600))
            local me; me=$(cast wallet address --private-key "$sk" 2>/dev/null)
            cast send "$r_addr" "removeLiquidity(address,address,uint256,uint256,uint256,address,uint256)" "$tA" "$tB" "$lp_wei" "0" "0" "$me" "$deadline" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            ;;
        createPool)
            local factory="$1" tokenA="$2" tokenB="$3" wallet="$4"
            local sk; sk=$(load_wallet_full "$wallet")
            local f_addr; f_addr=$(get_contract "$factory"); [ -z "$f_addr" ] && f_addr="$factory"
            local tA; tA=$(get_token_addr "$tokenA"); [ -z "$tA" ] && tA="$tokenA"
            local tB; tB=$(get_token_addr "$tokenB"); [ -z "$tB" ] && tB="$tokenB"
            echo "--- chain createPool: $tokenA + $tokenB"
            cast send "$f_addr" "createPair(address,address)(address)" "$tA" "$tB" --rpc-url "$rpc" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g"
            # 查回新 pair 地址
            cast call "$f_addr" "getPair(address,address)(address)" "$tA" "$tB" --rpc-url "$rpc" 2>&1
            ;;
        *)
            echo "ERROR: unknown chain command '$cmd'. Try: mint approve transfer transferNFT balanceOf swap addLiquidity removeLiquidity createPool"
            return 1
            ;;
    esac
}

# ── 场景文件解析 ───────────────────────────────
parse_scenarios() {
    local file="$1"
    # Compatible with: | ID | col2 | col3 | col4 | (4-column table)
    # col3 = command/operation, col4 = expected result
    grep -E '^\|[^-].*\|$' "$file" 2>/dev/null | grep -v -- '------' | grep -v -E '^\|.*ID\|' |     awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2); gsub(/^[[:space:]]+|[[:space:]]+$/,"",$3); gsub(/^[[:space:]]+|[[:space:]]+$/,"",$4); gsub(/^[[:space:]]+|[[:space:]]+$/,"",$5); print $2"|"$3"|"$4"|"$5}' || true
}
# ── 一键编排 ──────────────────────────────────
run_all() {
    parse_opts "$@"
    [ -z "$PROJECT" ] && { echo "ERROR: --project required"; exit 1; }

    # 先自检
    if ! selfcheck "$@"; then
        echo "⛔ 配置不全，停止执行。请向架构师提问补充缺失配置。"
        exit 1
    fi

    local report_dir="${PROJECT}/test-reports"
    local report="${report_dir}/E2E_TEST_REPORT.md"
    local ts=$(date -Iseconds)
    mkdir -p "$report_dir"

    local pass=0 fail=0 skip=0 blocking_fail=0

    # 报告头
    cat > "$report" << EOF
# E2E Test Report

**项目**: ${PROJECT}
**时间**: ${ts}
**范围**: ${SCOPE}
**引擎**: autotest v${AUTOTEST_VERSION}

## 工具可用性
| 工具 | 版本 |
|------|------|
| forge | $(forge --version 2>/dev/null | head -1 || echo '❌') |
| cast | $(cast --version 2>/dev/null | head -1 || echo '❌') |
| agent-browser | $(agent-browser --version 2>/dev/null | head -1 || echo '❌') |
| autotest | v${AUTOTEST_VERSION} |

---

EOF

    # ── CT 段 ──
    if [ "$SCOPE" = "ct" ] || [ "$SCOPE" = "all" ]; then
        local ct="${report_dir}/TEST_SCENARIOS_CT.md"
        echo "## 一、合约测试 (CT)" >> "$report"
        echo "" >> "$report"
        echo "| CT-ID | 操作 | 预期 | 实际 | 结果 |" >> "$report"
        echo "|-------|------|------|------|------|" >> "$report"

        if [ -f "$ct" ]; then
            AT_CT_FILE="$ct"  # 供 parse_declarations/parse_tokens 查找合约代币声明
            # 从 ## chains 表自动读取 gas_price (v2.0)
            local ct_gas; ct_gas=$(get_chain_gas_price "$ct")
            [ -n "$ct_gas" ] && GAS_PRICE_GWEI="${GAS_PRICE_GWEI:-$ct_gas}"
            # 多链 RPC 解析 (v1.3)
            local ct_rpc; ct_rpc=$(get_chain_rpc "$ct")
            [ -n "$ct_rpc" ] && RPC="$ct_rpc"
            [ -n "$ct_rpc" ] && echo "🔗 CT 段链: ${ct_rpc:0:50}..."
            while IFS='|' read -r id _desc op expected _rest; do
                [ -z "$id" ] && continue
                (echo "$id" | grep -qE '^(CT-ID|AT-ID|FT-ID)$') && continue
                id=$(echo "$id" | xargs); op=$(echo "$op" | tr -d '\140' | xargs); expected=$(echo "$expected" | xargs)

                local is_blocking=false; echo "$op" | grep -q '@blocking' && is_blocking=true
                [ "$blocking_fail" -gt 0 ] && { echo "| $id | $op | $expected | ⏭️ 前置阻断 | ⏭️ |" >> "$report"; skip=$((skip+1)); continue; }

                local result="" actual=""

                # --- 链高级命令 (v1.2): chain mint/approve/swap/addLiquidity 等 ---
                if echo "$op" | grep -qiE '^chain (mint|approve|transfer|transferNFT|balanceOf|swap|addLiquidity|removeLiquidity|createPool)'; then
                    actual=$(run_chain_cmd $(echo "$op" | sed 's/^chain //') 2>&1 | sed 's/|/\|/g')
                    local pass_line; pass_line=$(echo "$actual" | grep -c 'blockHash\|transactionHash' 2>/dev/null || true)
                    if echo "$expected" | grep -qE '^[0-9]+$'; then
                        echo "$actual" | tr -d '\n' | grep -qE "[^0-9]*${expected}[^0-9]*" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qiE '^(success|=.*)'; then
                        [ "$pass_line" -gt 0 ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qiE '> 0'; then
                        # 通用余额/数量 > 0 断言
                        local just_num; just_num=$(echo "$actual" | grep -oE '[0-9]+(\.?[0-9]+)?' | head -1)
                        [ -n "$just_num" ] && [ "$just_num" != "0" ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qi '^true$'; then
                        [ "$pass_line" -gt 0 ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qi '^false$'; then
                        [ "$pass_line" -eq 0 ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qE '^0x[0-9a-fA-F]{40}$'; then
                        # 精确 42 字符地址匹配 (0x + 40 hex)
                        echo "$actual" | grep -qw "${expected}" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qE '^0x'; then
                        # 非完整地址的 hex 预期 → 大小写不敏感匹配
                        echo "$actual" | grep -qi "${expected}" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    else
                        echo "$actual" | grep -qi "$expected" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    fi
                    # Trim for display
                    actual=$(echo "$actual" | head -3 | tr '\n' ' ')

                elif echo "$op" | grep -qi "forge test"; then
                    cd "${PROJECT}/contracts" 2>/dev/null || cd "$PROJECT" 2>/dev/null || true
                    local tmpf; tmpf=$(mktemp /tmp/at_ct_XXXXXX.log)
                    if forge test -vvv 2>&1 >"$tmpf"; then
                        actual="$(grep -c 'test.*ok' "$tmpf" 2>/dev/null || echo 0)条通过"
                        result="✅"; pass=$((pass+1))
                    else
                        actual="$(grep 'FAIL' "$tmpf" | head -1 | xargs)"
                        result="❌"; fail=$((fail+1))
                        $is_blocking && blocking_fail=1
                    fi
                    rm -f "$tmpf"
                    cd - >/dev/null 2>&1 || true

                elif echo "$op" | grep -qiE "^cast call"; then
                    # 合约别名替换: ContraNFT → 0xba204d...
                    local resolved_op="$op"
                    while IFS='|' read -r name addr _; do
                        [ -n "$name" ] && [ -n "$addr" ] && resolved_op=$(echo "$resolved_op" | sed "s/\b${name}\b/${addr}/g")
                    done <<< "$(parse_declarations "$AT_CT_FILE")"
                    actual=$(cast call $(echo "$resolved_op" | sed 's/^cast call //') --rpc-url "${RPC:-${SEPOLIA_RPC:-}}" 2>&1 | head -1)
                    # 条件断言 (v2.0)
                    if echo "$expected" | grep -qE '^>=\s*[0-9]+'; then
                        local just_num threshold; threshold=$(echo "$expected" | grep -o '[0-9]\+'); just_num=$(echo "$actual" | grep -oE '[0-9]+' | head -1)
                        [ -n "$just_num" ] && [ "$just_num" -ge "$threshold" ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qE '^<=\s*[0-9]+'; then
                        local just_num threshold; threshold=$(echo "$expected" | grep -o '[0-9]\+'); just_num=$(echo "$actual" | grep -oE '[0-9]+' | head -1)
                        [ -n "$just_num" ] && [ "$just_num" -le "$threshold" ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    elif echo "$expected" | grep -qiE '^>\s*0'; then
                        local just_num; just_num=$(echo "$actual" | grep -oE '[0-9]+' | head -1)
                        [ -n "$just_num" ] && [ "$just_num" != "0" ] 2>/dev/null && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    else
                        echo "$actual" | grep -qi "$expected" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    fi

                elif echo "$op" | grep -qiE "^cast send"; then
                    local sk="${DEPLOYER_PRIVATE_KEY:-}"
                    local resolved_op="$op"
                    while IFS='|' read -r name addr _; do
                        [ -n "$name" ] && [ -n "$addr" ] && resolved_op=$(echo "$resolved_op" | sed "s/\b${name}\b/${addr}/g")
                    done <<< "$(parse_declarations "$AT_CT_FILE")"
                    actual=$(cast send $(echo "$resolved_op" | sed 's/^cast send //') --rpc-url "${RPC:-${SEPOLIA_RPC:-}}" --private-key "$sk" $gas_opt --legacy 2>&1 | sed "s/${sk//\//\\/}/***/g")
                    echo "$actual" | grep -q "0x" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }

                elif echo "$op" | grep -qiE "^cast code"; then
                    local resolved_op="$op"
                    while IFS='|' read -r name addr _; do
                        [ -n "$name" ] && [ -n "$addr" ] && resolved_op=$(echo "$resolved_op" | sed "s/\b${name}\b/${addr}/g")
                    done <<< "$(parse_declarations "$AT_CT_FILE")"
                    actual=$(cast code $(echo "$resolved_op" | sed 's/^cast code //') --rpc-url "${RPC:-${SEPOLIA_RPC:-}}" 2>&1 | head -3)
                    if echo "$expected" | grep -qi "非空\|not empty"; then
                        echo "$actual" | grep -qE '0x[0-9a-fA-F]{2,}' && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    else
                        echo "$actual" | grep -qi "$expected" && { result="✅"; pass=$((pass+1)); } || { result="❌"; fail=$((fail+1)); $is_blocking && blocking_fail=1; }
                    fi

                else
                    result="⏭️"; skip=$((skip+1)); actual="未识别: $op"
                fi

                echo "| $id | $op | $expected | ${actual:0:200} | $result |" >> "$report"
            done < <(parse_scenarios "$ct" || true)
        else
            echo "| — | 无合约测试场景 | — | TEST_SCENARIOS_CT.md 不存在 | ⏭️ |" >> "$report"
            skip=$((skip+1))
        fi
        echo "" >> "$report"
    fi

    # ── AT 段 ──
    if [ "$SCOPE" = "at" ] || [ "$SCOPE" = "all" ]; then
        local at="${report_dir}/TEST_SCENARIOS_AT.md"
        echo "## 二、API 测试 (AT)" >> "$report"
        echo "" >> "$report"
        echo "| AT-ID | 端点 | 预期状态码 | 实际 | 结果 |" >> "$report"
        echo "|-------|------|-----------|------|------|" >> "$report"

        if [ -f "$at" ]; then
            while IFS='|' read -r id method endpoint expected_code _rest; do
                [ -z "$id" ] && continue
                (echo "$id" | grep -qE '^(CT-ID|AT-ID|FT-ID)$') && continue
                id=$(echo "$id" | xargs)
                # Clean + expand ${FRONTEND} template (v2.0)
                endpoint_clean=$(echo "$endpoint" | tr -d '\140' | xargs)
                endpoint_clean=$(expand_vars "$endpoint_clean" "$at")
                method_clean=$(echo "$method" | xargs)
                expected_clean=$(echo "$expected_code" | xargs)
                local actual_code="" result=""
                # Case 1: endpoint is a full curl command (e.g. curl -s http://HOST/api/stats)
                if echo "$endpoint_clean" | grep -qi '^curl '; then
                    # 安全: 不pipe到bash, 提取URL后直接调用curl (防命令注入)
                    local curl_url flags curl_flags=""
                    # Extract URL from curl command (last argument-like token starting with http)
                    curl_url=$(echo "$endpoint_clean" | grep -oE 'https?://[^ ]+' | head -1)
                    # Extract flags: -sI/-s/-i/-I
                    echo "$endpoint_clean" | grep -q '\-sI' && curl_flags="-sI" || curl_flags="-s"
                    local curl_out
                    curl_out=$(curl ${curl_flags} --connect-timeout 5 --max-time 10 "$curl_url" 2>/dev/null || echo "CURL_FAIL")
                    # Simple: if expected looks like status code, check HTTP code; otherwise grep body
                    if echo "$expected_clean" | grep -qE '^[0-9]{3}$'; then
                        local st_code
                        curl ${curl_flags} -o /dev/null -w "%{http_code}" --connect-timeout 5 "$curl_url" 2>/dev/null && st_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$curl_url" 2>/dev/null) || st_code="000"
                        [ "$st_code" = "$expected_clean" ] && result="✅" pass=$((pass+1)) actual_code="HTTP ${st_code}" || { result="❌" fail=$((fail+1)); actual_code="HTTP ${st_code}"; }
                    else
                        echo "$curl_out" | grep -qi "$expected_clean" && result="✅" pass=$((pass+1)) actual_code="匹配" || { result="❌" fail=$((fail+1)); actual_code="不匹配"; }
                    fi
                # Case 2: endpoint is a URL, method is HTTP method
                elif echo "$endpoint_clean" | grep -qi '^https\?://'; then
                    actual_code=$(timeout 10 curl -s -o /dev/null -w "%{http_code}" -X "$method_clean" "$endpoint_clean" --connect-timeout 5 2>/dev/null || echo "000")
                    [ "$actual_code" = "$expected_clean" ] && result="✅" pass=$((pass+1)) || result="❌" fail=$((fail+1))
                else
                    result="⏭️"; skip=$((skip+1)); actual_code="未识别: $endpoint_clean"
                fi
                echo "| $id | $method_clean $endpoint_clean | $expected_clean | $actual_code | $result |" >> "$report"
            done < <(parse_scenarios "$at" || true)
        else
            echo "| — | 无 API 测试场景 | — | TEST_SCENARIOS_AT.md 不存在 | ⏭️ |" >> "$report"
            skip=$((skip+1))
        fi
        echo "" >> "$report"
    fi

    # ── FT 段 ──
    if [ "$SCOPE" = "ft" ] || [ "$SCOPE" = "all" ]; then
        local ft="${report_dir}/TEST_SCENARIOS_FT.md"
        echo "## 三、前端测试 (FT)" >> "$report"
        echo "" >> "$report"
        echo "| FT-ID | 页面 | 操作 | 预期 | 实际 | 结果 |" >> "$report"
        echo "|-------|------|------|------|------|------|" >> "$report"

        local f_url="${FRONTEND:-${FRONTEND_URL:-http://localhost:4000}}"

        if [ -f "$ft" ]; then
            while IFS='|' read -r id page action expected _rest; do
                [ -z "$id" ] && continue
                (echo "$id" | grep -qE '^(CT-ID|AT-ID|FT-ID)$') && continue
                id=$(echo "$id" | xargs); page=$(echo "$page" | xargs)
                # Clean + expand ${FRONTEND} (v2.0)
                action_clean=$(echo "$action" | tr -d '\140' | xargs)
                action_clean=$(expand_vars "$action_clean" "$ft")
                expected_clean=$(echo "$expected" | xargs)
                local url="${f_url}${page}" result="" actual=""

                # browser 命令 (v2.0) → 用 page 列作为 URL 路径
                if echo "$action_clean" | grep -qiE '^browser '; then
                    local bcmd bpath
                    bcmd=$(echo "$action_clean" | sed 's/^browser //')
                    bpath=$(echo "$bcmd" | awk '{print $NF}')  # last token = URL path
                    local target_url="${f_url}${bpath}"
                    case "$bcmd" in
                        snapshot*)
                            agent-browser open "$target_url" 2>/dev/null; sleep 2
                            local snap snap=$(agent-browser snapshot -i --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d.get('data',{}).get('snapshot','')
print(s[:500])" 2>/dev/null)
                            [ -n "$snap" ] && echo "$snap" | grep -qi "$expected_clean" && result="✅" pass=$((pass+1)) actual="含 '$expected_clean'" || { result="❌" fail=$((fail+1)); actual="快照不含 '$expected_clean'"; }
                            ;;
                        navigate*)
                            agent-browser open "$target_url" 2>/dev/null
                            result="✅" pass=$((pass+1)); actual="已打开 $target_url"
                            ;;
                        click*)
                            local sel; sel=$(echo "$bcmd" | awk '{print $2}')
                            agent-browser click "$sel" 2>/dev/null && result="✅" pass=$((pass+1)) actual="已点击 $sel" || { result="❌" fail=$((fail+1)); actual="点击失败"; }
                            ;;
                        type*)
                            local args; args=($(echo "$bcmd"))
                            agent-browser type "${args[1]}" "${args[2]}" 2>/dev/null && result="✅" pass=$((pass+1)) actual="已输入" || { result="❌" fail=$((fail+1)); actual="输入失败"; }
                            ;;
                        assert*) ;;
                        *) result="⏭️"; skip=$((skip+1)); actual="未识别 browser: $bcmd" ;;
                    esac

                elif echo "$action_clean" | grep -qi '^curl '; then
                    # 安全: 不pipe到bash, 提取URL后直接调用curl (防命令注入)
                    local curl_url; curl_url=$(echo "$action_clean" | grep -oE 'https?://[^ ]+' | head -1)
                    local curl_flags; echo "$action_clean" | grep -q '\-sI' && curl_flags="-sI" || curl_flags="-s"
                    local curl_out; curl_out=$(curl ${curl_flags} --connect-timeout 5 --max-time 10 "$curl_url" 2>/dev/null || echo "CURL_FAIL")
                    if echo "$expected_clean" | grep -qE '^[0-9]{3}$'; then
                        local st_code
                        curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$curl_url" 2>/dev/null && st_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$curl_url" 2>/dev/null) || st_code="000"
                        [ "$st_code" = "$expected_clean" ] && result="✅" pass=$((pass+1)) actual="HTTP $st_code" || { result="❌" fail=$((fail+1)); actual="HTTP $st_code"; }
                    else
                        echo "$curl_out" | grep -qi "$expected_clean" && result="✅" pass=$((pass+1)) actual="匹配" || { result="❌" fail=$((fail+1)); actual="不匹配"; }
                    fi

                elif echo "$action_clean" | grep -qi "open\|加载\|访问"; then
                    agent-browser open "$url" 2>/dev/null; sleep 2
                    actual=$(browser snapshot 2>&1 | head -1)
                    echo "$actual" | grep -q "DOM_OK" && result="✅" pass=$((pass+1)) || { result="❌" fail=$((fail+1)); actual="加载失败"; }

                elif echo "$action" | grep -qi "click\|点击"; then
                    local idx=$(echo "$action" | grep -oP '\d+' | head -1)
                    agent-browser click "$idx" 2>/dev/null && result="✅" pass=$((pass+1)) actual="点击 #$idx" || { result="❌" fail=$((fail+1)); actual="点击失败"; }

                elif echo "$action" | grep -qi "check\|检查\|验证\|包含"; then
                    agent-browser open "$url" 2>/dev/null; sleep 1
                    local c; c=$(agent-browser get markdown 2>/dev/null | head -50)
                    echo "$c" | grep -qi "$expected" && result="✅" pass=$((pass+1)) actual="包含" || { result="❌" fail=$((fail+1)); actual="不含 '$expected'"; }

                else
                    result="⏭️"; skip=$((skip+1)); actual="未识别: $action"
                fi
                echo "| $id | $page | $action | $expected | $actual | $result |" >> "$report"
            done < <(parse_scenarios "$ft" || true)
            agent-browser close 2>/dev/null || true
        else
            echo "| — | 无前端测试场景 | — | TEST_SCENARIOS_FT.md 不存在 | ⏭️ |" >> "$report"
            skip=$((skip+1))
        fi
        echo "" >> "$report"
    fi

    # ── 历史对比 (v1.3) ──
    local prev_report="${report_dir}/E2E_TEST_REPORT_PREV.md"
    echo "" >> "$report"
    echo "## 历史对比" >> "$report"
    echo "" >> "$report"
    if [ -f "$prev_report" ]; then
        # Extract pass/fail from summary row: | N | N | N | N | N% |
        # grep for the row containing "通过" and "失败" then extract 2nd and 3rd numeric columns
        local prev_pass; prev_pass=$(grep '|.*✅.*|.*❌.*|.*⏭️' "$prev_report" | head -1 | awk -F'|' '{print $2}' | grep -oE '[0-9]+' | head -1 || echo "0")
        local prev_fail; prev_fail=$(grep '|.*✅.*|.*❌.*|.*⏭️' "$prev_report" | head -1 | awk -F'|' '{print $3}' | grep -oE '[0-9]+' | head -1 || echo "0")
        local prev_total=$((prev_pass + prev_fail))
        local prev_rate=0
        [ "$prev_total" -gt 0 ] && prev_rate=$(echo "scale=1; $prev_pass * 100 / $prev_total" | bc 2>/dev/null || echo "0")

        local new_total=$((pass + fail))
        local new_rate=0
        [ "$new_total" -gt 0 ] && new_rate=$(echo "scale=1; $pass * 100 / $new_total" | bc 2>/dev/null || echo "0")

        # Diff logic
        local rate_diff; rate_diff=$(echo "$new_rate - $prev_rate" | bc 2>/dev/null || echo "0")
        local fail_diff=$((fail - prev_fail))
        local trend
        if (( $(echo "$rate_diff > 0" | bc 2>/dev/null || echo 0) )); then
            trend="📈 提升 ${rate_diff}%"
        elif (( $(echo "$rate_diff < 0" | bc 2>/dev/null || echo 0) )); then
            trend="📉 下降 ${rate_diff#-}%"
        else
            trend="➡️ 持平"
        fi

        # Find new failures and fixes by comparing CT/AT/FT IDs
        local new_fails=""; local fixed=""
        for section in "CT" "AT" "FT"; do
            local new_fail_ids; new_fail_ids=$(grep -oP "${section}-\d+.*❌" "$report" | grep -oP "${section}-\d+" 2>/dev/null || echo "")
            local prev_pass_ids; prev_pass_ids=$(grep -oP "${section}-\d+.*✅" "$prev_report" | grep -oP "${section}-\d+" 2>/dev/null || echo "")
            local prev_fail_ids; prev_fail_ids=$(grep -oP "${section}-\d+.*❌" "$prev_report" | grep -oP "${section}-\d+" 2>/dev/null || echo "")
            for fid in $new_fail_ids; do
                echo "$prev_pass_ids" | grep -qw "$fid" && new_fails="$new_fails $fid"
            done
            for fid in $(grep -oP "${section}-\d+.*✅" "$report" | grep -oP "${section}-\d+" 2>/dev/null || echo ""); do
                echo "$prev_fail_ids" | grep -qw "$fid" && fixed="$fixed $fid"
            done
        done

        cat >> "$report" << HIST
| 指标 | 上次 | 本次 | 变化 |
|------|------|------|------|
| 通过率 | ${prev_rate}% | ${new_rate}% | ${trend} |
| 失败数 | ${prev_fail} | ${fail} | ${fail_diff} |

**趋势**: ${trend}
**新增失败**: ${new_fails:-无}
**已修复**: ${fixed:-无}

HIST
    else
        echo "| 历史对比 | 无上次报告 | — | 首次运行 |" >> "$report"
        echo "" >> "$report"
    fi

    # ── 汇总 ──
    local total=$((pass + fail + skip))
    local rate=0
    [ "$((pass + fail))" -gt 0 ] && rate=$(echo "scale=1; $pass * 100 / ($pass + $fail)" | bc 2>/dev/null || echo "0")
    [ -z "$rate" ] && rate=0

    cat >> "$report" << EOF
## 汇总

| ✅ 通过 | ❌ 失败 | ⏭️ 跳过 | 总计 | 通过率 |
|---------|---------|---------|------|--------|
| ${pass} | ${fail} | ${skip} | ${total} | ${rate}% |

---

> 报告: ${report}
> 引擎: autotest v${AUTOTEST_VERSION}

EOF

    # 保存为上一份报告供下次对比 (v1.3)
    cp "$report" "${report_dir}/E2E_TEST_REPORT_PREV.md" 2>/dev/null || true

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📊 报告: $report"
    echo "   ✅ $pass  ❌ $fail  ⏭️ $skip  → ${rate}%"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── 帮助 ──────────────────────────────────────
show_help() {
    cat << 'EOF'
╔═══════════════════════════════════════════════╗
║  autotest v1.0 — AutoOps 通用自动化测试引擎  ║
╚═══════════════════════════════════════════════╝

项目无关，任何项目遵循场景文件约定即可用。

用法:
  autotest run --project <path> [--scope ct|at|ft|all]
  autotest selfcheck --project <path>
  autotest data {email|phone|username|eth-address|uuid|long-string|special-chars|empty|null} [--tag X]
  autotest browser {open|snapshot|elements|content|click|type|screenshot|close}
  autotest chain {call|send|receipt|balance|code|block}
  autotest db {psql|redis}
  autotest assert {status|tx-confirmed|page-contains|page-not-contains|page-title|element-count|balance|db-row|event-emitted|notification|file-exists|file-contains|response-time|screenshot-match}

项目约定:
  {项目}/test-reports/TEST_SCENARIOS_CT.md   → | CT-ID | 描述 | 操作 | 预期 |
    v1.3: 可选 ## chains 段声明链和RPC
  {项目}/test-reports/TEST_SCENARIOS_AT.md   → | AT-ID | 方法 | 端点 | 预期状态码 |
  {项目}/test-reports/TEST_SCENARIOS_FT.md   → | FT-ID | 页面 | 操作 | 预期 |
  {项目}/test-reports/E2E_TEST_REPORT.md     ← 产出

场景标记:
  @blocking   — 失败则停止当前阶段
  @depends ID — 前置必须通过

环境变量:
  FRONTEND_URL  SEPOLIA_RPC  DEPLOYER_PRIVATE_KEY
  DB_HOST DB_PORT DB_USER DB_PASS DB_NAME
EOF
}

# ── 入口 ──────────────────────────────────────
case "$CMD" in
    run)        run_all "$@" ;;
    selfcheck)  selfcheck "$@" ;;
    data)       data "$@" ;;
    browser)    browser "$@" ;;
    chain)      chain "$@" ;;
    db)         db "$@" ;;
    assert)     assert "$@" ;;
    help|--help|-h) show_help ;;
    *) show_help ;;
esac
