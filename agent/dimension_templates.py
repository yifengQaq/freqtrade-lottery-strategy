"""
Dimension templates and diagnostic engine for multi-dimension optimization.

Each dimension has a detailed guidance template that the LLM uses to focus
its modifications on a single optimization axis per round.  The
DimensionDiagnosticEngine selects which dimension to focus on based on
current metrics, exploration history, and anti-loop heuristics.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dimension Templates
# ---------------------------------------------------------------------------

DIMENSION_TEMPLATES: dict[str, str] = {}

DIMENSION_TEMPLATES["entry_signal"] = """\
## 入场信号优化指导

### 可选因子目录
{FACTOR_CATALOG}

### 每轮如何选因子
1. 从目录中选 2-4 个因子构成入场条件组合
2. 选择逻辑一致的因子：
   - ✅ 趋势确认(ADX>25) + 突破信号(close>BB_upper) + 动量确认(RSI>50)
   - ❌ 突破信号(close>BB_upper) + 超卖信号(RSI<30) ← 逻辑矛盾！

### 因子组合模式
| 模式 | 适合市况 | 示例 |
|------|---------|------|
| 趋势跟随 | 单边市 | ADX + EMA_CROSS + ATR_BREAKOUT |
| 均值回归 | 横盘市 | BBANDS + RSI + STOCH |
| 动量突破 | 大波动 | MACD + CCI + ATR |
| 量价共振 | 任何 | OBV + ADX + MOM |

### 参数范围
- ADX: 15-35, RSI: 20-80, CCI: -200~200
- EMA fast: 5-15, EMA slow: 20-50
- BBANDS period: 15-30, std: 1.5-3.0
- STOCH K: 3-14, D: 3-5

### ⚠️ 硬约束
- 必须用 AND 组合条件，但不超过 3 个核心条件
- 入场条件不能逻辑矛盾
"""

DIMENSION_TEMPLATES["exit_strategy"] = """\
## 出场策略优化指导

### ROI 梯度模板
你可以设计多级止盈，根据持仓时间逐步降低止盈要求：

| 类型 | 配置 | 适合场景 |
|------|------|---------|
| 激进快出 | {"30": 0.80, "60": 0.40, "120": 0.15} | 高波动、短线 |
| 稳健均衡 | {"60": 1.00, "120": 0.50, "240": 0.20} | 中等波动 |
| 耐心持有 | {"120": 0.50, "360": 0.30, "720": 0.15} | 趋势跟随 |

### Trailing Stop 配置
| 模式 | 参数 | 说明 |
|------|------|------|
| 关闭 | trailing_stop=False | 不用追踪止损 |
| 紧跟 | trailing_stop=True, trailing_stop_positive=0.01, trailing_stop_positive_offset=0.02 | 盈利2%后跟1% |
| 宽松 | trailing_stop=True, trailing_stop_positive=0.02, trailing_stop_positive_offset=0.05 | 盈利5%后跟2% |
| 激进 | trailing_stop=True, trailing_stop_positive=0.03, trailing_stop_positive_offset=0.10 | 盈利10%后跟3% |

### Stoploss 值
- 范围: -0.20 ~ -0.50
- 与杠杆关系: leverage × |stoploss| ≤ 100%
- 推荐: 3x/-0.30, 5x/-0.20, 7x/-0.14

### ⚠️ 关键原则
- ROI + trailing + stoploss 三者协调：不要 ROI 设得很高但 stoploss 很紧
- 如果胜率高但总收益低，考虑放宽 ROI 让盈利单跑更久
- 如果胜率低但单笔盈利大，trailing stop 能帮助保护利润
"""

DIMENSION_TEMPLATES["risk_params"] = """\
## 风控参数优化指导

### 杠杆倍数
| 杠杆 | 风险等级 | 适合策略 | 推荐stoploss |
|------|---------|---------|-------------|
| 3x | 低风险 | 保守/新策略 | -0.30 ~ -0.40 |
| 5x | 中风险 | 验证过的策略 | -0.20 ~ -0.25 |
| 7x | 高风险 | 高胜率策略 | -0.14 ~ -0.18 |
| 10x | 极高风险 | 极高胜率+严格止损 | -0.10 ~ -0.12 |

### 安全约束
- **铁律**: leverage × |stoploss| ≤ 100%（否则单笔爆仓）
- 推荐: leverage × |stoploss| ≤ 80%（留20%缓冲）
- 回撤超60%时，必须降杠杆或收紧 stoploss

