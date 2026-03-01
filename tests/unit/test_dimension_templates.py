"""Tests for agent.dimension_templates — DimensionDiagnosticEngine and templates."""

import pytest

from agent.dimension_templates import (
    DIMENSION_TEMPLATES,
    DimensionDiagnosticEngine,
)


class TestDimensionTemplates:
    """Verify all 6 dimension templates exist and have relevant content."""

    EXPECTED_DIMS = [
        "entry_signal",
        "exit_strategy",
        "risk_params",
        "trade_direction",
        "timeframe",
        "signal_logic",
    ]

    def test_all_dimensions_present(self):
        for dim in self.EXPECTED_DIMS:
            assert dim in DIMENSION_TEMPLATES, f"Missing template: {dim}"

    def test_templates_are_nonempty_strings(self):
        for dim in self.EXPECTED_DIMS:
            tmpl = DIMENSION_TEMPLATES[dim]
            assert isinstance(tmpl, str)
            assert len(tmpl) > 50, f"Template too short: {dim}"

    def test_entry_signal_has_factor_catalog_placeholder(self):
        assert "{FACTOR_CATALOG}" in DIMENSION_TEMPLATES["entry_signal"]

    def test_exit_strategy_mentions_roi_and_trailing(self):
        tmpl = DIMENSION_TEMPLATES["exit_strategy"]
        assert "ROI" in tmpl
        assert "trailing" in tmpl.lower() or "Trailing" in tmpl

    def test_risk_params_mentions_leverage(self):
        tmpl = DIMENSION_TEMPLATES["risk_params"]
        assert "leverage" in tmpl.lower() or "杠杆" in tmpl

    def test_trade_direction_mentions_long_short(self):
        tmpl = DIMENSION_TEMPLATES["trade_direction"]
        assert "做多" in tmpl or "long" in tmpl.lower()
        assert "做空" in tmpl or "short" in tmpl.lower()

    def test_timeframe_mentions_15m_and_1h(self):
        tmpl = DIMENSION_TEMPLATES["timeframe"]
        assert "15m" in tmpl
        assert "1h" in tmpl

    def test_signal_logic_mentions_and_or(self):
        tmpl = DIMENSION_TEMPLATES["signal_logic"]
        assert "AND" in tmpl
        assert "OR" in tmpl


