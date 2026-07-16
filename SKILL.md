# autotest v2.0 — AutoOps 通用自动化测试引擎

## 定位

autoops 项目产出的核心 Skill。让 agent 像真人一样做测试：操作浏览器 → 发链上交易（9种高级命令）→ 查数据库 → 真实验证。

**v2.0 更新**：声明的 `${FRONTEND}` `${RELAY_API}` 模板变量展开 + `## declarations` 段 + 条件断言(>=/<=/>) + browser snapshot/navigate 集成 + gas 自动读取

## ⚠️ OpenClaw Sandbox 网络说明

**OpenClaw 子 Agent（tester 等）运行在 Docker sandbox 中，默认 network="none"。**
这是 OpenClaw 的安全设计，不是 bug。子 Agent sandbox 无法直接访问外网（URL、RPC、链上）。

**解决方案：SSH 隧道**

在 spawn tester 前，由架构师在宿主机上建立 SSH 隧道：

```bash
# 前端穿透（tester sandbox → 宿主机 localhost:端口 → 测试服务器前端）
ssh -fN -L 5000:localhost:3080 root@43.159.39.85

# 子 Agent 场景文件使用 localhost 而非真实 IP
# ${FRONTEND} = http://localhost:5000
```

子 Agent 场景文件中的 `## declarations` 应使用隧道地址（架构师 spawn 时替换）：

```markdown
## declarations
| 键 | 值 |
|----|----|
| frontend | http://localhost:5000 |
| relay_api | http://localhost:5000/api |
```

**链上 RPC 不受影响** — `cast call`/`cast send` 通过 RPC 直接访问公网节点，不走隧道。

## 命令体系

```
autotest run                                    一键编排 (CT→AT→FT, 含历史对比)
autotest selfcheck                              启动前配置自检 (含多链RPC)
autotest data                                   测试数据工厂
autotest browser                                浏览器真实操作
autotest chain                                  链上真实交易
autotest db                                     数据库查询
autotest assert                                 真实验证断言
```

## 项目约定

```
{项目}/test-reports/
  TEST_SCENARIOS_CT.md         → 合约测试 (含 ## chains/contracts/tokens/declarations)
  TEST_SCENARIOS_AT.md         → API 测试
  TEST_SCENARIOS_FT.md         → 前端测试 (含 browser snapshot/navigate 等)
  E2E_TEST_REPORT.md           ← autotest 产出
  E2E_TEST_REPORT_PREV.md      ← 自动保存的上次报告
```

## CT 段完整模板 (v2.0)

```markdown
# TEST_SCENARIOS_CT — 合约测试

## chains
| 链名 | RPC | gas_price_gwei |
|------|-----|---------------|
| sepolia | $SEPOLIA_RPC | 100 |

## declarations
| 键 | 值 |
|----|-----|
| frontend | http://localhost:5000 |
| relay_api | http://localhost:5000/api |

## contracts
| 合约名 | 地址 | 类型 |
|--------|------|------|
| ContraNFT | 0xF2eAF... | NFT |
| USDC | 0x286D... | ERC20 |

## tokens
| 别名 | 地址 | 小数位 |
|------|------|--------|
| USDC | 0x286D... | 6 |

## scenarios
| CT-ID | 类型 | 操作 | 预期 |
|-------|------|------|------|
| CT-001 | call | cast call ContraNFT "name()(string)" | Contra AI |
| CT-002 | call | cast call ContraNFT "totalMinted()(uint256)" | > 0 |
| CT-003 | tx | chain mint ContraNFT owner | success |
```

## 声明展开优先级 (v2.0)

`${FRONTEND}` `${RELAY_API}` 展开按以下优先级查找：

1. 当前文件 `## declarations` 段
2. CT 文件 `## declarations` 段 (fallback)
3. 环境变量 `FRONTEND` / `FRONTEND_URL` (兜底)
4. CLI 参数 `--frontend` (仅 FT 段)

## 条件断言 (v2.0)

| 格式 | 含义 | 示例 |
|------|------|------|
| `> 0` | 大于 0 | totalMinted > 0 ✅ |
| `>=N` | 大于等于 N | totalMinted >= 5 |
| `<=N` | 小于等于 N | allowance <= max |
| 精确值 | 精确匹配 | `Contra AI` / `0xF2eAF...` |

## 多链支持 (v1.3+)

| 链名 | 环境变量 | 说明 |
|------|---------|------|
| sepolia | `$SEPOLIA_RPC` | 测试网（默认） |
| sepolia_2 | `$SEPOLIA_RPC_2` | Sepolia 备用 |
| mainnet | `$ETH_RPC` | 以太坊主网 |
| arbitrum | `$ARB_RPC` | Arbitrum One |
| base | `$BASE_RPC` | Base |
| monad | `$MONAD_RPC` | Monad |
| bsc | `$BSC_RPC` | BNB Chain |
| polygon | `$POLYGON_RPC` | Polygon |

## 历史对比 (v1.3+)

每次运行自动保存 `E2E_TEST_REPORT_PREV.md`，下次运行时对比：

| 指标 | 显示 |
|------|------|
| 通过率变化 | 📈 提升 / 📉 下降 / ➡️ 持平 |
| 新增失败 | 本次 ❌ 但上次 ✅ 的场景 ID |
| 已修复 | 本次 ✅ 但上次 ❌ 的场景 ID |

## Chain 高级命令

| 命令 | 参数 | 自动处理 |
|------|------|---------|
| `chain mint` | token wallet amount | 类型感知: NFT→mint(), ERC20→mint(address,uint256) |
| `chain approve` | token spender wallet [amount] | — |
| `chain transfer` | token to wallet amount | — |
| `chain transferNFT` | nft to tokenId wallet | — |
| `chain balanceOf` | token wallet | — |
| `chain swap` | router tokenIn tokenOut amountIn wallet | approve + getAmountsOut + swap |
| `chain addLiquidity` | router tokenA amtA tokenB amtB wallet | approve(A)+approve(B)+add |
| `chain removeLiquidity` | router tokenA tokenB lpAmt wallet | approve(LP)+remove |
| `chain createPool` | factory tokenA tokenB wallet | createPair + 返回地址 |

## 场景标记

| 标记 | 说明 |
|------|------|
| `@blocking` | 失败停止当前阶段 |
| `@depends CT-001` | 前置必须通过 |

## 快速使用

```bash
source ~/.openclaw/workspace/.sepolia.env
autotest run --project /path/to/project --scope all

# 全局命令: /usr/local/bin/autotest
```