### 代码示例
```python
# 在策略类中修改
leverage_value = 5  # 3-10 范围
stoploss = -0.20    # 与杠杆协调

def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
    return self.leverage_value
```

### ⚠️ 关键原则
- 高杠杆+宽stoploss = 灾难（10x × 40% = 爆仓）
- 优先降杠杆而非收紧stoploss（stoploss太紧会频繁止损）
- 新策略从 3x 开始试，验证盈利后再加杠杆
"""

DIMENSION_TEMPLATES["trade_direction"] = """\
## 交易方向优化指导

### 三种模式
| 模式 | 实现方式 | 适合市况 |
|------|---------|---------|
| 只做多 | enter_short条件全部设为0 | 牛市/上升趋势 |
| 只做空 | enter_long条件全部设为0 | 熊市/下降趋势 |
| 双向 | 保留两个方向的入场条件 | 震荡市/不确定市况 |

### 方向选择指导
- 如果回测显示做多盈利、做空亏损 → 试试只做多
- 如果回测显示做空盈利、做多亏损 → 试试只做空
- 如果两个方向都亏 → 先修入场信号再考虑方向
- 做空的入场条件应该比做多更严格（空头市场反弹剧烈）

### 代码模板
```python
# 只做多: 清空做空条件
def populate_entry_trend(self, dataframe, metadata):
    dataframe.loc[
        (条件1) & (条件2) & (dataframe["volume"] > 0),
        ["enter_long", "enter_tag"],
    ] = (1, "long_signal")
    # 不设置 enter_short
    return dataframe

# 双向: 独立设置
def populate_entry_trend(self, dataframe, metadata):
    dataframe.loc[多头条件, ["enter_long", "enter_tag"]] = (1, "long_signal")
    dataframe.loc[空头条件, ["enter_short", "enter_tag"]] = (1, "short_signal")
    return dataframe
```

### ⚠️ 注意
- can_short = True 必须保持
- 即使只做多，也不要删除 can_short 属性
- 做空条件要额外加趋势过滤（如EMA排列确认下跌）
"""

DIMENSION_TEMPLATES["timeframe"] = """\
## 时间框架优化指导

### 对比分析
| 参数 | 15m | 1h |
|------|-----|-----|
| 信号频率 | 高（更多交易机会） | 低（更少但更可靠） |
| 噪音 | 多（假信号多） | 少（信号更清晰） |
| 适合策略 | 均值回归、快进快出 | 趋势跟随、动量 |
| 持仓时间 | 通常 <4h | 通常 4h-24h |
| ROI设置 | 较低的止盈点(30-80%) | 较高的止盈点(50-200%) |

### 何时切换
- 当前 total_trades 太少（<20/窗口）→ 考虑用 15m 增加信号
- 当前假信号太多（win_rate < 30%）→ 考虑用 1h 过滤噪音
- 策略是趋势跟随型 → 1h 更合适
- 策略是均值回归型 → 15m 更合适

### ⚠️ 硬约束
- **只能用 "15m" 或 "1h"**，改成 5m/4h/1d 等会报错！
- 切换 timeframe 后需要同步调整 ROI 时间节点
- 15m 切到 1h: ROI 时间节点 ×4（如 60min → 240min）
"""

DIMENSION_TEMPLATES["signal_logic"] = """\
## 信号逻辑结构优化指导

### 组合模式

#### 1. 严格AND（默认模式）
所有条件必须同时满足才触发信号
- 优点: 信号精准，胜率高
- 缺点: 交易次数可能太少
```python
dataframe.loc[
    (dataframe["adx"] > 25) &
    (dataframe["rsi"] > 50) &
    (dataframe["close"] > dataframe["bb_upper"]) &
    (dataframe["volume"] > 0),
    ["enter_long", "enter_tag"],
] = (1, "strict_and")
```

#### 2. 分组OR（多策略入场）
多组独立条件，任一组满足即触发
- 优点: 交易次数增多，适应不同市况
- 缺点: 胜率可能下降
```python
# 第一组: 趋势突破
condition_trend = (
    (dataframe["adx"] > 25) &
    (dataframe["close"] > dataframe["ema_slow"])
)
# 第二组: 超卖反弹
condition_reversal = (
    (dataframe["rsi"] < 30) &
    (dataframe["stoch_k"] < 20)
)
dataframe.loc[
    (condition_trend | condition_reversal) &
    (dataframe["volume"] > 0),
    ["enter_long", "enter_tag"],
] = (1, "multi_group")
```

