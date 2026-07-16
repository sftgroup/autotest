# autotest-mcp — 自动化测试平台

## 这是什么？

一套**独立部署**的自动化测试系统。你能用它：

> **"测试我的智能合约"** → 它帮你编译、跑测试、发交易、验结果
> **"测试我的 DApp 前端"** → 它帮你打开浏览器、点按钮、截图、对比
> **"测试我的 API"** → 它帮你发请求、验状态码、做模糊攻击测试
> **"审计合约安全"** → 它帮你跑 Slither、Echidna、Halmos
> **"测试整个 Swap 流程"** → 发交易 → 等确认 → 打开前端看余额变了没，一个命令搞定

**你的 OpenClaw agent 直接调用这些能力，自动完成测试。**

---

## 一句话解释

```
你(飞书/终端) → OpenClaw Agent → MCP Server(测试服务器) → 真实的链/浏览器/API
                                                              ↓
                                                         返回: ✅ passed / ❌ failed
```

---

## 它能做什么？（按场景）

### 🪙 你要测智能合约

```
Agent 自动做的事:
1. forge build       → 编译通过了吗？
2. forge test -vvv    → 测试用例都过了吗？
3. slither .          → 有安全漏洞吗？（可选）
4. 返回: 编译✅ | 测试 10/10 ✅ | 安全问题 0 个 ✅
```

### 🌐 你要测 DApp 前端

```
Agent 自动做的事:
1. 打开 Chrome       → 页面能打开吗？
2. 截屏              → 长什么样？
3. 检查关键文字      → "Welcome" 在页面上吗？
4. 对比上次截图       → 页面变了吗？（视觉回归）
5. 返回: 页面✅ | 文字✅ | 截图对比✅
```

### 💱 你要测 Swap 全流程（链+前端联调）

```
Agent 自动做的事:
1. 发 approve 交易   → 授权成功了吗？
2. 发 swap 交易      → 换到了吗？
3. 查链上余额         → USDC 少了, WETH 多了吗？
4. 打开 DApp 前端     → 前端显示的余额对吗？
5. 截屏              → 页面状态截图
6. 返回: 全部✅ 或 具体哪步❌
```

### 🔐 你要审计合约安全

```
Agent 自动做的事:
1. Slither 静态分析   → 高危/中危/低危 各几个？
2. Echidna 模糊测试   → 有 invariant 被打破吗？
3. Halmos 符号执行    → 有路径会 revert 吗？
4. 返回: 综合安全报告
```

### 🐳 你要检查容器安全

```
Agent 自动做的事:
1. Trivy 扫描镜像     → 有高危 CVE 吗？
2. Dockle 检查 Dockerfile → 有最佳实践违规吗？
3. 返回: CVE 列表 + 修复建议
```

---

## 怎么部署？

### 你需要什么

> 一台服务器（Ubuntu 22.04+，4核8G 起步）  
> 一个 Sepolia RPC 地址（测试用）  
> 一个测试用的私钥（不要用主网私钥！）

### 部署步骤（5 步，15 分钟）

```bash
# 第 1 步：把代码传到服务器
scp -r autotest-mcp/ ubuntu@你的服务器IP:/home/ubuntu/

# 第 2 步：登录服务器，安装依赖
ssh ubuntu@你的服务器IP
cd /home/ubuntu/autotest-mcp
bash autotest-web3/install.sh   # 装 forge/cast/solana/slither/echidna
bash autotest-web/install.sh    # 装 playwright/hurl/lighthouse/trivy/dockle
bash autotest-dapp/install.sh   # 装 playwright + solana

# 第 3 步：配置密钥（只做一次）
cp autotest-web3/.env.example autotest-web3/.env
vim autotest-web3/.env    # 填 SEPOLIA_RPC + DEPLOYER_PRIVATE_KEY

cp autotest-dapp/.env.example autotest-dapp/.env
vim autotest-dapp/.env     # 填同样内容 + FRONTEND_URL

cp autotest-web/.env.example autotest-web/.env
vim autotest-web/.env      # 填 FRONTEND_URL + API_BASE

# 第 4 步：启动服务
sudo cp autotest-{web3,web,dapp}/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autotest-web3 autotest-web autotest-dapp

# 第 5 步：验证
sudo systemctl status autotest-web3 autotest-web autotest-dapp
curl http://localhost:8083/sse  # 应该返回 SSE 连接
```

### 让 OpenClaw agent 能用

在**每台** OpenClaw 实例上执行（不是测试服务器）：

```bash
# 告诉 OpenClaw 去哪找测试能力
openclaw mcp add autotest-web3 --transport sse --url http://<测试服务器IP>:8081/sse
openclaw mcp add autotest-web  --transport sse --url http://<测试服务器IP>:8082/sse
openclaw mcp add autotest-dapp --transport sse --url http://<测试服务器IP>:8083/sse
```

