"""
Factor Lab — generate, evaluate, and track candidate factor experiments.

Provides:
1. LLM-driven candidate factor generation
2. Deduplication by (factor_family, params)
3. Promotion / quarantine based on evaluation
4. JSONL experiment logging
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class FactorLab:
    """Generate and evaluate candidate factors for strategy improvement."""

    def __init__(
        self,
        deepseek_client,
        experiment_log_path: str = "results/experiments/factor_trials.jsonl",
    ):
        self.deepseek_client = deepseek_client
        self.experiment_log_path = experiment_log_path
        self._candidates: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_candidates(
        self,
        current_code: str,
        metrics: dict,
        num_candidates: int = 5,
    ) -> list[dict]:
        """
        Ask the LLM to propose candidate factors.

        Returns a deduplicated list of candidate dicts:
            [
                {
                    "candidate_id": "fc_001",
                    "factor_family": "volatility",
                    "params": {...},
                    "description": "...",
                    "status": "active",
                },
                ...
            ]
        """
        raw_candidates = self.deepseek_client.generate_factor_candidates(
            system_prompt="You are a quantitative strategy researcher.",
            current_code=current_code,
            metrics=metrics,
            num_candidates=num_candidates,
        )

        # Normalise each candidate
        normalised: list[dict] = []
        for i, c in enumerate(raw_candidates):
            candidate = {
                "candidate_id": c.get("candidate_id", f"fc_{i + 1:03d}"),
                "factor_family": c.get("factor_family", "unknown"),
                "params": c.get("params", {}),
                "description": c.get("description", ""),
                "status": "active",
            }
            normalised.append(candidate)

        # Deduplicate
        deduped = self.deduplicate(normalised)

        # Store internally
        self._candidates.extend(deduped)

        return deduped

    def evaluate_candidate(
        self,
        candidate: dict,
        backtest_metrics: dict,
        baseline_score: float,
        evaluator=None,
    ) -> dict:
        """
        Evaluate a candidate factor's backtest results.

        If the candidate passes gates and its score >= baseline → "promoted".
        Otherwise → "quarantined".
        """
        if evaluator is not None:
            eval_result = evaluator.evaluate(backtest_metrics)
            passed = eval_result.passed
            score = eval_result.score
        else:
            # Simple fallback: just compare a "score" key in metrics
            passed = True
            score = backtest_metrics.get("score", 0.0)

        if passed and score >= baseline_score:
            candidate["status"] = "promoted"
        else:
            candidate["status"] = "quarantined"

        # Update internal list
        for c in self._candidates:
            if c["candidate_id"] == candidate["candidate_id"]:
                c["status"] = candidate["status"]
                break

        return candidate

    def log_experiment(
        self,
        candidate: dict,
        metrics: dict,
        score: float,
    ):
        """Append an experiment record to the JSONL log file."""
        os.makedirs(os.path.dirname(self.experiment_log_path), exist_ok=True)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "candidate_id": candidate.get("candidate_id", ""),
            "factor_family": candidate.get("factor_family", ""),
            "params": candidate.get("params", {}),
            "status": candidate.get("status", ""),
            "score": score,
            "metrics": metrics,
        }

        with open(self.experiment_log_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        logger.info(
            "Logged experiment: %s (score=%.2f, status=%s)",
            candidate.get("candidate_id"), score, candidate.get("status"),
        )

    def get_active_candidates(self) -> list[dict]:
        """Return candidates whose status is 'active'."""
        return [c for c in self._candidates if c["status"] == "active"]

    def deduplicate(self, candidates: list[dict]) -> list[dict]:
        """
        Remove duplicate candidates based on (factor_family, params).

        Keeps the first occurrence.
        """
        seen: set[str] = set()
        result: list[dict] = []

        for c in candidates:
            key = self._dedup_key(c)
            if key not in seen:
                seen.add(key)
                result.append(c)

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(candidate: dict) -> str:
        """Generate a deterministic key from factor_family + params."""
        family = candidate.get("factor_family", "")
        params = candidate.get("params", {})
        params_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
        raw = f"{family}:{params_str}"
        return hashlib.md5(raw.encode()).hexdigest()
