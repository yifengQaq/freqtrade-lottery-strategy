"""
DeepSeek API Client for Strategy Iteration Agent.

Wraps the DeepSeek Chat API (OpenAI-compatible) to:
1. Send strategy context + backtest results to LLM
2. Receive structured JSON with parameter changes
3. Handle retries, rate limits, token budgeting
"""

import json
import os
import time
import logging
from typing import Any, Optional

import httpx

from agent.dimension_templates import DimensionDiagnosticEngine, DIMENSION_TEMPLATES

logger = logging.getLogger(__name__)

# DeepSeek API is OpenAI-compatible
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"  # deepseek-chat or deepseek-reasoner


class DeepSeekClient:
    """Thin wrapper around DeepSeek Chat Completions API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEEPSEEK_API_BASE,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        max_retries: int = 3,
        timeout: float = 300.0,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY not set. "
                "Export it or pass api_key= to DeepSeekClient."
            )

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout

        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        response_format: Optional[dict] = None,
    ) -> str:
        """Send a chat completion request and return the assistant message."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        return self._complete(messages, response_format)

    def chat_with_history(
        self,
        system_prompt: str,
        history: list[dict],
        user_message: str,
        response_format: Optional[dict] = None,
    ) -> str:
        """Chat with prior conversation history for multi-round iteration."""
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return self._complete(messages, response_format)

    def generate_fix_patch(
        self,
        system_prompt: str,
        fix_prompt: str,
    ) -> dict:
        """
        Ask the LLM to fix a broken strategy.

        Returns:
            {"code_patch": str, "fix_summary": str}
        """
        raw = self.chat(
            system_prompt=system_prompt,
            user_message=fix_prompt,
            response_format={"type": "json_object"},
        )

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = self._extract_json_from_text(raw)

        if "code_patch" not in result:
            raise ValueError("LLM fix response missing 'code_patch'")

        result.setdefault("fix_summary", "")
        return result

    def generate_factor_candidates(
        self,
        system_prompt: str,
        current_code: str,
        metrics: dict,
        num_candidates: int = 5,
    ) -> list[dict]:
        """
        Ask the LLM to propose candidate factors for experimentation.

        Returns a list of candidate dicts.
        """
        user_msg = (
            f"## Current Strategy Code\n```python\n{current_code}\n```\n\n"
            f"## Current Metrics\n```json\n"
            f"{json.dumps(metrics, indent=2, ensure_ascii=False)}\n```\n\n"
            f"Generate exactly {num_candidates} candidate factor improvements.\n"
            "Return a JSON array where each element has:\n"
            '  "candidate_id", "factor_family" (volatility/trend/momentum/filter),\n'
            '  "params" (dict), "description" (string).\n'
        )

        raw = self.chat(
            system_prompt=system_prompt,
            user_message=user_msg,
            response_format={"type": "json_object"},
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = self._extract_json_from_text(raw)

        # The LLM may wrap the list in a key like "candidates"
        if isinstance(parsed, dict):
            for key in ("candidates", "factors", "results", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key]
            # Fallback: wrap single dict in a list
            return [parsed]

        if isinstance(parsed, list):
            return parsed

        raise ValueError("Cannot parse factor candidates from LLM response")

    def generate_targeted_adjustment(
        self,
        system_prompt: str,
        comparison_matrix: dict,
        target_gap: dict,
        current_code: str,
        previous_changes: list[dict] | None = None,
        epoch_round: int = 4,
    ) -> dict:
        """
        Generate parameter adjustment suggestions informed by the
        multi-window comparison matrix, the target gap vector,
        and the dimension diagnostic engine.

        Returns::

            {
                "changes_made": str,
                "rationale": str,
                "code_patch": str,
                "config_patch": dict,
                "next_action": str,
            }
        """
        engine = DimensionDiagnosticEngine()
        prev = previous_changes or []

        # Extract metrics from target_gap for diagnosis
        metrics_for_diagnosis = {}
        if isinstance(target_gap, dict):
            # target_gap has metric names as keys with gap values
            # Try to derive absolute metrics from comparison_matrix
            if isinstance(comparison_matrix, dict):
                for window_data in comparison_matrix.values():
                    if isinstance(window_data, dict):
                        metrics_for_diagnosis.update(window_data)
                        break
            # Fallback: use target_gap directly
            if not metrics_for_diagnosis:
                metrics_for_diagnosis = target_gap

        focus = engine.select_focus_dimension(
            metrics=metrics_for_diagnosis,
            previous_changes=prev,
            epoch_round=epoch_round,
        )
        dim_key = focus["dimension"]

        # Build dimension stats
        dim_stats = engine.build_dimension_stats(prev)
        stats_lines = "\n".join(
            f"  - {engine._dim_name_cn(d)}: {count}次"
            for d, count in sorted(dim_stats.items(), key=lambda x: -x[1])
        )

        # Get dimension template
        template_text = engine.get_dimension_template(dim_key)
        if "{FACTOR_CATALOG}" in template_text:
            try:
                from agent.factor_templates import FactorTemplateLibrary
                catalog_text = FactorTemplateLibrary().get_catalog_text()
            except Exception:
                catalog_text = "(因子目录加载失败)"
            template_text = template_text.replace("{FACTOR_CATALOG}", catalog_text)

        user_msg = (
            "## Multi-Window Comparison Matrix\n"
            f"```json\n{json.dumps(comparison_matrix, indent=2, ensure_ascii=False)}\n```\n\n"
            "## Target Gap Vector\n"
            f"```json\n{json.dumps(target_gap, indent=2, ensure_ascii=False)}\n```\n\n"
            f"## Current Strategy Code\n```python\n{current_code}\n```\n\n"
            f"## 维度探索统计\n{stats_lines}\n\n"
            f"## 🔍 诊断结论\n{focus['reason']}\n\n"
            f"## 🎯 本轮焦点维度: {engine._dim_name_cn(dim_key)}\n"
            f"**你本轮必须聚焦修改「{engine._dim_name_cn(dim_key)}」相关参数。**\n"
            f'⚠️ 硬约束: "focus_dimension" 必须填 "{dim_key}"\n\n'
            f"## {engine._dim_name_cn(dim_key)} 详细指导\n{template_text}\n\n"
            "Based on the comparison matrix, target gap, and dimension guidance, "
            "suggest adjustments to close the gap.  Return JSON with keys: "
            '"focus_dimension", "dimension_changes", "changes_made", "rationale", '
            '"code_patch", "config_patch", "next_action".'
        )

        raw = self.chat(
            system_prompt=system_prompt,
            user_message=user_msg,
            response_format={"type": "json_object"},
        )

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = self._extract_json_from_text(raw)

        if "code_patch" not in result:
            raise ValueError("LLM targeted-adjustment response missing 'code_patch'")

        result.setdefault("changes_made", "")
        result.setdefault("rationale", "")
        result.setdefault("config_patch", {})
        result.setdefault("next_action", "continue")
        result.setdefault("focus_dimension", dim_key)
        result.setdefault("dimension_changes", [])
        return result

    def generate_strategy_patch(
        self,
        system_prompt: str,
        current_strategy_code: str,
        backtest_results: dict,
        iteration_round: int,
        previous_changes: list[dict],
    ) -> dict:
        """
        High-level method: ask DeepSeek to propose strategy changes.

        Returns a structured dict:
        {
            "round": int,
            "changes_made": str,
            "rationale": str,
            "code_patch": str,       # Full updated strategy code
            "config_patch": dict,    # Config changes (if any)
            "next_action": str,
        }
        """
        user_msg = self._build_iteration_prompt(
            current_strategy_code,
            backtest_results,
            iteration_round,
            previous_changes,
        )

        raw = self.chat(
            system_prompt=system_prompt,
            user_message=user_msg,
            response_format={"type": "json_object"},
        )

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON, attempting extraction...")
            result = self._extract_json_from_text(raw)

        # Validate required fields
        required = ["changes_made", "rationale", "code_patch"]
        for field in required:
            if field not in result:
                raise ValueError(f"LLM response missing required field: {field}")

        result.setdefault("round", iteration_round)
        result.setdefault("next_action", "continue")
        result.setdefault("config_patch", {})

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _complete(
        self,
        messages: list[dict],
        response_format: Optional[dict] = None,
    ) -> str:
        """Execute chat completion with retry logic."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(
                    "DeepSeek response: %d tokens (attempt %d)",
                    data.get("usage", {}).get("total_tokens", -1),
                    attempt,
                )
                return content

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = min(2**attempt, 30)
                    logger.warning("Rate limited, waiting %ds...", wait)
                    time.sleep(wait)
                elif e.response.status_code >= 500:
                    wait = min(2**attempt, 30)
                    logger.warning("Server error %d, retrying in %ds...",
                                   e.response.status_code, wait)
                    time.sleep(wait)
                else:
                    raise
            except httpx.TimeoutException:
                logger.warning("Timeout on attempt %d/%d", attempt, self.max_retries)
                if attempt == self.max_retries:
                    raise

        raise RuntimeError(f"DeepSeek API failed after {self.max_retries} attempts")

    def _build_iteration_prompt(
        self,
        strategy_code: str,
        backtest_results: dict,
        iteration_round: int,
        previous_changes: list[dict],
    ) -> str:
        """Build the per-round user prompt with dimension-aware guidance."""
        engine = DimensionDiagnosticEngine()

        # 1. Select focus dimension via diagnosis
        metrics = backtest_results if isinstance(backtest_results, dict) else {}
        focus = engine.select_focus_dimension(
            metrics=metrics,
            previous_changes=previous_changes,
            epoch_round=iteration_round,
        )
        dim_key = focus["dimension"]

        # 2. Build dimension exploration stats
        dim_stats = engine.build_dimension_stats(previous_changes)
        stats_lines = "\n".join(
            f"  - {engine._dim_name_cn(d)}: {count}次"
            for d, count in sorted(dim_stats.items(), key=lambda x: -x[1])
        )

        # 3. Get dimension template and inject factor catalog if needed
        template_text = engine.get_dimension_template(dim_key)
        if "{FACTOR_CATALOG}" in template_text:
            try:
                from agent.factor_templates import FactorTemplateLibrary
                catalog_text = FactorTemplateLibrary().get_catalog_text()
            except Exception:
                catalog_text = "(因子目录加载失败)"
            template_text = template_text.replace("{FACTOR_CATALOG}", catalog_text)

        # 4. Build recent changes summary
        changes_summary = ""
        if previous_changes:
            for c in previous_changes[-5:]:  # Last 5 rounds for context
                dim_label = c.get("focus_dimension", "entry_signal")
                changes_summary += (
                    f"  Round {c.get('round', '?')} [{engine._dim_name_cn(dim_label)}]: "
                    f"{c.get('changes_made', 'N/A')} → "
                    f"Score: {c.get('score', 'N/A')}\n"
                )

        return f"""
## 当前迭代轮次: {iteration_round}

## 当前策略代码
```python
{strategy_code}
```

## 最新回测结果
```json
{json.dumps(backtest_results, indent=2, ensure_ascii=False)}
```

## 历史变更摘要
{changes_summary if changes_summary else "这是第一轮迭代，无历史记录。"}

## 维度探索统计
{stats_lines}

## 🔍 诊断结论
{focus["reason"]}

## 🎯 本轮焦点维度: {engine._dim_name_cn(dim_key)}
**你本轮必须聚焦修改「{engine._dim_name_cn(dim_key)}」相关参数。**
可以同时微调其他维度，但主要改动必须在焦点维度上。

⚠️ 硬约束: 你的 "focus_dimension" 字段必须填 "{dim_key}"

## {engine._dim_name_cn(dim_key)} 详细指导
{template_text}

## 你的任务

### 第一步：理解诊断
- 仔细阅读上方诊断结论，理解为什么选择这个维度
- 查看回测结果中的具体数字

### 第二步：在焦点维度内做出修改
- 根据「{engine._dim_name_cn(dim_key)}」的详细指导，进行针对性修改
- 确保修改是有意义的、有数据依据的

### 第三步：确保代码正确
- code_patch 必须是完整可运行的 .py 文件
- 所有 indicator 计算在 populate_indicators 中
- talib 多列指标用 DataFrame 列名，禁止元组解包

⚠️ 关键约束:
- timeframe 只能是 "15m" 或 "1h"
- stake_amount 必须保持 "unlimited"
- code_patch 必须是完整可运行的 .py 文件

请直接返回纯 JSON（不要 markdown fence），格式:
{{
    "round": {iteration_round},
    "focus_dimension": "{dim_key}",
    "dimension_changes": ["具体修改项1", "具体修改项2"],
    "factors_used": ["因子名1", "因子名2"],
    "changes_made": "[{engine._dim_name_cn(dim_key)}] 简述修改内容",
    "rationale": "修改理由（基于诊断和数据）",
    "code_patch": "完整的修改后策略代码（Python）",
    "config_patch": {{}},
    "next_action": "下一步计划"
}}
"""

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
        """Try to extract JSON from text that may contain markdown fences."""
        import re

        # 1. Try ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 2. Find outermost balanced { ... } using bracket counting
        start = text.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape = False
            for i in range(start, len(text)):
                c = text[i]
                if escape:
                    escape = False
                    continue
                if c == "\\":
                    escape = True
                    continue
                if c == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                        break

        # 3. Greedy fallback (original approach)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(
            f"Cannot extract JSON from LLM response "
            f"(length={len(text)}, first 200 chars: {text[:200]!r})"
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