### 装 Skill（告诉 Agent 怎么用这些工具）

创建文件 `~/.openclaw/workspace/tester/skills/autotest-mcp/SKILL.md`，内容为：

```markdown
# autotest-mcp Skill

你是 tester agent。所有测试走 MCP tool，不走 exec 命令。

## 用哪个工具？

| 我要做什么 | 用这个 |
|-----------|--------|
| 合约编译 + 跑测试 | evm_contract_test(目录) |
| 发一笔交易 + 验证结果 | evm_tx_and_verify(地址, 签名, 参数) |
| 部署合约 + 验证 | evm_deploy_and_verify(目录, 脚本路径) |
| 安全审计 | security_audit(目录) |
| 打开页面 + 截图 + 检查文字 | browser_page_check(网址, 期望文字) |
| 多步操作(登录/注册) | browser_user_flow(网址, 步骤) |
| 截图前后对比 | visual_regression(网址, 名称) |
| API 多步测试 | api_e2e_test(hurl内容) |
| API 模糊测试 | api_fuzz_test(OpenAPI地址) |
| 容器安全扫描 | security_scan(镜像名) |
| 发 swap + 验证前端余额 | dapp_swap_flow(路由, 币A, 币B, 金额, 前端地址) |
| 部署后检查前端 | dapp_deploy_and_ui_check(目录, 脚本, 前端地址) |
| 链上交易 + 前端 UI 验证 | dapp_tx_and_ui_check(合约, 函数, 参数, 前端地址, 期望文字) |
| 监听链事件 + 前端更新 | dapp_event_to_ui(合约, 事件topic, 前端地址) |
| Solana 转账 + 前端验证 | dapp_sol_transfer_and_ui(地址, 金额, 前端地址) |

## 规则

1. 优先用上面的场景工具，不要自己拼原子工具
2. 每个测试开头调用 test_health，确认服务器在线
3. 返回值里的 passed 字段就是最终结论，不要自己再判断
4. 交易类操作一定要用带了 verify/confirm 的工具，不要发了不管
5. 如果工具报 rate_limit_exceeded，等一分钟再试

## 假阳性杜绝

以前的做法（❌）:
- curl 一下 grep 200 就说过了
- 交易发出去了就说成功
- echo "browser opened" 就当页面正常

现在的做法（✅）:
- 每个场景工具内部自动做断言
- 返回 passed=true/false + 每一步的详细结果
- 你只需报告 passed 和失败的步骤
```

---

## 安全说明

### 私钥安全

- 私钥只存在测试服务器的 `.env` 文件里
- 所有返回给 agent 的结果中，私钥自动替换为 `***`
- agent 永远看不到你的私钥

### 速率限制

关键操作有自动限频，防止 agent 误操作：

| 操作 | 限制 | 为什么 |
|------|------|--------|
| 发交易 | 10次/分钟 | 防止烧钱 |
| 部署合约 | 3次/2分钟 | 防止 RPC 限流 |
| 负载测试 | 3次/2分钟 | 防止打挂服务 |
| 合约 fuzz | 2次/5分钟 | 吃资源 |

### 认证（可选）

```bash
# 在 .env 里设置:
AUTH_TOKEN=***
# 然后在 nginx 里拦截不带这个 header 的请求
```

---

## 工具总览

```
45 个 MCP Tool，分 3 台 Server

🔗 autotest-web3 (8081) ── 链上: EVM + Solana
   ├── ⚡ 6 个场景工具（一键完成测试/部署/审计）
   └── ⚛ 13 个原子工具（高级场景手动用）

🌐 autotest-web (8082) ── 中心化: 浏览器/API/性能/安全
   ├── ⚡ 10 个场景工具（页面检查/API测试/性能审计）
   └── ⚛ 4 个原子工具（高级场景手动用）

🔄 autotest-dapp (8083) ── DApp 全链路: 链+前端联合
   └── ⚡ 8 个场景工具（swap/部署/钱包连接/事件→UI）

⚡ = agent 首选，一个工具完成整个测试
⚛ = 场景工具不够用时手动下钻
```

---

## 日常使用

### 测试一个合约

> "@tester 测试一下 /path/to/contracts 目录的合约，顺便做个安全审计"

Agent 自动调用 `evm_contract_test` + `security_audit`，你收到：
```
✅ 编译通过
✅ 测试 15/15 通过
✅ 安全审计: High 0, Medium 0, Low 2
```

### 测试一个 DApp 页面

> "@tester 检查 https://my-dapp.com 首页是否正常，包含 'Welcome' 文字"

Agent 调用 `browser_page_check`，你收到：
```
✅ 页面打开成功
✅ 标题: My DApp
✅ 包含 "Welcome"
📸 截图: /tmp/autotest-screenshots/page_check_xxx.png
```

