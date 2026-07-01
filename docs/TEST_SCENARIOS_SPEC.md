# TEST_SCENARIOS 场景文档编写规范 v2.0

> autotest v2.0+ · 架构师按此规范编写 · 场景文件是单一事实来源

---

## ⚠️ OpenClaw Sandbox 网络隔离

**OpenClaw 子 Agent（tester 等）运行在 Docker sandbox 中，默认 `network="none"`。**
这是 OpenClaw 的安全设计（非 bug），子 Agent sandbox 无法直接访问外网 URL 或测试服务器。

**架构师 spawn tester 前，必须先建 SSH 隧道：**

```bash
# 前端穿透：宿主机 localhost:5000 → 测试服务器前端
ssh -fN -L 5000:localhost:3080 root@43.159.39.85
```

**场景文件中的 `## declarations` 使用隧道地址（localhost），不是真实 IP：**

```markdown
## declarations
| 键 | 值 |
|----|----|
| frontend | http://localhost:5000 |
| relay_api | http://localhost:5000/api |
```

> 链上 RPC（`cast call`/`cast send`）不受影响 —— 它们直接访问公网 RPC 节点，不走隧道。

---

## 架构师快速上手 (4 步)

拿到一个新项目后，按以下流程编写场景文档：

### Step 0: 建 SSH 隧道（给 tester 用）

```bash
ssh -fN -L 5000:localhost:3080 root@43.159.39.85
```

### Step 1: 填写声明

打开 `test-reports/TEST_SCENARIOS_CT.md`，补全头部声明：

```markdown
## chains (需要哪些链? RPC? gas 费用?)
| 链名 | RPC | gas_price_gwei |
| sepolia | $SEPOLIA_RPC | 100 |

## declarations (前端在哪? API 在哪?)
| 键 | 值 |
| frontend | http://43.159.39.85:3080 |
| relay_api | http://43.159.39.85:3080/api |

## contracts (有哪些合约? 什么类型? → 影响 chain mint 签名)
| 合约名 | 地址 | 类型 |
| ContraNFT | 0xF2eAF... | NFT |
| USDC | 0x286D... | ERC20 |

## tokens (ERC20 小数位? → 影响 chain approve/transfer 金额换算)
| 别名 | 地址 | 小数位 |
| USDC | 0x286D... | 6 |
```

### Step 2: 编写场景

```markdown
## scenarios
| CT-ID | 类型 | 操作 | 预期 |
| CT-001 | call | cast call ContraNFT "name()(string)" | Contra AI |
| CT-002 | tx | chain mint ContraNFT owner | success |
| CT-003 | call | cast call ContraNFT "totalMinted()(uint256)" | > 0 |
```

**类型选择**：`call`(只读查询) / `tx`(链上交易) / `curl`(HTTP) / `browser`(浏览器)

**预期策略**：精确值用数字/地址，递增用 `> 0` 或 `>=N`，交易成功用 `success`

### Step 3: 运行验证

```bash
source ~/.openclaw/workspace/.sepolia.env
autotest run --project {项目路径} --scope all
```

---

## 核心原则

1. **场景文件 = 完整声明** — 所有 autotest 需要的信息都在场景文件里，不允许额外手动配置（如 Relay config.js 硬编码）
2. **按类型路由，不按段路由** — `cast call` 和 `chain mint` 在 CT/AT/FT 任意段都应该能执行，不限制段
3. **断言语义化** — `> 0`、`>=5`、`contains`、`200`（状态码）等，autotest 按预期格式自动选择匹配策略

---

## 文件约定

| 要求 | 说明 |
|------|------|
| 文件名 | `TEST_SCENARIOS_CT.md` / `_AT.md` / `_FT.md`（固定） |
| 目录 | `{项目根目录}/test-reports/` |
| 格式 | Markdown 表格，声明段 + 场景段 |
| 编码 | UTF-8 |

---

## 完整文件结构

```
## chains (可选)
## declarations (可选, v2.0 新增: 环境声明)
## contracts (可选)
## tokens (可选)
## scenarios (必填)
```

### ## chains — 链 RPC + gas 配置 (v2.0 扩展)

```markdown
## chains
| 链名 | RPC | gas_price_gwei |
|------|-----|---------------|
| sepolia | $SEPOLIA_RPC | 50 |
```

- `gas_price_gwei` 可选，填了 autotest 自动对 cast send 加 `--gas-price {N}000000000`
- 环境变量 `$SEPOLIA_RPC` 自动展开
- 支持的链名: sepolia / mainnet / arbitrum / base / monad / bsc / polygon

