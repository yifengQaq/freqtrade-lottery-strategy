"""
OP 策略的灵魂：周期性预算重置
不是复利模型，是"彩票模型"

每周一重置：
  - 充入 100 USDT
  - 目标 1000 USDT
  - 达标 → 提现 + 停机
  - 亏完 → 停机
  - 周日 23:59 UTC 强制结算
"""

import datetime
from typing import Optional


class WeeklyBudgetController:
    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cycle_start_day: int = 0,       # 0 = Monday
    ):
        self.weekly_budget = weekly_budget
        self.weekly_target = weekly_target
        self.cycle_start_day = cycle_start_day

        # 状态
        self.cycle_start_balance: float = 0.0
        self.current_cycle_pnl: float = 0.0
        self.is_active: bool = True

    def on_cycle_start(self, current_balance: float):
        """每周一调用：重置状态"""
        self.cycle_start_balance = current_balance
        self.current_cycle_pnl = 0.0
        self.is_active = True

    def update_pnl(self, current_balance: float):
        """每次交易结束后更新"""
        self.current_cycle_pnl = current_balance - self.cycle_start_balance

    def should_stop(self, current_balance: float) -> tuple[bool, str]:
        """判断是否应停机"""
        self.update_pnl(current_balance)

        # 达标 → 停
        if self.current_cycle_pnl >= self.weekly_target:
            self.is_active = False
            return True, f"TARGET_HIT: +{self.current_cycle_pnl:.2f} USDT"

        # 亏完 → 停
        if self.current_cycle_pnl <= -self.weekly_budget:
            self.is_active = False
            return True, f"BUDGET_EXHAUSTED: {self.current_cycle_pnl:.2f} USDT"

        # 周日 23:00 UTC 后强制结算
        now = datetime.datetime.utcnow()
        if now.weekday() == 6 and now.hour >= 23:
            self.is_active = False
            return True, f"WEEK_END_FORCE_CLOSE: {self.current_cycle_pnl:.2f} USDT"

        return False, "ACTIVE"

    def can_open_trade(self) -> bool:
        """策略在 confirm_trade_entry 中调用"""
        return self.is_active
