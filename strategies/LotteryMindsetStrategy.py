"""
彩票心态合约策略 - LotteryMindsetStrategy
===========================================
OP 策略核心：日常高波动率突破
性质：趋势跟踪 + 波动率突破
不是高频，不是网格，是"等待 → 一击"

每周预算 100 USDT，目标 1000 USDT（1:10 盈亏比）
单笔 ALL IN，不分仓，不复利
达标提现，亏完停机

指标体系：ADX + Bollinger Bands + ATR
入场：趋势确认 + 波动率突破
出场：硬止损 + ROI 梯度止盈 + 移动止盈
"""

import datetime
import logging
from typing import Optional

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade


logger = logging.getLogger(__name__)


# ============================================
# WeeklyBudgetController（嵌入版）
# OP 策略的灵魂：周期性预算重置
# 不是复利模型，是"彩票模型"
# ============================================
class WeeklyBudgetController:
    """
    每周一重置：
      - 充入 100 USDT
      - 目标 1000 USDT
      - 达标 → 提现 + 停机
      - 亏完 → 停机
      - 周日 23:59 UTC 强制结算
    """

    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cycle_start_day: int = 0,  # 0 = Monday
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


# ============================================
# LotteryMindsetStrategy
# ============================================
class LotteryMindsetStrategy(IStrategy):
    """
    彩票心态合约策略

    入场条件（全部满足）：
      1. ADX > 25 （趋势存在）
      2. 价格突破 Bollinger Band 上轨（做多）或下轨（做空）
      3. ATR > 最小阈值（波动率足够）

    出场条件：
      1. 硬止损 -95%
      2. ROI 梯度止盈
      3. 移动止盈
    """

    # ---- 策略元数据 ----
    INTERFACE_VERSION = 3

    # ---- 时间框架 ----
    timeframe = "15m"

    # ---- 止损 ----
    stoploss = -0.95

    # ---- ROI 梯度止盈 ----
    minimal_roi = {
        "0": 10.0,      # 即刻 1000% 止盈
        "1440": 5.0,     # 1天后 500%
        "4320": 2.0,     # 3天后 200%
    }

    # ---- 移动止盈 ----
    trailing_stop = True
    trailing_stop_positive = 0.5           # 利润达 50% 后启动
    trailing_stop_positive_offset = 2.0    # 利润达 200% 后才开始追踪
    trailing_only_offset_is_reached = True

    # ---- 交易控制 ----
    max_open_trades = 1

    # ---- 杠杆 ----
    leverage_value = 5

    # ---- 可优化参数 ----
    adx_threshold = IntParameter(15, 40, default=25, space="buy", optimize=True)
    bb_period = IntParameter(10, 30, default=20, space="buy", optimize=True)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    atr_period = IntParameter(7, 21, default=14, space="buy", optimize=True)
    atr_min_multiplier = DecimalParameter(0.5, 2.0, default=1.0, decimals=1, space="buy", optimize=True)

    # ---- 仅在新蜡烛时处理 ----
    process_only_new_candles = True

    # ---- 启动所需蜡烛数 ----
    startup_candle_count: int = 30

    # ---- 不使用卖出信号（通过 ROI / trailing / stoploss 出场）----
    use_exit_signal = True
    exit_profit_only = False

    # ---- 订单类型 ----
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

    # ---- 杠杆设置 ----
    def leverage(self, pair: str, current_time: datetime.datetime,
                 current_rate: float, proposed_leverage: float,
                 max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        """固定杠杆倍数"""
        return min(self.leverage_value, max_leverage)

    # ---- 指标计算 ----
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算 ADX + Bollinger Bands + ATR 指标体系
        """
        # ADX - 趋势强度
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # Bollinger Bands - 波动率突破
        bb = ta.BBANDS(
            dataframe,
            timeperiod=int(self.bb_period.value),
            nbdevup=float(self.bb_std.value),
            nbdevdn=float(self.bb_std.value),
            matype=0,
        )
        dataframe["bb_upper"] = bb["upperband"]
        dataframe["bb_middle"] = bb["middleband"]
        dataframe["bb_lower"] = bb["lowerband"]

        # ATR - 波动率量化
        dataframe["atr"] = ta.ATR(
            dataframe, timeperiod=int(self.atr_period.value)
        )
        # ATR 移动平均作为基准阈值
        dataframe["atr_ma"] = dataframe["atr"].rolling(window=50).mean()

        # 辅助：收盘价相对 BB 位置
        dataframe["bb_width"] = (
            (dataframe["bb_upper"] - dataframe["bb_lower"]) / dataframe["bb_middle"]
        )

        return dataframe

    # ---- 入场信号 ----
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        入场条件（全部满足）：
        做多：
          1. ADX > threshold
          2. close > BB 上轨
          3. ATR > ATR_MA * multiplier
        做空：
          1. ADX > threshold
          2. close < BB 下轨
          3. ATR > ATR_MA * multiplier
        """
        # ---- 做多条件 ----
        long_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] > dataframe["bb_upper"])
            & (dataframe["atr"] > dataframe["atr_ma"] * float(self.atr_min_multiplier.value))
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "bb_breakout_long"

        # ---- 做空条件 ----
        short_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["atr"] > dataframe["atr_ma"] * float(self.atr_min_multiplier.value))
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[short_conditions, "enter_short"] = 1
        dataframe.loc[short_conditions, "enter_tag"] = "bb_breakout_short"

        return dataframe

    # ---- 出场信号 ----
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        出场主要依赖 stoploss / ROI / trailing_stop。
        额外信号：价格回归 BB 中轨时考虑退出。
        """
        # 做多出场：价格跌破 BB 中轨
        long_exit = (
            (dataframe["close"] < dataframe["bb_middle"])
            & (dataframe["adx"] < int(self.adx_threshold.value))
        )
        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "bb_mean_revert_long"

        # 做空出场：价格涨破 BB 中轨
        short_exit = (
            (dataframe["close"] > dataframe["bb_middle"])
            & (dataframe["adx"] < int(self.adx_threshold.value))
        )
        dataframe.loc[short_exit, "exit_short"] = 1
        dataframe.loc[short_exit, "exit_tag"] = "bb_mean_revert_short"

        return dataframe

    # ---- 确认入场（Budget Controller 集成） ----
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
        """
        在入场前检查 WeeklyBudgetController 是否允许开仓。
        同时处理周期重置逻辑。
        """
        # 周期重置检查
        current_week = current_time.isocalendar()[1]
        if self._last_cycle_week != current_week:
            # 新的一周，重置 budget controller
            wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
            self.budget_controller.on_cycle_start(wallet_balance)
            self._last_cycle_week = current_week
            logger.info(
                f"[BudgetCtrl] 新周期开始 week={current_week}, "
                f"balance={wallet_balance:.2f}"
            )

        # 检查预算
        if not self.budget_controller.can_open_trade():
            logger.info(
                f"[BudgetCtrl] 拒绝开仓 {pair} - 本周预算已耗尽或已达标"
            )
            return False

        # 检查是否应该停机
        wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
        should_stop, reason = self.budget_controller.should_stop(wallet_balance)
        if should_stop:
            logger.info(f"[BudgetCtrl] 停机: {reason}")
            return False

        logger.info(
            f"[BudgetCtrl] 允许开仓 {pair} {side}, "
            f"PnL={self.budget_controller.current_cycle_pnl:.2f}"
        )
        return True

    # ---- 可选：交易退出时更新 PnL ----
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
        """交易退出后更新 budget controller PnL"""
        wallet_balance = self.wallets.get_free(self.config.get("stake_currency", "USDT"))
        self.budget_controller.update_pnl(wallet_balance)
        logger.info(
            f"[BudgetCtrl] 交易退出 {pair}, reason={exit_reason}, "
            f"cycle_pnl={self.budget_controller.current_cycle_pnl:.2f}"
        )
        return True

    # ---- 做空支持 ----
    can_short = True