### ## declarations — 环境声明 (v2.0 新增)

```markdown
## declarations
| 键 | 值 |
|----|-----|
| frontend | http://43.159.39.85:3080 |
| relay_api | http://43.159.39.85:3080/api |
| env_file | ~/.openclaw/workspace/.sepolia.env |
```

- `frontend`: autotest 自动用于 AT/FT 段的 HTTP 请求基础路径
- `relay_api`: Relay API 地址（可选，覆盖 frontend）
- autotest 解析后不再需要 `--frontend` 参数

### ## contracts — 合约声明

```markdown
## contracts
| 合约名 | 地址 | 类型 |
|--------|------|------|
| ContraNFT | 0xF2eAF1048090d40d7035FFC81541254f3cA9dD91 | NFT |
| Treasury | 0x6d250E302b8217FEe26e41F150978769b440E29A | Treasury |
| USDC | 0x286D18bc7aFa5DC8Af7FdF93fAb544849E972479 | ERC20 |
```

**类型列约定 (v2.0)**:

| 类型关键词 | 影响 |
|-----------|------|
| 含 `NFT` 或 `ERC721` | `chain mint` 用无参 `mint()` |
| 含 `ERC20` 或 `Token` | `chain mint` 用 `mint(address,uint256)` |
| 含 `Treasury` | 留空，不做类型判断 |
| 其他或无 | 默认按 ERC20 处理 |

### ## tokens — 代币声明

```markdown
## tokens
| 别名 | 地址 | 小数位 |
|------|------|--------|
| USDC | 0x286D18bc7aFa5DC8Af7FdF93fAb544849E972479 | 6 |
```

### ## scenarios — 测试场景

四列表格:

| 列 | 含义 |
|----|------|
| ID | `CT-001` / `AT-001` / `FT-001` |
| 类型 | `call` / `tx` / `curl` / `browser` (v2.0 统一) |
| 操作 | 具体命令 |
| 预期 | 断言表达式 |

---

## 操作类型清单 (v2.0 统一)

### 全段通用命令

| 类型 | 操作格式 | 示例 | 适用段 |
|------|---------|------|--------|
| `call` | cast call 合约 "sig" [args] | `cast call ContraNFT "name()(string)"` | CT/AT/FT |
| `tx` | chain 语义命令 | `chain mint ContraNFT owner` | CT/AT/FT |
| `tx` | cast send 合约 "sig" args | `cast send 0xADDR "mint(address)" 0xTO` | CT/AT/FT |
| `tx` | forge create src/X.sol:X | `forge create src/ContraNFT.sol:ContraNFT` | CT |
| `curl` | curl 命令 | `curl -s http://HOST/api/stats` | AT/FT |
| `browser` | browser 命令 | `browser snapshot /` | FT |
| `forge` | forge test | `forge test` | CT |

### chain 语义命令

| 命令 | 参数 | 说明 |
|------|------|------|
| `chain mint` | `<token> <wallet> [amount]` | NFT→无参mint(), ERC20→mint(address,uint256) |
| `chain approve` | `<token> <spender> [amount] <wallet>` | ERC20 授权 |
| `chain transfer` | `<token> <to> <wallet> <amount>` | 转账 |
| `chain transferNFT` | `<nft> <to> <tokenId> <wallet>` | NFT 转移 |
| `chain balanceOf` | `<token> <wallet>` | 查余额 |
| `chain swap` | `<router> <tokenIn> <tokenOut> <amountIn> <wallet>` | DEX 兑换 |
| `chain addLiquidity` | `<router> <tokenA> <amountA> <tokenB> <amountB> <wallet>` | 添加 LP |
| `chain removeLiquidity` | `<router> <tokenA> <tokenB> <lpAmount> <wallet>` | 移除 LP |
| `chain createPool` | `<factory> <tokenA> <tokenB> <wallet>` | 创建交易对 |

### browser 命令 (v2.0 新增)

| 命令 | 参数 | 说明 |
|------|------|------|
| `browser snapshot` | `<url-path>` | 打开页面并获取 DOM 快照 |
| `browser navigate` | `<url-path>` | 打开页面 |
| `browser click` | `<selector>` | 点击元素 |
| `browser type` | `<selector> <text>` | 输入文本 |
| `browser assert` | `<text>` | 断言页面包含文字 |

---

## 断言表达式 (v2.0 扩展)

autotest 根据预期列内容自动选择匹配策略：

