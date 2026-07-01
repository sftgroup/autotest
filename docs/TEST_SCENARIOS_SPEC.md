# TEST_SCENARIOS 场景文档编写规范

> autotest v1.2 要求，架构师按此规范编写测试场景，autotest 自动解析执行。

---

## CT 段完整结构 (v1.3)

CT 段支持 4 个声明段 + 1 个场景段。`## chains` 和 `## contracts` 和 `## tokens` 均可选。

```
## chains (可选, v1.3)  → 声明链和RPC, 不写默认Sepolia
## contracts (可选)     → 声明合约（名称→地址→类型）
## tokens (可选)        → 声明代币（别名→地址→decimals）
## scenarios (必填)     → 测试用例
```

### ## chains 段

```markdown
## chains
| 链名 | RPC |
|------|-----|
| sepolia | https://sepolia.infura.io/v3/xxx |
```

支持的链: sepolia / mainnet / arbitrum / base / monad / bsc / polygon

---

## 文件约定

| 要求 | 说明 |
|------|------|
| 文件名 | `TEST_SCENARIOS_CT.md` / `_AT.md` / `_FT.md`（固定，不可改名） |
| 目录 | 必须放在 `{项目根目录}/test-reports/` 下 |
| 格式 | Markdown 表格（4 列 scenarios）+ 可选声明段（contracts/tokens 2 列表格） |
| ID 前缀 | `CT-`（合约测试）/ `AT-`（API 测试）/ `FT-`（前端测试） |
| 编码 | UTF-8 |

---

## CT 段文件结构（v1.2 扩展）

CT 段支持**声明段 + 场景段**两段式结构。声明段不是必填——如果不使用 `chain` 高级命令，可以直接写 `## scenarios`。

```markdown
# TEST_SCENARIOS_CT — 合约测试

> RPC: $SEPOLIA_RPC
> 测试私钥: source ~/.openclaw/workspace/.test-wallets.env

## contracts
| 合约名 | 地址 | 类型 |
|--------|------|------|
| USDC     | 0xTokenA         | ERC20 |
| NFT      | 0xContraNFT      | ERC721 |
| Router   | 0xUniswapRouter  | UniswapV2Router |
| Factory  | 0xFactory        | UniswapV2Factory |
| Pair     | 0xPairAddr       | UniswapV2Pair |

## tokens
| 别名 | 地址 | 小数位 |
|------|------|--------|
| USDC | 0xTokenA    | 6 |
| WETH | 0xWETH      | 18 |
| LP-USDC-WETH | 0xPairAddr | 18 |

## scenarios
| CT-ID | 描述 | 操作 | 预期 |
|-------|------|------|------|
| CT-001 | 铸造 USDC | chain mint USDC wallet-1 1000000 | 1000000 |
| CT-002 | 授权 Router | chain approve USDC Router wallet-1 | true |
| CT-003 | 添加流动性 | chain addLiquidity Router USDC 1000 WETH 1 wallet-1 | success |
| CT-004 | Swap | chain swap Router USDC WETH 100 wallet-1 | success |
| CT-005 | 移除流动性 | chain removeLiquidity Router USDC WETH 50 wallet-1 | success |
| CT-006 | 创建池子 | chain createPool Factory USDC WETH wallet-1 | 0x  |
| CT-007 | 转移 NFT | chain transferNFT NFT wallet-1 wallet-2 1 | success |
```

---

## Chain 高级命令清单（v1.2）

### 命令总表

| 命令 | 参数 | 自动处理 | 适用合约类型 |
|------|------|---------|-------------|
| `chain mint` | `<token> <wallet> <amount>` | approve(若需要) + mint | ERC20 / ERC721 |
| `chain approve` | `<token> <spender> <wallet> [amount]` | — | ERC20 |
| `chain transfer` | `<token> <to> <wallet> <amount>` | — | ERC20 / ETH |
| `chain transferNFT` | `<nft> <to> <tokenId> <wallet>` | — | ERC721 |
| `chain balanceOf` | `<token> <wallet>` | — | ERC20 / ERC721 |
| `chain swap` | `<router> <tokenIn> <tokenOut> <amountIn> <wallet>` | approve + getAmountsOut + swapExactTokens | UniswapV2 |
| `chain addLiquidity` | `<router> <tokenA> <amountA> <tokenB> <amountB> <wallet>` | approve(A) + approve(B) + addLiquidity | UniswapV2 |
| `chain removeLiquidity` | `<router> <tokenA> <tokenB> <lpAmount> <wallet>` | approve(LP) + removeLiquidity | UniswapV2 |
| `chain createPool` | `<factory> <tokenA> <tokenB> <wallet>` | createPair + 返回新 pair 地址 | UniswapV2Factory |

### 参数约定

| 参数类型 | 格式 | 示例 | 如何解析 |
|----------|------|------|---------|
| **合约名** | 别名（与 ## contracts 匹配） | `Router`, `USDC`, `Factory` | 查 contracts 表 → 地址 |
| **代币名** | 别名（与 ## tokens 匹配） | `USDC`, `WETH`, `LP-USDC-WETH` | 查 tokens 表 → 地址 + 小数位 |
| **钱包** | `wallet-N` 别名 | `wallet-1`, `wallet-2` | `.test-wallets.env` → `TEST_N_SK` |
| **金额** | 人类可读（自动 x 10^decimals） | `1000`, `1.5` | bc 计算 wei |

### 执行流程示例（chain swap）

```
操作列: chain swap Router USDC WETH 100 wallet-1

1. 查 contracts 表: Router → 0xF...
2. 查 tokens 表:  USDC→0xA...(6), WETH→0xW...(18)
3. 查钱包:        wallet-1 → TEST_1_SK
4. cast call Router.getAmountsOut(100000000, [USDC, WETH])  → minOut
5. cast send USDC.approve(Router, 100000000)                 → approve
6. cast send Router.swapExactTokensForTokens(...)            → swap
7. 预期匹配: success → 只要有 txHash 就 ✅
```

