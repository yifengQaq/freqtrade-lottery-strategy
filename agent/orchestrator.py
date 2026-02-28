"""
Orchestrator — multi-round iteration loop for the LLM + Backtest agent.

Coordinates all Phase 3 modules:
- DeepSeekClient: LLM strategy suggestions
- BacktestRunner:  freqtrade backtest execution
- Evaluator:       gate checks + scoring
- StrategyModifier: safe code writes + versioning

Terminates when:
- max_rounds reached
- stale_rounds_limit consecutive rounds with no improvement
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.backtest_runner import BacktestRunner
from agent.deepseek_client import DeepSeekClient
from agent.error_recovery import ErrorRecoveryManager
from agent.evaluator import Evaluator, EvalResult
from agent.factor_lab import FactorLab
from agent.strategy_modifier import StrategyModifier

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main iteration loop that ties all components together."""

    def __init__(self, config: dict):
        """
        Args:
            config: Flat dict from ``config/agent_config.yaml`` → ``agent:`` key.
                    Expected keys: max_rounds, stale_rounds_limit, deepseek_model,
                    freqtrade_dir, strategy_name, config_path,
                    timerange_is, timerange_oos, enable_walk_forward, …
        """
        self.config = config
        self.max_rounds: int = config.get("max_rounds", 20)
        self.stale_rounds_limit: int = config.get("stale_rounds_limit", 3)
        self.enable_walk_forward: bool = config.get("enable_walk_forward", False)
        self.enable_auto_repair: bool = config.get("enable_auto_repair", False)
        self.repair_max_retries: int = config.get("repair_max_retries", 3)
        self.enable_factor_lab: bool = config.get("enable_factor_lab", False)
        self.factor_candidates: int = config.get("factor_candidates", 5)

        # --- sub-components (can be overridden in tests) ---
        self.deepseek_client: DeepSeekClient = self._build_deepseek_client(config)
        self.backtest_runner: BacktestRunner = self._build_backtest_runner(config)
        self.evaluator: Evaluator = Evaluator()
        self.strategy_modifier: StrategyModifier = self._build_strategy_modifier(config)

        # Error recovery manager (lazy — depends on other components)
        self.error_recovery: ErrorRecoveryManager | None = None
        if self.enable_auto_repair:
            self.error_recovery = ErrorRecoveryManager(
                deepseek_client=self.deepseek_client,
                strategy_modifier=self.strategy_modifier,
                backtest_runner=self.backtest_runner,
                max_retries=self.repair_max_retries,
            )

        # Factor lab (lazy)
        self.factor_lab: FactorLab | None = None
        if self.enable_factor_lab:
            self.factor_lab = FactorLab(
                deepseek_client=self.deepseek_client,
                experiment_log_path=os.path.join(
                    config.get("results_dir", "results"),
                    "experiments", "factor_trials.jsonl",
                ),
            )

        # System prompt for the LLM
        self.system_prompt: str = self._load_system_prompt()

        # Results directory for iteration log
        self.results_dir: str = config.get("results_dir", "results")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_iteration_loop(self, max_rounds: Optional[int] = None) -> list[dict]:
        """
        Execute the main optimisation loop.

        Returns:
            List of ``IterationRound`` dicts (one per completed round).
        """
        cap = max_rounds if max_rounds is not None else self.max_rounds
        rounds: list[dict] = []
        best_score: float = float("-inf")

        for round_num in range(1, cap + 1):
            logger.info("===== Round %d / %d =====", round_num, cap)

            record = self._run_single_round(round_num, rounds)
            rounds.append(record)

            # Track best score on *successful* rounds
            if record["status"] == "success":
                if record["score"] > best_score:
                    best_score = record["score"]

            # Overfitting check (walk-forward)
            if (
                record["status"] == "success"
                and self.enable_walk_forward
            ):
                wf_ok, wf_msg = self.run_walk_forward()
                if not wf_ok:
                    record["status"] = "overfitting"
                    record["next_action"] = wf_msg
                    # Rollback to previous round
                    if round_num > 1:
                        self.strategy_modifier.rollback(round_num - 1)
                        logger.warning(
                            "Overfitting detected — rolled back to round %d",
                            round_num - 1,
                        )

            # Termination check
            should_stop, reason = self._check_termination(rounds)
            if should_stop:
                logger.info("Terminating: %s", reason)
                if rounds:
                    rounds[-1]["next_action"] = f"STOP: {reason}"
                break

        # Persist log
        self._save_iteration_log(rounds)
        return rounds

    def run_walk_forward(self) -> tuple[bool, str]:
        """
        Walk-forward validation: run OOS backtest and compare with IS result.

        Returns:
            (passed, message)
        """
        timerange_oos = self.config.get("timerange_oos")
        if not timerange_oos:
            return True, "No OOS timerange configured — skipping walk-forward."

        oos_bt = self.backtest_runner.run(timerange=timerange_oos)
        if not oos_bt["success"]:
            return False, f"OOS backtest failed: {oos_bt['error']}"

        oos_eval = self.evaluator.evaluate(oos_bt["metrics"])

        # We need the latest IS eval — run IS again for fair comparison
        timerange_is = self.config.get("timerange_is")
        is_bt = self.backtest_runner.run(timerange=timerange_is)
        if not is_bt["success"]:
            return False, f"IS backtest failed: {is_bt['error']}"

        is_eval = self.evaluator.evaluate(is_bt["metrics"])

        return self.evaluator.compare_is_oos(is_eval, oos_eval)

    # ------------------------------------------------------------------
    # Internal — single round
    # ------------------------------------------------------------------

    def _run_single_round(
        self, round_num: int, previous_rounds: list[dict]
    ) -> dict:
        """Execute one full iteration round and return an IterationRound dict."""
        timestamp = datetime.now(timezone.utc).isoformat()
        base_record: dict = {
            "round": round_num,
            "timestamp": timestamp,
            "changes_made": "",
            "rationale": "",
            "backtest_metrics": {},
            "eval_result": {},
            "score": 0.0,
            "strategy_version_path": "",
            "next_action": "",
            "status": "failed",
        }

        # 1. Read current strategy code
        try:
            current_code = self.strategy_modifier.get_current_code()
        except Exception as exc:
            base_record["next_action"] = f"Cannot read strategy: {exc}"
            return base_record

        # 2. Build backtest-results context for LLM
        backtest_context: dict = {}
        if previous_rounds:
            last = previous_rounds[-1]
            backtest_context = last.get("backtest_metrics", {})

        previous_changes = [
            {
                "round": r["round"],
                "changes_made": r["changes_made"],
                "score": r["score"],
            }
            for r in previous_rounds
        ]

        # 3. Call DeepSeek
        try:
            llm_result = self.deepseek_client.generate_strategy_patch(
                system_prompt=self.system_prompt,
                current_strategy_code=current_code,
                backtest_results=backtest_context,
                iteration_round=round_num,
                previous_changes=previous_changes,
            )
        except Exception as exc:
            base_record["next_action"] = f"LLM call failed: {exc}"
            return base_record

        base_record["changes_made"] = llm_result.get("changes_made", "")
        base_record["rationale"] = llm_result.get("rationale", "")
        new_code = llm_result.get("code_patch", "")

        # 4. Apply patch via StrategyModifier
        if not new_code:
            base_record["next_action"] = "LLM returned empty code_patch"
            return base_record

        patch_result = self.strategy_modifier.apply_patch(
            new_code,
            round_num=round_num,
            changes_description=base_record["changes_made"],
        )
        if not patch_result["success"]:
            base_record["next_action"] = (
                f"Patch rejected: {patch_result['errors']}"
            )
            return base_record

        base_record["strategy_version_path"] = patch_result.get(
            "backup_path", ""
        )

        # 5. Run backtest
        timerange = self.config.get("timerange_is")
        bt_result = self.backtest_runner.run(timerange=timerange)
        if not bt_result["success"]:
            # --- Auto-repair path ---
            if self.error_recovery is not None:
                logger.info("Backtest failed — attempting auto-repair")
                fix = self.error_recovery.attempt_fix(
                    error_log=bt_result.get("error", ""),
                    current_code=new_code,
                    round_num=round_num,
                    timerange=timerange,
                )
                if fix["success"]:
                    logger.info("Auto-repair succeeded (attempts=%d)", fix["attempts"])
                    bt_result = {
                        "success": True,
                        "error": "",
                        "metrics": fix["metrics"],
                        "raw_results": {},
                        "result_file": "",
                    }
                    base_record["changes_made"] += f" [auto-repaired: {fix['fix_summary']}]"
                else:
                    # Exhausted — rollback
                    rb = self.error_recovery.rollback_on_exhausted(round_num)
                    base_record["next_action"] = (
                        f"Auto-repair exhausted ({fix['attempts']} attempts). "
                        f"Rolled back: {rb['rolled_back']}"
                    )
                    base_record["status"] = "rolled_back"
                    return base_record

            if not bt_result["success"]:
                base_record["next_action"] = (
                    f"Backtest failed: {bt_result['error']}"
                )
                return base_record

        base_record["backtest_metrics"] = bt_result["metrics"]

        # 6. Evaluate
        eval_result: EvalResult = self.evaluator.evaluate(bt_result["metrics"])
        base_record["eval_result"] = eval_result.to_dict()
        base_record["score"] = eval_result.score
        base_record["next_action"] = llm_result.get("next_action", "continue")
        base_record["status"] = "success"

        return base_record

    # ------------------------------------------------------------------
    # Termination
    # ------------------------------------------------------------------

    def _check_termination(self, rounds: list[dict]) -> tuple[bool, str]:
        """
        Check whether the loop should stop.

        Returns:
            (should_stop, reason)
        """
        if not rounds:
            return False, ""

        # Stale-rounds check: last N *successful* rounds show no improvement
        successful = [r for r in rounds if r["status"] == "success"]
        limit = self.stale_rounds_limit
        if len(successful) >= limit:
            tail = successful[-limit:]
            scores = [r["score"] for r in tail]
            # If the scores are non-increasing over the window → stale
            if all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)):
                return True, (
                    f"No improvement in last {limit} successful rounds "
                    f"(scores: {scores})"
                )

        return False, ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_iteration_log(self, rounds: list[dict]):
        """Write ``results/iteration_log.json``."""
        log_path = os.path.join(self.results_dir, "iteration_log.json")
        os.makedirs(self.results_dir, exist_ok=True)

        try:
            with open(log_path, "w") as f:
                json.dump(rounds, f, indent=2, ensure_ascii=False, default=str)
            logger.info("Iteration log saved to %s", log_path)
        except Exception as exc:
            logger.error("Failed to save iteration log: %s", exc)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_deepseek_client(cfg: dict) -> DeepSeekClient:
        return DeepSeekClient(
            model=cfg.get("deepseek_model", "deepseek-chat"),
        )

    @staticmethod
    def _build_backtest_runner(cfg: dict) -> BacktestRunner:
        return BacktestRunner(
            freqtrade_dir=cfg.get("freqtrade_dir", ""),
            config_path=cfg.get("config_path", "config/config_backtest.json"),
            strategy_name=cfg.get("strategy_name", "LotteryMindsetStrategy"),
            timerange=cfg.get("timerange_is"),
        )

    @staticmethod
    def _build_strategy_modifier(cfg: dict) -> StrategyModifier:
        return StrategyModifier(
            strategy_dir=cfg.get("strategy_dir", "strategies"),
            backup_dir=cfg.get("backup_dir", "results/strategy_versions"),
        )

    @staticmethod
    def _load_system_prompt() -> str:
        prompt_path = os.path.join("agent", "prompts", "system_prompt.md")
        if os.path.exists(prompt_path):
            with open(prompt_path) as f:
                return f.read()
        return "You are a Freqtrade strategy optimisation agent."