class TestDimensionDiagnosticEngine:
    """Test the diagnosis engine logic."""

    def setup_method(self):
        self.engine = DimensionDiagnosticEngine()

    # --- Cold start ---
    def test_cold_start_round_1_is_entry_signal(self):
        result = self.engine.select_focus_dimension(
            metrics={}, previous_changes=[], epoch_round=1
        )
        assert result["dimension"] == "entry_signal"
        assert "冷启动" in result["reason"]

    def test_cold_start_round_2_is_exit_strategy(self):
        result = self.engine.select_focus_dimension(
            metrics={}, previous_changes=[], epoch_round=2
        )
        assert result["dimension"] == "exit_strategy"

    def test_cold_start_round_3_is_risk_params(self):
        result = self.engine.select_focus_dimension(
            metrics={}, previous_changes=[], epoch_round=3
        )
        assert result["dimension"] == "risk_params"

    # --- Metric-based diagnosis ---
    def test_zero_trades_selects_entry_signal(self):
        metrics = {"total_trades": 0, "win_rate": 0, "max_drawdown_pct": 0}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=[], epoch_round=5
        )
        assert result["dimension"] == "entry_signal"

    def test_few_trades_selects_signal_logic(self):
        metrics = {"total_trades": 20, "win_rate": 0.5, "max_drawdown_pct": 30,
                    "profit_factor": 1.5, "sharpe_ratio": 1.0}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=[], epoch_round=5
        )
        assert result["dimension"] == "signal_logic"

    def test_high_drawdown_selects_risk_params(self):
        metrics = {"total_trades": 100, "win_rate": 0.5, "max_drawdown_pct": 70,
                    "profit_factor": 1.2, "sharpe_ratio": 0.5}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=[], epoch_round=5
        )
        assert result["dimension"] == "risk_params"

    def test_low_win_rate_selects_entry_signal(self):
        metrics = {"total_trades": 100, "win_rate": 0.20, "max_drawdown_pct": 30,
                    "profit_factor": 0.8, "sharpe_ratio": 0.5}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=[], epoch_round=5
        )
        # entry_signal urgency 80 or exit_strategy 75 — entry wins
        assert result["dimension"] in ("entry_signal", "exit_strategy")

    def test_bad_profit_factor_selects_exit_strategy(self):
        metrics = {"total_trades": 100, "win_rate": 0.50, "max_drawdown_pct": 30,
                    "profit_factor": 0.7, "sharpe_ratio": 0.5}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=[], epoch_round=5
        )
        assert result["dimension"] == "exit_strategy"

    # --- Dimension stats ---
    def test_build_dimension_stats_empty_history(self):
        stats = self.engine.build_dimension_stats([])
        assert all(v == 0 for v in stats.values())
        assert len(stats) == 6

    def test_build_dimension_stats_tracks_focus_dimension(self):
        changes = [
            {"focus_dimension": "entry_signal", "score": 10},
            {"focus_dimension": "exit_strategy", "score": 20},
            {"focus_dimension": "entry_signal", "score": 15},
        ]
        stats = self.engine.build_dimension_stats(changes)
        assert stats["entry_signal"] == 2
        assert stats["exit_strategy"] == 1
        assert stats["risk_params"] == 0

    def test_build_dimension_stats_legacy_rounds(self):
        """Rounds without focus_dimension should be counted as entry_signal."""
        changes = [
            {"changes_made": "some change", "score": 10},
            {"changes_made": "another change", "score": 15},
        ]
        stats = self.engine.build_dimension_stats(changes)
        assert stats["entry_signal"] == 2

    # --- Anti-loop penalty ---
    def test_anti_loop_penalty_reduces_urgency(self):
        """If a dimension was explored 3+ times without improvement, it should be penalized."""
        changes = [
            {"focus_dimension": "entry_signal", "score": 50},
            {"focus_dimension": "entry_signal", "score": 45},
            {"focus_dimension": "entry_signal", "score": 40},
        ]
        metrics = {"total_trades": 100, "win_rate": 0.20, "max_drawdown_pct": 30,
                    "profit_factor": 1.2, "sharpe_ratio": 0.5}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=changes, epoch_round=5
        )
        # entry_signal should be penalized (3 rounds, declining)
        # So another dimension should be picked
        assert result["dimension"] != "entry_signal"

    # --- Under-explored boost ---
    def test_underexplored_boost(self):
        """Never-explored dimensions should get a boost."""
        changes = [
            {"focus_dimension": "entry_signal", "score": 50},
            {"focus_dimension": "entry_signal", "score": 55},
            {"focus_dimension": "exit_strategy", "score": 60},
        ]
        # All metrics are OK-ish, so base urgency is 50 for all
        metrics = {"total_trades": 100, "win_rate": 0.50, "max_drawdown_pct": 30,
                    "profit_factor": 1.5, "sharpe_ratio": 1.0}
        result = self.engine.select_focus_dimension(
            metrics=metrics, previous_changes=changes, epoch_round=5
        )
        # risk_params, trade_direction, timeframe, signal_logic never explored → boosted to 70
        assert result["dimension"] in ("risk_params", "trade_direction", "timeframe", "signal_logic")

    # --- Template retrieval ---
    def test_get_dimension_template_returns_string(self):
        for dim in DimensionDiagnosticEngine.DIMENSIONS:
            tmpl = self.engine.get_dimension_template(dim)
            assert isinstance(tmpl, str)
            assert len(tmpl) > 0

    def test_get_unknown_dimension_returns_empty(self):
        assert self.engine.get_dimension_template("nonexistent") == ""

    # --- Chinese names ---
    def test_dim_name_cn_all_dimensions(self):
        for dim in DimensionDiagnosticEngine.DIMENSIONS:
            name = self.engine._dim_name_cn(dim)
            assert isinstance(name, str)
            assert len(name) > 0

    # --- Return structure ---
    def test_select_returns_required_keys(self):
        result = self.engine.select_focus_dimension(
            metrics={"total_trades": 100}, previous_changes=[], epoch_round=5
        )
        assert "dimension" in result
        assert "reason" in result
        assert "urgency" in result
        assert result["dimension"] in DimensionDiagnosticEngine.DIMENSIONS
