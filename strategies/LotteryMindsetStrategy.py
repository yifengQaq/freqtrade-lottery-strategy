import datetime
import logging
from typing import Optional

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade


logger = logging.getLogger(__name__)


class WeeklyBudgetController:
    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cycle_start_day: int = 0,
        min_balance_ratio: float = 0.05,
    ):
        self.weekly_budget = weekly_budget
        self.weekly_target = weekly_target
        self.cycle_start_day = cycle_start_day
        self.min_balance_ratio = min_balance_ratio
        self.cycle_start_balance: float = 0.0
        self.current_balance: float = 0.0
        self.current_cycle_pnl: float = 0.0
        self.trade_count: int = 0
        self.is_active: bool = True

    def on_cycle_start(self, current_balance: float):
        self.cycle_start_balance = current_balance
        self.current_balance = current_balance
        self.current_cycle_pnl = 0.0
        self.trade_count = 0
        self.is_active = True

    def update_balance(self, current_balance: float):
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance
        self.trade_count += 1

    def get_stake_amount(self) -> float:
        return self.current_balance

    def should_stop(self, current_balance: float) -> tuple[bool, str]:
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance
        if current_balance >= self.weekly_target:
            self.is_active = False
            return True, f"TARGET_HIT: 余额 {current_balance:.2f} ≥ 目标 {self.weekly_target:.2f}, 共{self.trade_count}笔滚仓"
        min_threshold = self.cycle_start_balance * self.min_balance_ratio
        if current_balance <= max(min_threshold, 1.0):
            self.is_active = False
            return True, f"BUDGET_EXHAUSTED: 余额 {current_balance:.2f} ≤ 阈值 {max(min_threshold, 1.0):.2f}"
        now = datetime.datetime.utcnow()
        if now.weekday() == 6 and now.hour >= 23:
            self.is_active = False
            return True, f"WEEK_END_FORCE_CLOSE: 余额={current_balance:.2f}, PnL={self.current_cycle_pnl:+.2f}"
        return False, "ACTIVE"

    def can_open_trade(self) -> bool:
        return self.is_active


class LotteryMindsetStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    stoploss = -0.30
    minimal_roi = {
        "0": 1.5,
    }
    trailing_stop = False
    max_open_trades = 1
    stake_amount = "unlimited"
    leverage_value = 3
    rsi_period = IntParameter(7, 21, default=14, space="buy", optimize=True)
    rsi_oversold = IntParameter(20, 40, default=30, space="buy", optimize=True)
    rsi_overbought = IntParameter(60, 80, default=70, space="buy", optimize=True)
    bbands_period = IntParameter(10, 30, default=20, space="buy", optimize=True)
    bbands_std = DecimalParameter(1.5, 3.0, default=2.0, space="buy", optimize=True)
    adx_period = IntParameter(7, 28, default=14, space="buy", optimize=True)
    adx_threshold = IntParameter(15, 40, default=25, space="buy", optimize=True)
    process_only_new_candles = True
    startup_candle_count: int = 100
    use_exit_signal = True
    exit_profit_only = False
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.budget_controller = WeeklyBudgetController(
            weekly_budget=100.0,
            weekly_target=1000.0,
        )
        self._last_cycle_week: Optional[int] = None

    def leverage(self, pair: str, current_time: datetime.datetime,
                 current_rate: float, proposed_leverage: float,
                 max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return min(self.leverage_value, max_leverage)

    def custom_stake_amount(self, current_time: datetime.datetime,
                            current_rate: float, proposed_stake: float,
                            min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str],
                            side: str, **kwargs) -> float:
        wallet_balance = self.wallets.get_free(
            self.config.get("stake_currency", "USDT")
        )
        self.budget_controller.current_balance = wallet_balance
        stake = min(wallet_balance, max_stake)
        if min_stake and stake < min_stake:
            logger.info(
                f"[滚仓] 余额 {wallet_balance:.2f} 不足最小下注 {min_stake:.2f}"
            )
            return 0
        logger.info(
            f"[滚仓] ALL IN: {stake:.2f} USDT "
            f"(余额={wallet_balance:.2f}, 第{self.budget_controller.trade_count+1}笔)"
        )
        return stake

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi = ta.RSI(dataframe, timeperiod=int(self.rsi_period.value))
        dataframe["rsi"] = rsi
        bbands_upper, bbands_middle, bbands_lower = ta.BBANDS(
            dataframe,
            timeperiod=int(self.bbands_period.value),
            nbdevup=float(self.bbands_std.value),
            nbdevdn=float(self.bbands_std.value)
        )
        dataframe["bbands_upper"] = bbands_upper
        dataframe["bbands_lower"] = bbands_lower
        adx = ta.ADX(dataframe, timeperiod=int(self.adx_period.value))
        dataframe["adx"] = adx
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] > dataframe["bbands_upper"])
            & (dataframe["rsi"] < int(self.rsi_overbought.value))
        )
        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "adx_bb_rsi_breakout_long"
        short_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] < dataframe["bbands_lower"])
            & (dataframe["rsi"] > int(self.rsi_oversold.value))
        )
        dataframe.loc[short_conditions, "enter_short"] = 1
        dataframe.loc[short_conditions, "enter_tag"] = "adx_bb_rsi_breakout_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_exit = (
            (dataframe["close"] < dataframe["bbands_lower"])
        )
        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "bb_revert_long"
        short_exit = (
            (dataframe["close"] > dataframe["bbands_upper"])
        )
        dataframe.loc[short_exit, "exit_short"] = 1
        dataframe.loc[short_exit, "exit_tag"] = "bb_revert_short"
        return dataframe

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime.datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        current_week = current_time.isocalendar()[1]
        if self._last_cycle_week != current_week:
            wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
            self.budget_controller.on_cycle_start(wallet_balance)
            self._last_cycle_week = current_week
            logger.info(
                f"[BudgetCtrl] 新周期 week={current_week}, "
                f"起始余额={wallet_balance:.2f}"
            )
        if not self.budget_controller.can_open_trade():
            logger.info(f"[BudgetCtrl] 拒绝开仓 {pair} - 本周已达标或亏完")
            return False
        wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
        should_stop, reason = self.budget_controller.should_stop(wallet_balance)
        if should_stop:
            logger.info(f"[BudgetCtrl] 停机: {reason}")
            return False
        logger.info(
            f"[滚仓] 允许开仓 {pair} {side}, "
            f"余额={wallet_balance:.2f}, 第{self.budget_controller.trade_count+1}笔"
        )
        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime.datetime,
        **kwargs,
    ) -> bool:
        wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
        self.budget_controller.update_balance(wallet_balance)
        should_stop, reason = self.budget_controller.should_stop(wallet_balance)
        if should_stop:
            logger.info(
                f"[滚仓] 退出 {pair}, {exit_reason} → {reason}"
            )
        else:
            progress = wallet_balance / self.budget_controller.weekly_target
            logger.info(
                f"[滚仓] 退出 {pair}, {exit_reason}, "
                f"余额={wallet_balance:.2f}, 进度={progress:.0%}, 继续滚仓"
            )
        return True

    can_short = True
