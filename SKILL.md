# autotest v1.3 — AutoOps 通用自动化测试引擎

## 定位

autoops 项目产出的核心 Skill。让 agent 像真人一样做测试：操作浏览器 → 发链上交易（9种高级命令）→ 查数据库 → 真实验证。

**v1.3 更新**：多链支持（## chains 声明段）+ 历史报告对比（新增失败/已修复/趋势）

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
  TEST_SCENARIOS_CT.md         → 合约测试
  TEST_SCENARIOS_AT.md         → API 测试
  TEST_SCENARIOS_FT.md         → 前端测试
  E2E_TEST_REPORT.md           ← autotest 产出
  E2E_TEST_REPORT_PREV.md      ← 自动保存的上次报告
```

## CT 段完整模板 (v1.3)

```markdown
# TEST_SCENARIOS_CT — 合约测试

## chains
| 链名 | RPC |
|------|-----|
| sepolia | $SEPOLIA_RPC |

## contracts
| 合约名 | 地址 | 类型 |
|--------|------|------|
| USDC    | 0xTokenA         | ERC20 |
| Router  | 0xUniswapRouter  | UniswapV2Router |

## tokens
| 别名 | 地址 | 小数位 |
|------|------|--------|
| USDC | 0xTokenA | 6 |
| WETH | 0xWETH   | 18 |

## scenarios
| CT-ID | 描述 | 操作 | 预期 |
|-------|------|------|------|
| CT-001 | 铸造 USDC | chain mint USDC wallet-1 1000000 | success |
| CT-002 | Swap | chain swap Router USDC WETH 100 wallet-1 | success |
```

## 多链支持 (v1.3)

`## chains` 段可选。不写默认 Sepolia。

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

## 历史对比 (v1.3)

每次运行自动保存 `E2E_TEST_REPORT_PREV.md`，下次运行时对比：

| 指标 | 显示 |
|------|------|
| 通过率变化 | 📈 提升 / 📉 下降 / ➡️ 持平 |
| 新增失败 | 本次 ❌ 但上次 ✅ 的场景 ID |
| 已修复 | 本次 ✅ 但上次 ❌ 的场景 ID |

## Chain 高级命令

| 命令 | 参数 | 自动处理 |
|------|------|---------|
| `chain mint` | token wallet amount | approve + mint |
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
