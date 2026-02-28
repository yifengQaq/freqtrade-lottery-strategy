"""
Unit tests for agent.strategy_modifier — safety checks, apply_patch, versioning, rollback.
"""

import os

import pytest

from agent.strategy_modifier import StrategyModifier


# ---------------------------------------------------------------------------
# Helper — minimal valid strategy code containing all REQUIRED_PATTERNS
# ---------------------------------------------------------------------------

VALID_STRATEGY_CODE = '''\
class WeeklyBudgetController:
    pass

class LotteryMindsetStrategy:
    stoploss = -0.95
    leverage = 5

    def can_open_trade(self):
        return True

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
'''


def _make_modifier(tmp_path) -> StrategyModifier:
    """Create a StrategyModifier with isolated temp directories and a seed strategy file."""
    strategy_dir = tmp_path / "strategies"
    backup_dir = tmp_path / "backups"
    strategy_dir.mkdir()
    # Write the initial valid strategy so get_current_code works
    (strategy_dir / "LotteryMindsetStrategy.py").write_text(VALID_STRATEGY_CODE)

    return StrategyModifier(
        strategy_dir=str(strategy_dir),
        backup_dir=str(backup_dir),
        strategy_filename="LotteryMindsetStrategy.py",
    )


# ===================================================================
# Syntax validation
# ===================================================================


class TestSyntaxValidation:
    """语法校验测试组"""

    def test_valid_syntax_passes(self, tmp_path):
        """合法 Python 代码通过语法检查"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(VALID_STRATEGY_CODE, round_num=1, changes_description="valid")
        assert result["success"] is True
        assert result["errors"] == []

    def test_invalid_syntax_rejected(self, tmp_path):
        """语法错误的代码被拒绝，errors 包含语法错误信息"""
        bad_code = "def foo(\n    # missing closing paren"
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(bad_code, round_num=1, changes_description="bad syntax")
        assert result["success"] is False
        assert any("Syntax error" in e for e in result["errors"])


# ===================================================================
# Safety checks
# ===================================================================


class TestSafetyChecks:
    """安全限制测试组"""

    def _code_with(self, **overrides) -> str:
        """Generate valid code with selectively overridden fields."""
        leverage = overrides.get("leverage", 5)
        stoploss = overrides.get("stoploss", -0.95)
        body = f"""\
class WeeklyBudgetController:
    pass

class LotteryMindsetStrategy:
    stoploss = {stoploss}
    leverage = {leverage}

    def can_open_trade(self):
        return True

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
"""
        return body

    # -- leverage ---------------------------------------------------

    def test_leverage_within_limit(self, tmp_path):
        """leverage = 10 ≤ 20 → 允许"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(self._code_with(leverage=10), round_num=1)
        assert result["success"] is True

    def test_leverage_exceeds_limit(self, tmp_path):
        """leverage = 25 > 20 → 拒绝"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(self._code_with(leverage=25), round_num=1)
        assert result["success"] is False
        assert any("Leverage" in e and "25" in e for e in result["errors"])

    # -- stoploss ---------------------------------------------------

    def test_stoploss_within_limit(self, tmp_path):
        """stoploss = -0.95 ≥ -0.98 → 允许"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(self._code_with(stoploss=-0.95), round_num=1)
        assert result["success"] is True

    def test_stoploss_too_wide(self, tmp_path):
        """stoploss = -0.99 < -0.98 → 拒绝"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(self._code_with(stoploss=-0.99), round_num=1)
        assert result["success"] is False
        assert any("Stoploss" in e or "stoploss" in e.lower() for e in result["errors"])

    # -- required patterns ------------------------------------------

    def test_missing_weekly_budget_controller(self, tmp_path):
        """缺少 WeeklyBudgetController 引用 → 拒绝"""
        code = """\
class LotteryMindsetStrategy:
    stoploss = -0.95
    leverage = 5

    def can_open_trade(self):
        return True

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(code, round_num=1)
        assert result["success"] is False
        assert any("WeeklyBudgetController" in e for e in result["errors"])

    def test_missing_can_open_trade(self, tmp_path):
        """缺少 can_open_trade 引用 → 拒绝"""
        code = """\
class WeeklyBudgetController:
    pass

class LotteryMindsetStrategy:
    stoploss = -0.95
    leverage = 5

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(code, round_num=1)
        assert result["success"] is False
        assert any("can_open_trade" in e for e in result["errors"])


# ===================================================================
# apply_patch — happy path
# ===================================================================


class TestApplyPatch:
    """apply_patch 正常流程测试组"""

    def test_apply_patch_success(self, tmp_path):
        """合法修改写入成功，success=True，backup_path 非空"""
        mod = _make_modifier(tmp_path)
        result = mod.apply_patch(VALID_STRATEGY_CODE, round_num=1, changes_description="first patch")
        assert result["success"] is True
        assert result["backup_path"] != ""
        assert result["errors"] == []

    def test_apply_patch_creates_backup(self, tmp_path):
        """apply 后 backup 文件存在且内容是旧代码"""
        mod = _make_modifier(tmp_path)
        old_code = mod.get_current_code()

        # Apply slightly different valid code
        new_code = VALID_STRATEGY_CODE.replace("leverage = 5", "leverage = 8")
        result = mod.apply_patch(new_code, round_num=1, changes_description="bump leverage")

        assert result["success"] is True
        backup_path = result["backup_path"]
        assert os.path.exists(backup_path)

        with open(backup_path, "r") as f:
            backup_content = f.read()
        assert backup_content == old_code

        # Current code should be the new code
        assert mod.get_current_code() == new_code


# ===================================================================
# Version management
# ===================================================================


class TestVersionManagement:
    """版本管理测试组"""

    def test_list_versions_empty(self, tmp_path):
        """无备份时返回空 list"""
        mod = _make_modifier(tmp_path)
        assert mod.list_versions() == []

    def test_list_versions_after_patches(self, tmp_path):
        """apply 两次后 list 返回 2 个版本"""
        mod = _make_modifier(tmp_path)

        code_v1 = VALID_STRATEGY_CODE.replace("leverage = 5", "leverage = 6")
        code_v2 = VALID_STRATEGY_CODE.replace("leverage = 5", "leverage = 8")

        mod.apply_patch(code_v1, round_num=1, changes_description="v1")
        mod.apply_patch(code_v2, round_num=2, changes_description="v2")

        versions = mod.list_versions()
        assert len(versions) == 2
        rounds = [v["round"] for v in versions]
        assert 1 in rounds
        assert 2 in rounds

    def test_rollback_success(self, tmp_path):
        """rollback 后当前代码恢复为指定轮次的备份版本"""
        mod = _make_modifier(tmp_path)
        original_code = mod.get_current_code()

        # Round 1: apply a change (backup contains original_code)
        code_v1 = VALID_STRATEGY_CODE.replace("leverage = 5", "leverage = 7")
        mod.apply_patch(code_v1, round_num=1, changes_description="round1")

        # Round 2: apply another change (backup contains code_v1)
        code_v2 = VALID_STRATEGY_CODE.replace("leverage = 5", "leverage = 9")
        mod.apply_patch(code_v2, round_num=2, changes_description="round2")

        # Current is code_v2
        assert mod.get_current_code() == code_v2

        # Rollback to round 1 → restores the backup made in round 1 (= original_code)
        assert mod.rollback(1) is True
        assert mod.get_current_code() == original_code

    def test_rollback_nonexistent_round(self, tmp_path):
        """回滚不存在的轮次返回 False"""
        mod = _make_modifier(tmp_path)
        assert mod.rollback(999) is False
