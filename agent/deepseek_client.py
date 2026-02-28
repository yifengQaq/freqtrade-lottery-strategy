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
    ) -> dict:
        """
        Generate parameter adjustment suggestions informed by the
        multi-window comparison matrix and the target gap vector.

        Returns::

            {
                "changes_made": str,
                "rationale": str,
                "code_patch": str,
                "config_patch": dict,
                "next_action": str,
            }
        """
        user_msg = (
            "## Multi-Window Comparison Matrix\n"
            f"```json\n{json.dumps(comparison_matrix, indent=2, ensure_ascii=False)}\n```\n\n"
            "## Target Gap Vector\n"
            f"```json\n{json.dumps(target_gap, indent=2, ensure_ascii=False)}\n```\n\n"
            f"## Current Strategy Code\n```python\n{current_code}\n```\n\n"
            "Based on the comparison matrix and target gap, suggest parameter "
            "adjustments to close the gap.  Return JSON with keys: "
            '"changes_made", "rationale", "code_patch", "config_patch", "next_action".'
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
        """Build the per-round user prompt."""
        changes_summary = ""
        if previous_changes:
            for c in previous_changes[-3:]:  # Only last 3 rounds for context
                changes_summary += (
                    f"  Round {c.get('round', '?')}: "
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

## 你的任务（按顺序执行）
### 第一步：逻辑诊断
- 检查入场条件的指标组合是否逻辑矛盾（如 close > BB_upper 同时 RSI < 30）
- 如有矛盾，本轮优先修复逻辑，不要只调参数
- 如果上轮 0 笔交易，几乎肯定是逻辑/条件过严问题

### 第二步：选择修改维度
参考历史变更摘要，不要连续 2 轮修改同一类参数。
优先级：逻辑矛盾修复 > 入场重构 > 出场调优 > 杠杆/参数微调

### 第三步：实施 1-2 个具体修改
- 每次最多修改 2 个维度
- 在 changes_made 中标注修改维度（如"[入场逻辑]""[出场参数]"）

⚠️ 关键约束:
- timeframe 只能是 "15m" 或 "1h"，严禁改为其他值
- stake_amount 必须保持 "unlimited"
- code_patch 必须是完整可运行的 .py 文件

请直接返回纯 JSON（不要 markdown fence），格式:
{{
    "round": {iteration_round},
    "changes_made": "[维度] 简述修改内容",
    "rationale": "修改理由（基于数据和逻辑诊断）",
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
