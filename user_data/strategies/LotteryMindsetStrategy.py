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
# 周内滚仓复利：ALL IN → 盈利加码 → 滚到目标
# ============================================
class WeeklyBudgetController:
    """
    周内滚仓复利模型：
      - 每周起始 100 USDT
      - 每笔 ALL IN 当前全部余额
      - 盈利 → 本金+利润 ALL IN 下一笔
      - 亏完 → 本周停机
      - 余额 ≥ 1000 → 达标提现 + 停机
      - 周日 23:59 UTC 强制结算
    """

    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cycle_start_day: int = 0,        # 0 = Monday
        min_balance_ratio: float = 0.05,  # 余额低于起始5%视为亏完
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
        """每周一调用：重置状态"""
        self.cycle_start_balance = current_balance
        self.current_balance = current_balance
        self.current_cycle_pnl = 0.0
        self.trade_count = 0
        self.is_active = True

    def update_balance(self, current_balance: float):
        """每次交易结束后更新余额"""
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance
        self.trade_count += 1

    def get_stake_amount(self) -> float:
        """返回本次下注金额 = 当前全部余额（ALL IN）"""
        return self.current_balance

    def should_stop(self, current_balance: float) -> tuple[bool, str]:
        """判断是否应停机"""
        self.current_balance = current_balance
        self.current_cycle_pnl = current_balance - self.cycle_start_balance

        if current_balance >= self.weekly_target:
            self.is_active = False
            return True, (
                f"TARGET_HIT: 余额 {current_balance:.2f} ≥ "
                f"目标 {self.weekly_target:.2f}, "
                f"共{self.trade_count}笔滚仓"
            )

        min_threshold = self.cycle_start_balance * self.min_balance_ratio
        if current_balance <= max(min_threshold, 1.0):
            self.is_active = False
            return True, (
                f"BUDGET_EXHAUSTED: 余额 {current_balance:.2f} ≤ "
                f"阈值 {max(min_threshold, 1.0):.2f}"
            )

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


# ============================================
# LotteryMindsetStrategy
# ============================================
class LotteryMindsetStrategy(IStrategy):
    """
    彩票心态合约策略 — 周内滚仓复利

    资金管理：
      - 每周 100 USDT 起始，ALL IN 全仓
      - 盈利后加码滚仓，滚到 ≥1000 USDT 达标
      - 亏完即停，等下周

    入场条件（全部满足）：
      1. ADX > 18 （趋势存在）
      2. 价格突破 Bollinger Band 上轨（做多）或下轨（做空）
      3. ATR > ATR_MA * 0.7（波动率足够）
      4. RSI > 50 做多，RSI < 50 做空（动量确认）

    出场条件：
      1. 硬止损 -40%（保护本金）
      2. ROI 梯度止盈（锁利用于下一笔滚仓）
      3. 移动止盈（让利润跑起来）
    """

    # ---- 策略元数据 ----
    INTERFACE_VERSION = 3

    # ---- 时间框架 ----
    timeframe = "15m"

    # ---- 止损 ----
    stoploss = -0.40

    # ---- ROI 梯度止盈 ----
    minimal_roi = {
        "0": 1.2,      # 即刻 +120% 止盈
        "30": 0.8,     # 30分钟后 +80%
        "120": 0.5,    # 2小时后 +50%
        "360": 0.3,    # 6小时后 +30%
    }

    # ---- 移动止盈 ----
    trailing_stop = True
    trailing_stop_positive = 0.20          # 利润达 20% 后启动追踪
    trailing_stop_positive_offset = 0.40   # 利润达 40% 后才开始追踪
    trailing_only_offset_is_reached = True

    # ---- 交易控制 ----
    max_open_trades = 1   # ALL IN 只能同时一笔

    # ---- 全仓模式 ----
    stake_amount = "unlimited"

    # ---- 杠杆 ----
    leverage_value = 5

    # ---- 可优化参数 ----
    adx_threshold = IntParameter(15, 40, default=18, space="buy", optimize=True)
    bb_period = IntParameter(10, 30, default=20, space="buy", optimize=True)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    atr_period = IntParameter(7, 21, default=14, space="buy", optimize=True)
    atr_min_multiplier = DecimalParameter(0.5, 2.0, default=0.7, decimals=1, space="buy", optimize=True)

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

    # ---- 自定义下注金额：ALL IN ----
    def custom_stake_amount(self, current_time: datetime.datetime,
                            current_rate: float, proposed_stake: float,
                            min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str],
                            side: str, **kwargs) -> float:
        """
        ALL IN 模式：每次用当前全部可用余额开仓。
        盈利后余额增加 → 下一笔下注更大 → 这就是滚仓复利。
        """
        wallet_balance = self.wallets.get_free(
            self.config.get("stake_currency", "USDT")
        )
        self.budget_controller.current_balance = wallet_balance

        # ALL IN，但不超过 max_stake
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

    # ---- 指标计算 ----
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算 ADX + Bollinger Bands + ATR + RSI 指标体系
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

        # RSI - 动量指标
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

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
          4. RSI > 50
        做空：
          1. ADX > threshold
          2. close < BB 下轨
          3. ATR > ATR_MA * multiplier
          4. RSI < 50
        """
        # ---- 做多条件 ----
        long_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] > dataframe["bb_upper"])
            & (dataframe["atr"] > dataframe["atr_ma"] * float(self.atr_min_multiplier.value))
            & (dataframe["rsi"] > 50)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "bb_breakout_long"

        # ---- 做空条件 ----
        short_conditions = (
            (dataframe["adx"] > int(self.adx_threshold.value))
            & (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["atr"] > dataframe["atr_ma"] * float(self.atr_min_multiplier.value))
            & (dataframe["rsi"] < 50)
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
        入场前检查：
        1. 周期重置（新的一周 → 重置 controller）
        2. 是否已达标/亏完 → 拒绝
        """
        # 周期重置检查
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

    # ---- 交易退出时更新余额并检查达标 ----
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
        """
        交易退出后：
        1. 更新 controller 余额
        2. 检查达标（余额 ≥ 1000 → 本周停机）
        3. 检查亏完（余额 ≈ 0 → 本周停机）
        4. 未达标 → 继续滚仓
        """
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

    # ---- 做空支持 ----
    can_short = True
