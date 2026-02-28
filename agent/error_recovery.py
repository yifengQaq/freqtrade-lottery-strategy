"""
Error Recovery Manager — automatic error diagnosis and fix for backtest failures.

When a backtest fails (syntax error, runtime error, config issue, etc.),
this module:
1. Classifies the error type
2. Builds a fix prompt with full context
3. Calls the LLM to generate a fix
4. Validates syntax and retries backtest
5. Rolls back if max retries exhausted
"""

import ast
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Error classification patterns
_SYNTAX_PATTERNS = re.compile(r"SyntaxError|IndentationError", re.IGNORECASE)
_RUNTIME_PATTERNS = re.compile(
    r"KeyError|NameError|AttributeError|TypeError|IndexError", re.IGNORECASE
)
_CONFIG_PATTERNS = re.compile(r"config|json|settings", re.IGNORECASE)
_DATA_PATTERNS = re.compile(r"\bdata\b|download|pairs|timerange", re.IGNORECASE)


class ErrorRecoveryManager:
    """Diagnose backtest errors and attempt LLM-driven fixes."""

    def __init__(
        self,
        deepseek_client,
        strategy_modifier,
        backtest_runner,
        max_retries: int = 3,
    ):
        self.deepseek_client = deepseek_client
        self.strategy_modifier = strategy_modifier
        self.backtest_runner = backtest_runner
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_error(self, error_log: str) -> str:
        """
        Classify error type from a traceback / error log.

        Returns one of: "syntax", "runtime", "config", "data", "unknown".
        """
        if _SYNTAX_PATTERNS.search(error_log):
            return "syntax"
        if _RUNTIME_PATTERNS.search(error_log):
            return "runtime"
        if _CONFIG_PATTERNS.search(error_log):
            return "config"
        if _DATA_PATTERNS.search(error_log):
            return "data"
        return "unknown"

    def build_fix_prompt(
        self,
        error_type: str,
        traceback: str,
        code_snippet: str,
        changes_summary: str,
    ) -> str:
        """Build a structured prompt asking the LLM to fix the error."""
        return (
            f"## Error Type\n{error_type}\n\n"
            f"## Traceback\n```\n{traceback}\n```\n\n"
            f"## Current Code Snippet\n```python\n{code_snippet}\n```\n\n"
            f"## Recent Changes\n{changes_summary}\n\n"
            "## Task\n"
            "Fix the error above and return the complete corrected strategy code.\n"
            'Return JSON: {{"code_patch": "<full corrected code>", '
            '"fix_summary": "<what you fixed>"}}'
        )

    def attempt_fix(
        self,
        error_log: str,
        current_code: str,
        round_num: int,
        timerange: Optional[str] = None,
    ) -> dict:
        """
        Attempt to fix a failing strategy via LLM-driven repair loop.

        Returns:
            {
                "success": bool,
                "attempts": int,
                "error_type": str,
                "fix_summary": str,
                "metrics": dict,   # backtest metrics on success
            }
        """
        error_type = self.classify_error(error_log)
        last_error = error_log

        for attempt in range(1, self.max_retries + 1):
            logger.info(
                "Repair attempt %d/%d (error_type=%s)",
                attempt, self.max_retries, error_type,
            )

            # 1. Build fix prompt
            fix_prompt = self.build_fix_prompt(
                error_type=error_type,
                traceback=last_error,
                code_snippet=current_code,
                changes_summary=f"Round {round_num}, repair attempt {attempt}",
            )

            # 2. Ask LLM for fix
            try:
                fix_result = self.deepseek_client.generate_fix_patch(
                    system_prompt="You are a Python debugging expert.",
                    fix_prompt=fix_prompt,
                )
            except Exception as exc:
                logger.error("LLM fix call failed: %s", exc)
                continue

            patched_code = fix_result.get("code_patch", "")
            fix_summary = fix_result.get("fix_summary", "")

            if not patched_code:
                logger.warning("LLM returned empty code_patch on attempt %d", attempt)
                continue

            # 3. Syntax validation
            try:
                ast.parse(patched_code)
            except SyntaxError as se:
                logger.warning("Fix attempt %d still has syntax error: %s", attempt, se)
                last_error = str(se)
                current_code = patched_code
                continue

            # 4. Apply patch
            patch_result = self.strategy_modifier.apply_patch(
                patched_code,
                round_num=round_num,
                changes_description=f"auto-repair attempt {attempt}: {fix_summary}",
            )
            if not patch_result["success"]:
                logger.warning(
                    "Patch rejected on attempt %d: %s",
                    attempt, patch_result["errors"],
                )
                continue

            # 5. Re-run backtest
            bt_kwargs = {}
            if timerange:
                bt_kwargs["timerange"] = timerange
            bt_result = self.backtest_runner.run(**bt_kwargs)

            if bt_result["success"]:
                logger.info("Repair succeeded on attempt %d", attempt)
                return {
                    "success": True,
                    "attempts": attempt,
                    "error_type": error_type,
                    "fix_summary": fix_summary,
                    "metrics": bt_result.get("metrics", {}),
                }

            # Backtest still failing — update error for next attempt
            last_error = bt_result.get("error", "unknown error")
            current_code = patched_code

        # Exhausted
        logger.warning("Repair exhausted after %d attempts", self.max_retries)
        return {
            "success": False,
            "attempts": self.max_retries,
            "error_type": error_type,
            "fix_summary": "exhausted",
            "metrics": {},
        }

    def rollback_on_exhausted(self, round_num: int) -> dict:
        """
        Roll back to previous stable version when repairs are exhausted.

        Returns:
            {
                "rolled_back": True/False,
                "rollback_round": int,
                "status": "quarantined" | "rollback_failed",
            }
        """
        target_round = round_num - 1
        if target_round < 1:
            logger.warning("Cannot rollback — no prior round exists")
            return {
                "rolled_back": False,
                "rollback_round": 0,
                "status": "rollback_failed",
            }

        ok = self.strategy_modifier.rollback(target_round)
        if ok:
            logger.info("Rolled back to round %d (quarantined)", target_round)
            return {
                "rolled_back": True,
                "rollback_round": target_round,
                "status": "quarantined",
            }

        logger.error("Rollback to round %d failed", target_round)
        return {
            "rolled_back": False,
            "rollback_round": target_round,
            "status": "rollback_failed",
        }
