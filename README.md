# autotest — 通用自动化测试引擎

> AutoOps 项目核心 Skill | v2.0

**一条命令跑完合约 + API + 前端全链路测试**，含真链交易 + 浏览器交互。

## 安装

```bash
curl -sL https://raw.githubusercontent.com/sftgroup/autotest/main/autotest.sh -o /usr/local/bin/autotest
chmod +x /usr/local/bin/autotest
```

## 使用

```bash
autotest run --project /path/to/your-project --scope all
```

## 文档

| 文档 | 内容 |
|------|------|
| [SKILL.md](SKILL.md) | 使用手册 + 命令参考 + Sandbox 说明 |
| [docs/TEST_SCENARIOS_SPEC.md](docs/TEST_SCENARIOS_SPEC.md) | 架构师场景文档编写规范 |
| `autotest.sh` | 执行引擎源码 (~1000 行 bash) |

## 依赖

- `forge` / `cast` (Foundry)
- `curl`
- `agent-browser` (可选，浏览器测试需要)
- `bash` 4.0+

## 执照

MIT
