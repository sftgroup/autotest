#!/usr/bin/env python3
"""
report_builder — 统一测试结果包装器

为所有 MCP Tool 返回结果附加 verdict + summary 字段，
帮助 tester agent 快速决策而不需要解析大段原始 JSON。

用法:
    from report_builder import Result

    # Scenario tool 内部:
    r = Result()
    r.step("forge build", passed=True)
    r.step("forge test", passed=True, detail="33/33 passed")
    r.step("slither audit", passed=False, detail="2 HIGH, 3 MEDIUM")
    return r.done()

    # 返回:
    {
      "verdict": "FAIL",
      "verdict_confidence": "high",
      "summary": "🔴 FAIL: forge build ✅ | forge test ✅(33/33) | slither audit ❌(2 HIGH)",
      "passed": false,
      "checks": [...steps...],
      "suggestion": "修复 2 个 high-severity slither 问题后重新测试"
    }

    # 快捷用法:
    return Result.pass_("✅ CT-001: 全部通过, 33/33 tests")
    return Result.fail_("🔴 AT-001: auth 端点 404")
    return Result.skip_("⏭️ CT-006: 需先部署合约")
"""

from __future__ import annotations

import json
from typing import Any


class Result:
    """累积测试步骤，最后生成结构化报告。"""

    def __init__(self):
        self._checks: list[dict] = []
        self._passed_count = 0
        self._failed_count = 0
        self._skipped_count = 0
        self._extra: dict[str, Any] = {}

    def step(self, label: str, passed: bool | None = None, detail: str = "", **extra) -> "Result":
        """记录一个检查步骤。
        
        passed: True=通过, False=失败, None=信息性/跳过
        """
        if passed is True:
            self._passed_count += 1
            status = "✅"
        elif passed is False:
            self._failed_count += 1
            status = "🔴"
        else:
            self._skipped_count += 1
            status = "⏭️"
        
        entry = {"step": label, "passed": passed, "status": status}
        if detail:
            entry["detail"] = detail
        entry.update(extra)
        self._checks.append(entry)
        return self

    def extra(self, **kwargs) -> "Result":
        """附加任意数据到结果中（如截图路径、tx_hash 等）。"""
        self._extra.update(kwargs)
        return self

    def _build_summary(self) -> str:
        """自动生成一行摘要。"""
        parts = []
        if self._failed_count > 0:
            parts.append(f"🔴 FAIL ({self._passed_count}P/{self._failed_count}F/{self._skipped_count}S)")
        elif self._skipped_count > 0:
            parts.append(f"🟡 PASS+SKIP ({self._passed_count}P/{self._skipped_count}S)")
        else:
            parts.append(f"🟢 PASS ({self._passed_count}/{self._passed_count})")
        
        # 关键步骤摘要（最多 6 个）
        for c in self._checks[:6]:
            detail = f"({c.get('detail')})" if c.get('detail') else ""
            parts.append(f"{c['status']} {c['step']}{detail}")
        
        return " | ".join(parts)

    def _build_suggestion(self) -> str:
        """根据失败类型生成建议。"""
        if self._failed_count == 0:
            return "继续下一项测试"
        
        fails = [c for c in self._checks if c.get("passed") is False]
        # 找最常见的失败关键词
        keywords = []
        for f in fails:
            detail = f.get("detail", "") + f.get("error", "")
            if "403" in detail or "401" in detail or "auth" in detail.lower() or "unauthorized" in detail.lower():
                keywords.append("需要认证")
            elif "404" in detail:
                keywords.append("端点不存在")
            elif "500" in detail:
                keywords.append("服务端错误")
            elif "timeout" in detail.lower():
                keywords.append("超时")
            elif "revert" in detail.lower():
                keywords.append("合约 revert")
        
        if keywords:
            uniq = list(dict.fromkeys(keywords))  # dedup keep order
            return " | ".join(uniq)
        return "请检查失败详情"

    def done(self) -> str:
        """输出最终 JSON 字符串。"""
        verdict = "FAIL" if self._failed_count > 0 else "PASS"
        confidence = "high" if self._checks else "low"
        
        result = {
            "verdict": verdict,
            "verdict_confidence": confidence,
            "summary": self._build_summary(),
            "passed": self._failed_count == 0,
            "passed_count": self._passed_count,
            "failed_count": self._failed_count,
            "skipped_count": self._skipped_count,
            "checks": self._checks,
            "suggestion": self._build_suggestion(),
        }
        result.update(self._extra)
        return json.dumps(result, ensure_ascii=False, default=str)

    @classmethod
    def pass_(cls, summary: str = "passed", **extra) -> str:
        """一键生成通过结果。"""
        r = cls()
        r.step("result", passed=True, detail=summary)
        for k, v in extra.items():
            r.extra(**{k: v})
        return r.done()

    @classmethod
    def fail_(cls, summary: str = "failed", **extra) -> str:
        """一键生成失败结果。"""
        r = cls()
        r.step("result", passed=False, detail=summary)
        for k, v in extra.items():
            r.extra(**{k: v})
        return r.done()

    @classmethod
    def skip_(cls, reason: str = "skipped", **extra) -> str:
        """一键生成跳过结果。"""
        r = cls()
        r.step("result", passed=None, detail=reason)
        for k, v in extra.items():
            r.extra(**{k: v})
        return r.done()

    @classmethod
    def ok_(cls, data: dict | str, verdict: str = "pass") -> str:
        """包装已有 dict 结果，自动提取 verdict 和 summary。
        
        适用于改造已有 tool——在原有返回 JSON 基础上附加 verdict/summary。
        """
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return cls.pass_(summary=data) if verdict == "pass" else cls.fail_(summary=data)
        
        # 尝试从已有数据推断结果
        passed = data.get("passed", data.get("ok", True))
        r = cls()
        
        if passed is False:
            r.step("result", passed=False, detail=data.get("error", "failed"))
        elif passed is True:
            r.step("result", passed=True, detail="ok")
        else:
            r.step("result", passed=None, detail="skipped")
        
        for key in ["tx_hash", "screenshot", "contract_address", "report_path"]:
            if key in data:
                r.extra(**{key: data[key]})
        
        # 保留原始数据作为 details
        r.extra(**{"details": data})
        
        return r.done()
