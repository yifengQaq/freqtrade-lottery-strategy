"""
Unit tests for agent.factor_lab — candidate generation, dedup, evaluation, logging.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from agent.factor_lab import FactorLab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CANDIDATES = [
    {
        "candidate_id": "fc_001",
        "factor_family": "volatility",
        "params": {"indicator": "RSI", "period": 14, "threshold": 30},
        "description": "添加 RSI 超卖过滤",
    },
    {
        "candidate_id": "fc_002",
        "factor_family": "trend",
        "params": {"indicator": "EMA", "fast": 12, "slow": 26},
        "description": "EMA 交叉趋势过滤",
    },
    {
        "candidate_id": "fc_003",
        "factor_family": "momentum",
        "params": {"indicator": "MACD", "signal": 9},
        "description": "MACD 动量确认",
    },
]


def _make_lab(tmp_path) -> FactorLab:
    """Create a FactorLab with mocked DeepSeek client."""
    mock_ds = MagicMock()
    log_path = str(tmp_path / "experiments" / "factor_trials.jsonl")
    return FactorLab(deepseek_client=mock_ds, experiment_log_path=log_path)


# ===================================================================
# T039-1: test_generate_candidates
# ===================================================================


class TestGenerateCandidates:
    """生成 N 个候选因子，各有 candidate_id/factor_family/params/status"""

    def test_generate_candidates(self, tmp_path):
        lab = _make_lab(tmp_path)
        lab.deepseek_client.generate_factor_candidates.return_value = SAMPLE_CANDIDATES

        result = lab.generate_candidates(
            current_code="# strategy code",
            metrics={"total_trades": 100},
            num_candidates=3,
        )

        assert len(result) == 3
        for c in result:
            assert "candidate_id" in c
            assert "factor_family" in c
            assert "params" in c
            assert c["status"] == "active"

    def test_generate_candidates_normalise_missing_fields(self, tmp_path):
        lab = _make_lab(tmp_path)
        lab.deepseek_client.generate_factor_candidates.return_value = [
            {"factor_family": "filter", "params": {"x": 1}},
        ]

        result = lab.generate_candidates(
            current_code="# code",
            metrics={},
            num_candidates=1,
        )

        assert len(result) == 1
        assert result[0]["candidate_id"] == "fc_001"
        assert result[0]["status"] == "active"


# ===================================================================
# T039-2: test_candidate_dedup
# ===================================================================


class TestCandidateDedup:
    """相同 factor_family + params 的候选不重复"""

    def test_candidate_dedup(self, tmp_path):
        lab = _make_lab(tmp_path)

        dup_candidates = [
            {
                "candidate_id": "fc_001",
                "factor_family": "volatility",
                "params": {"indicator": "RSI", "period": 14},
                "description": "first",
                "status": "active",
            },
            {
                "candidate_id": "fc_002",
                "factor_family": "volatility",
                "params": {"indicator": "RSI", "period": 14},
                "description": "duplicate",
                "status": "active",
            },
            {
                "candidate_id": "fc_003",
                "factor_family": "trend",
                "params": {"indicator": "EMA", "period": 20},
                "description": "different",
                "status": "active",
            },
        ]

        result = lab.deduplicate(dup_candidates)

        assert len(result) == 2
        # First occurrence kept
        assert result[0]["candidate_id"] == "fc_001"
        assert result[1]["candidate_id"] == "fc_003"

    def test_dedup_via_generate(self, tmp_path):
        """generate_candidates also deduplicates internally."""
        lab = _make_lab(tmp_path)
        lab.deepseek_client.generate_factor_candidates.return_value = [
            {
                "candidate_id": "fc_001",
                "factor_family": "momentum",
                "params": {"indicator": "MACD", "signal": 9},
                "description": "A",
            },
            {
                "candidate_id": "fc_002",
                "factor_family": "momentum",
                "params": {"indicator": "MACD", "signal": 9},
                "description": "B (duplicate)",
            },
        ]

        result = lab.generate_candidates("# code", {}, num_candidates=2)
        assert len(result) == 1


# ===================================================================
# T039-3: test_evaluate_candidate_promoted
# ===================================================================


class TestEvaluateCandidatePromoted:
    """得分达标且通过门控 → promoted"""

    def test_evaluate_candidate_promoted(self, tmp_path):
        lab = _make_lab(tmp_path)
        candidate = {
            "candidate_id": "fc_001",
            "factor_family": "volatility",
            "params": {"indicator": "RSI"},
            "description": "RSI filter",
            "status": "active",
        }

        mock_evaluator = MagicMock()
        mock_eval_result = MagicMock()
        mock_eval_result.passed = True
        mock_eval_result.score = 75.0
        mock_evaluator.evaluate.return_value = mock_eval_result

        result = lab.evaluate_candidate(
            candidate=candidate,
            backtest_metrics={"total_trades": 120},
            baseline_score=50.0,
            evaluator=mock_evaluator,
        )

        assert result["status"] == "promoted"


# ===================================================================
# T039-4: test_evaluate_candidate_quarantined
# ===================================================================


class TestEvaluateCandidateQuarantined:
    """得分低于基线 → quarantined"""

    def test_evaluate_candidate_quarantined(self, tmp_path):
        lab = _make_lab(tmp_path)
        candidate = {
            "candidate_id": "fc_002",
            "factor_family": "trend",
            "params": {"indicator": "EMA"},
            "description": "EMA cross",
            "status": "active",
        }

        mock_evaluator = MagicMock()
        mock_eval_result = MagicMock()
        mock_eval_result.passed = True
        mock_eval_result.score = 30.0  # Below baseline
        mock_evaluator.evaluate.return_value = mock_eval_result

        result = lab.evaluate_candidate(
            candidate=candidate,
            backtest_metrics={"total_trades": 50},
            baseline_score=50.0,
            evaluator=mock_evaluator,
        )

        assert result["status"] == "quarantined"

    def test_evaluate_candidate_gate_failure(self, tmp_path):
        """Gate failure means quarantined even if score is high."""
        lab = _make_lab(tmp_path)
        candidate = {
            "candidate_id": "fc_003",
            "factor_family": "filter",
            "params": {},
            "description": "test",
            "status": "active",
        }

        mock_evaluator = MagicMock()
        mock_eval_result = MagicMock()
        mock_eval_result.passed = False  # Gate failure
        mock_eval_result.score = 100.0
        mock_evaluator.evaluate.return_value = mock_eval_result

        result = lab.evaluate_candidate(
            candidate=candidate,
            backtest_metrics={},
            baseline_score=50.0,
            evaluator=mock_evaluator,
        )

        assert result["status"] == "quarantined"


# ===================================================================
# T039-5: test_experiment_log_written
# ===================================================================


class TestExperimentLogWritten:
    """实验结果写入 jsonl 日志"""

    def test_experiment_log_written(self, tmp_path):
        lab = _make_lab(tmp_path)

        candidate = {
            "candidate_id": "fc_001",
            "factor_family": "volatility",
            "params": {"indicator": "RSI", "period": 14},
            "status": "promoted",
        }
        metrics = {"total_trades": 120, "monthly_net_profit_avg": 55.0}

        lab.log_experiment(candidate, metrics, score=72.5)
        lab.log_experiment(candidate, metrics, score=73.0)

        assert os.path.exists(lab.experiment_log_path)

        with open(lab.experiment_log_path) as f:
            lines = f.readlines()

        assert len(lines) == 2

        record = json.loads(lines[0])
        assert record["candidate_id"] == "fc_001"
        assert record["factor_family"] == "volatility"
        assert record["score"] == 72.5
        assert "timestamp" in record
        assert record["metrics"] == metrics

    def test_get_active_candidates(self, tmp_path):
        lab = _make_lab(tmp_path)
        lab._candidates = [
            {"candidate_id": "fc_001", "status": "active"},
            {"candidate_id": "fc_002", "status": "promoted"},
            {"candidate_id": "fc_003", "status": "active"},
        ]

        active = lab.get_active_candidates()
        assert len(active) == 2
        assert all(c["status"] == "active" for c in active)