#### 3. 核心+过滤器（推荐）
一个核心信号 + 多个过滤条件
- 优点: 兼顾信号量和质量
- 缺点: 需要仔细选择核心信号
```python
# 核心信号: MACD金叉
core_signal = (dataframe["macd"] > dataframe["macd_signal"])
# 过滤器: 趋势方向 + 波动率
trend_filter = (dataframe["adx"] > 20)
vol_filter = (dataframe["atr"] > dataframe["atr"].rolling(50).mean())
dataframe.loc[
    core_signal & trend_filter & vol_filter & (dataframe["volume"] > 0),
    ["enter_long", "enter_tag"],
] = (1, "core_plus_filter")
```

### ⚠️ 注意
- 0笔交易意味着条件太严或逻辑矛盾，优先尝试分组OR模式
- 不要超过4个AND条件（太难同时满足）
- 分组OR最多3组（太多会降低信号质量）
"""


# ---------------------------------------------------------------------------
# DimensionDiagnosticEngine
# ---------------------------------------------------------------------------


class DimensionDiagnosticEngine:
    """Diagnoses which optimization dimension to focus on based on metrics and history."""

    DIMENSIONS = [
        "entry_signal",
        "exit_strategy",
        "risk_params",
        "trade_direction",
        "timeframe",
        "signal_logic",
    ]

    # Cold start: first 3 rounds of each epoch force these dimensions
    COLD_START_SEQUENCE = ["entry_signal", "exit_strategy", "risk_params"]

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_focus_dimension(
        self,
        metrics: dict,
        previous_changes: list[dict],
        epoch_round: int,  # round within current epoch (1-based)
    ) -> dict:
        """Select the focus dimension for the current round.

        Returns:
            {
                "dimension": str,      # dimension key
                "reason": str,         # diagnosis reason in Chinese
                "urgency": int,        # urgency score
            }
        """
        # Cold start: first 3 rounds force specific dimensions
        if epoch_round <= len(self.COLD_START_SEQUENCE):
            dim = self.COLD_START_SEQUENCE[epoch_round - 1]
            return {
                "dimension": dim,
                "reason": (
                    f"冷启动阶段（第{epoch_round}轮），"
                    f"强制聚焦「{self._dim_name_cn(dim)}」确保基础维度覆盖"
                ),
                "urgency": 100,
            }

        # Compute urgency scores for each dimension
        urgencies = self._compute_urgencies(metrics)

        # Get dimension exploration history
        dim_history = self._build_dimension_history(previous_changes)

        # Apply anti-loop penalty: if a dimension was explored 3+ consecutive
        # times without improvement, reduce its urgency
        urgencies = self._apply_anti_loop_penalty(urgencies, dim_history)

        # Boost under-explored dimensions
        urgencies = self._boost_underexplored(urgencies, dim_history)

        # Select highest urgency
        best_dim = max(urgencies, key=urgencies.get)

        return {
            "dimension": best_dim,
            "reason": self._format_diagnosis(best_dim, metrics, urgencies[best_dim]),
            "urgency": urgencies[best_dim],
        }

    def get_dimension_template(self, dimension: str) -> str:
        """Get the detailed template text for a dimension."""
        return DIMENSION_TEMPLATES.get(dimension, "")

    def build_dimension_stats(self, previous_changes: list[dict]) -> dict[str, int]:
        """Count how many times each dimension has been explored."""
        stats: dict[str, int] = {d: 0 for d in self.DIMENSIONS}
        for change in previous_changes:
            dim = change.get("focus_dimension", "entry_signal")
            if dim in stats:
                stats[dim] += 1
            else:
                # Legacy rounds without focus_dimension → count as entry_signal
                stats["entry_signal"] += 1
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_urgencies(self, metrics: dict) -> dict[str, int]:
        """Compute urgency score for each dimension based on metrics."""
        urgencies: dict[str, int] = {d: 50 for d in self.DIMENSIONS}  # base urgency

        total_trades = metrics.get("total_trades", 0) or metrics.get("trade_count", 0)
        win_rate = metrics.get("win_rate", 0)
        max_dd = metrics.get("max_drawdown_pct", 0) or abs(
            metrics.get("max_drawdown", 0)
        )
        profit_factor = metrics.get("profit_factor", 1.0)
        avg_profit = metrics.get("avg_profit_pct", 0)
        sharpe = metrics.get("sharpe_ratio", 0)

        # No trades = entry signal problem
        if total_trades == 0:
            urgencies["entry_signal"] = 100
            urgencies["signal_logic"] = 90
            return urgencies

        # Too few trades
        if total_trades < 50:
            urgencies["signal_logic"] = 90
            urgencies["entry_signal"] = 80

        # High drawdown = risk problem
        if max_dd > 60:
            urgencies["risk_params"] = 85

        # Low win rate = entry signal problem
        if win_rate < 0.30:
            urgencies["entry_signal"] = 80

        # Bad profit factor = exit problem
        if profit_factor < 1.0:
            urgencies["exit_strategy"] = 75

        # Win rate OK but losing money = exit problem
        if avg_profit < 0 and win_rate > 0.40:
            urgencies["exit_strategy"] = 70

        # Negative sharpe = risk problem
        if sharpe < 0:
            urgencies["risk_params"] = max(urgencies["risk_params"], 65)

        return urgencies

    def _build_dimension_history(
        self, previous_changes: list[dict]
    ) -> dict[str, list]:
        """Build per-dimension score history."""
        history: dict[str, list] = {d: [] for d in self.DIMENSIONS}
        for change in previous_changes:
            dim = change.get("focus_dimension", "entry_signal")
            score = change.get("score", 0)
            if dim in history:
                history[dim].append(score)
            else:
                history["entry_signal"].append(score)
        return history

    def _apply_anti_loop_penalty(
        self,
        urgencies: dict[str, int],
        dim_history: dict[str, list],
    ) -> dict[str, int]:
        """If a dimension was explored 3+ consecutive last times without improvement, penalize."""
        for dim, scores in dim_history.items():
            if len(scores) >= 3:
                recent = scores[-3:]
                if recent[-1] <= recent[0]:  # no improvement over last 3
                    urgencies[dim] = int(urgencies[dim] * 0.3)
        return urgencies

    def _boost_underexplored(
        self,
        urgencies: dict[str, int],
        dim_history: dict[str, list],
    ) -> dict[str, int]:
        """Boost dimensions that have never been explored."""
        for dim in self.DIMENSIONS:
            if len(dim_history[dim]) == 0:
                urgencies[dim] = max(urgencies[dim], 70)  # at least 70
            elif len(dim_history[dim]) <= 1:
                urgencies[dim] = max(urgencies[dim], 60)  # slight boost
        return urgencies

    @staticmethod
    def _dim_name_cn(dim: str) -> str:
        """Return Chinese name for a dimension."""
        names = {
            "entry_signal": "入场信号",
            "exit_strategy": "出场策略",
            "risk_params": "风控参数",
            "trade_direction": "交易方向",
            "timeframe": "时间框架",
            "signal_logic": "信号逻辑结构",
        }
        return names.get(dim, dim)

    def _format_diagnosis(self, dim: str, metrics: dict, urgency: int) -> str:
        """Format a human-readable diagnosis reason in Chinese."""
        cn_name = self._dim_name_cn(dim)
        total_trades = metrics.get("total_trades", 0)
        win_rate = metrics.get("win_rate", 0)
        max_dd = metrics.get("max_drawdown_pct", 0)
        profit_factor = metrics.get("profit_factor", 1.0)

        reasons = {
            "entry_signal": (
                f"胜率={win_rate:.1%}偏低 或 交易数={total_trades}不足，"
                f"需要优化入场条件"
            ),
            "exit_strategy": (
                f"盈亏因子={profit_factor:.2f}<1.0，赢少输多，"
                f"需要改善出场时机"
            ),
            "risk_params": (
                f"最大回撤={max_dd:.1f}%过大 或 夏普比率为负，"
                f"需要调整风控"
            ),
            "trade_direction": (
                "建议尝试不同交易方向（只做多/只做空/双向），寻找更优方向"
            ),
            "timeframe": (
                f"当前交易数={total_trades}，考虑切换时间框架调整信号频率"
            ),
            "signal_logic": (
                f"交易数={total_trades}可能过少，"
                f"考虑调整信号逻辑结构（AND/OR组合方式）"
            ),
        }

        return (
            f"诊断: {cn_name}维度（紧迫度={urgency}）— "
            f"{reasons.get(dim, '需要优化')}"
        )