### 测试 Swap 功能

> "@tester 测试 https://swap.my-dapp.com 的 USDC→WETH swap，金额 100 USDC"

Agent 调用 `dapp_swap_flow`，你收到：
```
✅ Approve 成功
✅ Swap 成功 (tx: 0xabc...)
✅ USDC 余额: -100
✅ WETH 余额: +0.05
✅ 前端余额显示正确
📸 截图已保存
```

---

## 出问题了怎么办？

```bash
# 服务挂了？
ssh 测试服务器
sudo systemctl restart autotest-web3 autotest-web autotest-dapp
sudo systemctl status autotest-web3 autotest-web autotest-dapp

# 某个工具找不到？
ssh 测试服务器
cd /home/ubuntu/autotest-mcp
python3 shared/env_checker.py all              # 看缺什么
python3 shared/env_checker.py ensure --tool hurl  # 自动安装
```

---

## 文件目录

```
autotest-mcp/
├── README.md                  ← 你正在看的
├── setup-openclaw.sh          ← OC 一键接入（每台 OC 实例跑一次）
│
├── autotest-web3/             ← 链上测试 Server
├── autotest-web/              ← 前端/API 测试 Server
├── autotest-dapp/             ← DApp 全链路测试 Server
│   ├── server.py
│   ├── install.sh
│   ├── .env.example → .env
│   └── autotest-*.service
│
└── shared/                    ← 公共模块
    ├── env_checker.py         ← 28 种工具的自动检测+安装
    └── auth_guard.py          ← 认证 + 速率限制
```

---

## Apply 步骤（使用前必读）

### 第一步：部署 MCP 服务器

把 autotest-mcp/ 目录部署到独立测试服务器，按上面「怎么部署？」操作。

### 第二步：注册 MCP 到 OpenClaw

在**每台** OpenClaw 实例上执行：

```bash
# 替换 <测试服务器IP> 为真实 IP
MCP_IP=<测试服务器IP>

openclaw mcp add autotest-web3 --transport sse --url http://$MCP_IP:8081/sse --timeout 300
openclaw mcp add autotest-web  --transport sse --url http://$MCP_IP:8082/sse --timeout 300
openclaw mcp add autotest-dapp --transport sse --url http://$MCP_IP:8083/sse --timeout 300
```

验证：`openclaw mcp list` 应该看到 3 个 server。

### 第三步：配置 Agent 工具权限

```bash
# tester (全量)
openclaw mcp configure autotest-web3 --enable
openclaw mcp configure autotest-web --enable
openclaw mcp configure autotest-dapp --enable

# team7 (只读查询)
openclaw mcp tools autotest-web3 --include 'evm_call,evm_balance,evm_code,evm_block,evm_storage,evm_receipt,evm_logs,evm_trace,sol_balance,sol_account,test_health'
openclaw mcp tools autotest-web --include 'browser_navigate,api_get,data_fake,test_health'

# 注意: security 和 qa agent 不需要 MCP 工具权限
# 它们的工作是人脑审查（SCSVS 安全标准 / L1+L2 代码审查），不涉及自动化测试
```

### 第四步：安装 Skill

把 `skills/autotest-mcp/SKILL.md` 复制到 tester 的 workspace：

```bash
cp -r skills/autotest-mcp ~/.openclaw/workspace/tester/skills/
```

或者直接把仓库里的 SKILL.md 内容复制到 `~/.openclaw/workspace/tester/skills/autotest-mcp/SKILL.md`。

### 第五步：更新 Agent 的 AGENTS.md

给 tester、security、qa agent 更新 AGENTS.md，加入 MCP Tool 使用说明：

**tester AGENTS.md 关键改动：**
- 测试执行方式: `autotest CLI` → `MCP Tool`
- 禁止 exec 跑测试 → 全部走 MCP Tool
- 加入工具速查表和返回值解读规则
- 强调 ⚡ 场景 tool 优先

**security AGENTS.md 关键改动：**
- 新增「MCP 自动化工具」章节
- 可以用 `security_audit` 一键跑 slither+echidna+halmos
- 自动化结果作为辅助证据，核心判断仍由人脑做

**qa AGENTS.md 关键改动：**
- 第 8 条约束从「先跑 autotest」改为「先跑 MCP Tool」
- 新增 MCP 快速验证工具列表
- 用 `evm_contract_test` / `browser_page_check` / `api_e2e_test` 减少手动验证

## 验证生效

```bash
# 确认 MCP 已注册
openclaw mcp list

# 用 tester agent 测试
@tester test_health  # 应该返回三个 server 的状态

# 跑一个真实测试
@tester evm_contract_test("/path/to/contracts")
```