### 预期列判断规则（chain 命令）

| 预期写法 | 判断逻辑 | 示例 |
|----------|---------|------|
| `1000000` (纯数字) | 输出中是否出现该数字 | mint 后查总量 |
| `true` / `false` | transactionHash 存在 = true | approve 是否成功 |
| `success` | 同 true，交易成功 | swap/addLiquidity 等 |
| `LP > 0` / `WETH > 0` | 同 true | 交易成功即可 |
| `0x...` (地址) | 输出是否包含该地址 | createPool 返回 pair 地址 |
| 其他文字 | grep 匹配 | — |

---

## AT 段文件结构

```markdown
# TEST_SCENARIOS_AT — API 测试

> 测试服务器: <HOST>

| AT-ID | 方法 | 端点 | 预期 |
|-------|------|------|------|
| AT-001 | GET | curl -s http://HOST/api/stats | totalMinted |
| AT-002 | GET | curl -s http://HOST/api/account/0xADDR | 200 |
| AT-003 | HEAD | curl -sI http://HOST/ | 200 |
| AT-004 | POST | curl -s -X POST http://HOST/api/register -d '{}' | 201 |
```

**预期列判断**：
- 纯数字（如 `200`）→ HTTP 状态码匹配
- 其他文字 → body 内容 grep 匹配

**⚠️ 禁止**：在端点列使用 Docker 内部端口（如 `:3001`/`:3080`），必须使用对外可达地址。

---

## FT 段文件结构

```markdown
# TEST_SCENARIOS_FT — 前端测试

> 前端 URL: <URL>

| FT-ID | 页面 | 操作 | 预期 |
|-------|------|------|------|
| FT-001 | / | curl -sI http://HOST/ | 200 |
| FT-002 | / | 加载首页 | DOM > 10 |
| FT-003 | / | 检查标题 | 包含 Contra |
| FT-004 | /mint | 打开 | 200 |
```

**操作列支持**：
- `curl -sI http://...` → HTTP 状态码验证
- `curl -s http://...` → body 内容匹配
- `加载首页` / `打开` → browser open
- `点击 #5` → browser click
- `检查标题` / `验证` → page content check

---

## 场景标记

| 标记 | 可用段 | 效果 |
|------|--------|------|
| `@blocking` | CT / AT / FT | 此条失败则停止当前阶段 |
| `@depends CT-001` | CT / AT / FT | 前置场景必须通过才执行 |

```markdown
| CT-002 | 部署合约 | forge create src/MyNFT.sol | @blocking |
| CT-003 | 铸造 NFT | cast send 0x... "mint(address)" 0xBuyer | @depends CT-002 |
```

---

## 操作命令清单

### CT 段支持的命令

| 命令类型 | 示例 | 说明 |
|---------|------|------|
| **chain mint** | `chain mint USDC wallet-1 1000000` | 铸造代币 (v1.2) |
| **chain approve** | `chain approve USDC Router wallet-1` | 授权合约 (v1.2) |
| **chain transfer** | `chain transfer USDC wallet-2 wallet-1 100` | 转移代币 (v1.2) |
| **chain transferNFT** | `chain transferNFT NFT wallet-2 1 wallet-1` | 转移 NFT (v1.2) |
| **chain balanceOf** | `chain balanceOf USDC wallet-1` | 查询余额 (v1.2) |
| **chain swap** | `chain swap Router USDC WETH 100 wallet-1` | DEX 兑换 (v1.2) |
| **chain addLiquidity** | `chain addLiquidity Router USDC 1000 WETH 1 wallet-1` | 添加流动性 (v1.2) |
| **chain removeLiquidity** | `chain removeLiquidity Router USDC WETH 50 wallet-1` | 移除流动性 (v1.2) |
| **chain createPool** | `chain createPool Factory USDC WETH wallet-1` | 创建交易对 (v1.2) |
| cast call | `cast call 0x... "func()"` | 合约只读查询 |
| cast send | `cast send 0x... "mint(address)" 0xADDR` | 发送交易 |
| cast code | `cast code 0x...` | 验证合约 bytecode |
| forge test | `forge test` | 跑 Foundry 测试 |
| forge create | `forge create src/X.sol:X` | 部署合约 |

### AT 段支持的命令

| 命令类型 | 示例 | 说明 |
|---------|------|------|
| curl GET | `curl -s http://HOST/api/stats` | GET + body 匹配 |
| curl HEAD | `curl -sI http://HOST/` | HEAD + 状态码 |
| curl POST | `curl -s -X POST http://HOST/api/register -d '{}'` | POST 请求 |

### FT 段支持的命令

| 命令类型 | 示例 | 说明 |
|---------|------|------|
| curl | `curl -sI http://HOST/` | HTTP 状态码验证 |
| browser open | `加载首页` / `打开` | 打开浏览器页面 |
| browser click | `点击 #5` | 点击元素 |
| browser snapshot | `snapshot` | 获取 DOM 快照 |

---

## 禁止事项

| ❌ | ✅ 正确写法 |
|----|------------|
| 操作列用反引号包裹 | 直接写命令 |
| 预期列写长篇描述 | 简洁值 |
| 文件名自由命名 | 固定 `TEST_SCENARIOS_CT.md` |
| 场景文件放其他目录 | 放在 `test-reports/` 下 |
| AT/FT 段使用 Docker 内部端口 | 使用对外可达地址 |
| chain 命令不声明 contracts/tokens | 在 ## contracts 和 ## tokens 段声明 |
