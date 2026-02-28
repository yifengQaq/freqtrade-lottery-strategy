"""
OP 策略的灵魂：周内滚仓复利模型

每周一重置：
  - 起始本金 100 USDT
  - 每笔 ALL IN 当前全部余额
  - 盈利后：本金+利润 → 继续 ALL IN 下一笔
  - 任何一笔亏完 → 本周停机
  - 余额滚到 ≥1000 USDT → 达标提现 + 停机
  - 周日 23:59 UTC 强制结算

示例路径：100 → 250 → 600 → 1100（达标）
"""

import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WeeklyBudgetController:
    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cycle_start_day: int = 0,       # 0 = Monday
        min_balance_ratio: float = 0.05, # 余额低于起始的5%视为亏完
    ):
        self.weekly_budget = weekly_budget
        self.weekly_target = weekly_target
        self.cycle_start_day = cycle_start_day
        self.min_balance_ratio = min_balance_ratio

        # 状态
        self.cycle_start_balance: float = 0.0
        self.current_balance: float = 0.0
        self.current_cycle_pnl: float = 0.0
        self.trade_count: int = 0
        self.is_active: bool = True

    def on_cycle_start(self, current_balance: float):
        """每周一调用：重置状态，起始余额即为本周本金"""
        self.cycle_start_balance = current_balance
        self.current_balance = current_balance
        self.current_cycle_pnl = 0.0
        self.trade_count = 0
        self.is_active = True
        logger.info(
            f"[BudgetCtrl] 新周期开始, "
            f"起始余额={current_balance:.2f} USDT"
        )

    def update_balance(self, current_balance: float):
        """每次交易结束后更新余额"""
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance
        self.trade_count += 1
        logger.info(
            f"[BudgetCtrl] 第{self.trade_count}笔交易后, "
            f"余额={current_balance:.2f}, PnL={self.current_cycle_pnl:+.2f}"
        )

    def get_stake_amount(self) -> float:
        """
        返回本次应下注金额 = 当前全部余额（ALL IN）。
        策略的 custom_stake_amount 会调用此方法。
        """
        return self.current_balance

    def should_stop(self, current_balance: float) -> tuple[bool, str]:
        """判断是否应停机"""
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance

        # 达标 → 提现停机（余额 ≥ 目标）
        if current_balance >= self.weekly_target:
            self.is_active = False
            return True, (
                f"TARGET_HIT: 余额 {current_balance:.2f} ≥ "
                f"目标 {self.weekly_target:.2f}, "
                f"共{self.trade_count}笔滚仓"
            )

        # 亏完 → 停机（余额低于起始的 min_balance_ratio）
        min_threshold = self.cycle_start_balance * self.min_balance_ratio
        if current_balance <= min_threshold:
            self.is_active = False
            return True, (
                f"BUDGET_EXHAUSTED: 余额 {current_balance:.2f} ≤ "
                f"阈值 {min_threshold:.2f}"
            )

        # 周日 23:00 UTC 后强制结算
        now = datetime.datetime.utcnow()
        if now.weekday() == 6 and now.hour >= 23:
            self.is_active = False
            return True, (
                f"WEEK_END_FORCE_CLOSE: 余额={current_balance:.2f}, "
                f"PnL={self.current_cycle_pnl:+.2f}"
            )

        return False, "ACTIVE"

    def can_open_trade(self) -> bool:
        """策略在 confirm_trade_entry 中调用"""
        return self.is_active

    @property
    def progress(self) -> float:
        """当前余额相对目标的进度 (0.0~1.0+)"""
        if self.weekly_target <= 0:
            return 0.0
        return self.current_balance / self.weekly_target