| 预期写法 | 匹配策略 | 示例场景 |
|---------|---------|---------|
| `200` / `404` 等纯数字 | HTTP 状态码精确匹配 (curl) | `curl -sI http://...` |
| `> 0` | 输出中首个数字 > 0 | `totalMinted()(uint256)` |
| `>=5` | 输出中首个数字 >= N | `balanceOf()` |
| `success` | 有 txHash/blockHash 即成功 | `chain mint/approve` |
| `true` | txHash 存在 | 交易成功 |
| `false` | txHash 不存在 | 交易被拒绝 |
| `0x...` (42 字符) | 地址精确匹配 | `owner()` |
| `0x...` (短 hex) | 大小写不敏感包含 | 部分 hex 匹配 |
| `not empty` | bytecode 非空 | `cast code` |
| 其他文本 | 大小写不敏感 grep | `Contra AI`, `totalMinted` |

### 条件断言 (v2.0 新增)

| 预期写法 | 说明 |
|---------|------|
| `>=5` | 数字 ≥ 5 |
| `<=10` | 数字 ≤ 10 |
| `> 0` | 大于 0 |
| `=0` 或 `0` | 等于 0 |

---

## 完整示例 — ContraNFT v3

```markdown
## chains
| 链名 | RPC | gas_price_gwei |
|------|-----|---------------|
| sepolia | $SEPOLIA_RPC | 100 |

## declarations
| 键 | 值 |
|----|-----|
| frontend | http://43.159.39.85:3080 |
| relay_api | http://43.159.39.85:3080/api |

## contracts
| 合约名 | 地址 | 类型 |
|--------|------|------|
| ContraNFT | 0xF2eAF1048090d40d7035FFC81541254f3cA9dD91 | NFT |
| Treasury | 0x6d250E302b8217FEe26e41F150978769b440E29A | Treasury |
| USDC | 0x286D18bc7aFa5DC8Af7FdF93fAb544849E972479 | ERC20 |

## tokens
| 别名 | 地址 | 小数位 |
|------|------|--------|
| USDC | 0x286D18bc7aFa5DC8Af7FdF93fAb544849E972479 | 6 |

## scenarios
| CT-ID | 类型 | 操作 | 预期 |
|-------|------|------|------|
| CT-001 | call | cast call ContraNFT "name()(string)" | Contra AI |
| CT-002 | call | cast call ContraNFT "owner()(address)" | 0x0C5D732F9f70D4A192e86E3B3FCDFf5797D2638d |
| CT-003 | call | cast call ContraNFT "mintPrice()(uint256)" | 1 |
| CT-004 | call | cast call ContraNFT "maxSupply()(uint256)" | 100 |
| CT-005 | call | cast call ContraNFT "paused()(bool)" | false |
| CT-006 | tx | chain approve USDC ContraNFT max owner | success |
| CT-007 | tx | chain mint ContraNFT owner | success |
| CT-008 | call | cast call ContraNFT "ownerOf(uint256)(address)" 1 | 0x0C5D732F9f70D4A192e86E3B3FCDFf5797D2638d |
| CT-009 | call | cast call ContraNFT "totalMinted()(uint256)" | > 0 |
```

FT 段示例:
```markdown
## declarations
| 键 | 值 |
|----|-----|
| frontend | http://43.159.39.85:3080 |

## scenarios
| FT-ID | 类型 | 操作 | 预期 |
|-------|------|------|------|
| FT-001 | curl | curl -sI ${FRONTEND}/ | 200 |
| FT-002 | curl | curl -s ${FRONTEND}/ | Contra |
| FT-003 | browser | browser snapshot / | Contra AI |
```

- `${FRONTEND}` 在 autotest 解析时自动替换为 declarations.frontend

---

## 版本兼容

| 版本 | 变更 |
|------|------|
| v2.0 | 新增: `## declarations`, `gas_price_gwei`, browser 命令, `${FRONTEND}` 模板, 条件断言(>=/<=/>), cast call 全段可用 |
| v1.3 | 新增: `## chains`, 多链 RPC |
| v1.2 | 新增: chain 语义命令, `## contracts`, `## tokens` |

---

## 禁止事项

| ❌ | ✅ |
|----|-----|
| 操作列用反引号包裹 | 直接写命令 |
| AT/FT 段写 Docker 内部端口 (:3001) | 用对外地址或 `${FRONTEND}` |
| 场景文件放在 `test-reports/` 外 | 固定目录 |
| 用例预期写 `totalMinted()(uint256) \| 14` (精确值) | 用 `> 0` 避免每次 mint 追数 |
| chain 命令不声明类型列 | `## contracts` 里写 NFT/ERC20/Treasury |
