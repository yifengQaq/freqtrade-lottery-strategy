"""
Factor Template Library — curated collection of talib indicator templates.

Provides:
1. 40+ talib-based indicator templates with signal generation
2. Random sampling for exploration
3. Code assembly for freqtrade strategy integration

Each template is a self-contained unit:
- indicator_code: Python snippet to compute the indicator
- signal_long / signal_short: Boolean condition expressions
- params: Default parameter values
- param_ranges: Allowed ranges for LLM tuning
"""

import random
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# Factor Template Definitions
# ═══════════════════════════════════════════════════════════════

_TEMPLATES: list[dict] = [
    # ── Trend ──────────────────────────────────────────────────
    {
        "name": "ADX",
        "family": "trend",
        "indicator_code": 'dataframe["adx"] = ta.ADX(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["adx"] > {threshold})',
        "signal_short": '(dataframe["adx"] > {threshold})',
        "params": {"period": 14, "threshold": 20},
        "param_ranges": {"period": [7, 28], "threshold": [15, 40]},
    },
    {
        "name": "AROON_CROSS",
        "family": "trend",
        "indicator_code": (
            'dataframe["aroondown"], dataframe["aroonup"] = ta.AROON(dataframe, timeperiod={period})'
        ),
        "signal_long": '(dataframe["aroonup"] > {up_threshold})',
        "signal_short": '(dataframe["aroondown"] > {down_threshold})',
        "params": {"period": 14, "up_threshold": 70, "down_threshold": 70},
        "param_ranges": {"period": [7, 25], "up_threshold": [60, 90], "down_threshold": [60, 90]},
    },
    {
        "name": "SAR",
        "family": "trend",
        "indicator_code": (
            'dataframe["sar"] = ta.SAR(dataframe, acceleration={accel}, maximum={maximum})'
        ),
        "signal_long": '(dataframe["close"] > dataframe["sar"])',
        "signal_short": '(dataframe["close"] < dataframe["sar"])',
        "params": {"accel": 0.02, "maximum": 0.2},
        "param_ranges": {"accel": [0.01, 0.05], "maximum": [0.1, 0.3]},
    },
    {
        "name": "PLUS_MINUS_DI",
        "family": "trend",
        "indicator_code": (
            'dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod={period})\n'
            '        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod={period})'
        ),
        "signal_long": '(dataframe["plus_di"] > dataframe["minus_di"])',
        "signal_short": '(dataframe["minus_di"] > dataframe["plus_di"])',
        "params": {"period": 14},
        "param_ranges": {"period": [7, 28]},
    },
    {
        "name": "MACD_CROSS",
        "family": "trend",
        "indicator_code": (
            'dataframe["macd"], dataframe["macdsignal"], dataframe["macdhist"] = '
            "ta.MACD(dataframe, fastperiod={fast}, slowperiod={slow}, signalperiod={signal})"
        ),
        "signal_long": '(dataframe["macd"] > dataframe["macdsignal"])',
        "signal_short": '(dataframe["macd"] < dataframe["macdsignal"])',
        "params": {"fast": 12, "slow": 26, "signal": 9},
        "param_ranges": {"fast": [8, 16], "slow": [20, 34], "signal": [5, 14]},
    },
    {
        "name": "MACD_HIST",
        "family": "trend",
        "indicator_code": (
            'dataframe["macd"], dataframe["macdsignal"], dataframe["macdhist"] = '
            "ta.MACD(dataframe, fastperiod={fast}, slowperiod={slow}, signalperiod={signal})"
        ),
        "signal_long": '(dataframe["macdhist"] > 0)',
        "signal_short": '(dataframe["macdhist"] < 0)',
        "params": {"fast": 12, "slow": 26, "signal": 9},
        "param_ranges": {"fast": [8, 16], "slow": [20, 34], "signal": [5, 14]},
    },
    {
        "name": "HT_TRENDLINE",
        "family": "trend",
        "indicator_code": 'dataframe["ht_trendline"] = ta.HT_TRENDLINE(dataframe)',
        "signal_long": '(dataframe["close"] > dataframe["ht_trendline"])',
        "signal_short": '(dataframe["close"] < dataframe["ht_trendline"])',
        "params": {},
        "param_ranges": {},
    },
    {
        "name": "ADXR",
        "family": "trend",
        "indicator_code": 'dataframe["adxr"] = ta.ADXR(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["adxr"] > {threshold})',
        "signal_short": '(dataframe["adxr"] > {threshold})',
        "params": {"period": 14, "threshold": 20},
        "param_ranges": {"period": [7, 28], "threshold": [15, 35]},
    },

    # ── Momentum ───────────────────────────────────────────────
    {
        "name": "RSI",
        "family": "momentum",
        "indicator_code": 'dataframe["rsi"] = ta.RSI(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["rsi"] < {oversold})',
        "signal_short": '(dataframe["rsi"] > {overbought})',
        "params": {"period": 14, "oversold": 30, "overbought": 70},
        "param_ranges": {"period": [7, 21], "oversold": [20, 40], "overbought": [60, 80]},
    },
    {
        "name": "RSI_TREND",
        "family": "momentum",
        "indicator_code": 'dataframe["rsi"] = ta.RSI(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["rsi"] > {bull_threshold})',
        "signal_short": '(dataframe["rsi"] < {bear_threshold})',
        "params": {"period": 14, "bull_threshold": 50, "bear_threshold": 50},
        "param_ranges": {"period": [7, 21], "bull_threshold": [45, 60], "bear_threshold": [40, 55]},
    },
    {
        "name": "STOCHRSI",
        "family": "momentum",
        "indicator_code": (
            'dataframe["stochrsi_k"], dataframe["stochrsi_d"] = ta.STOCHRSI('
            "dataframe, timeperiod={period}, fastk_period={fastk}, "
            "fastd_period={fastd}, fastd_matype=0)"
        ),
        "signal_long": '(dataframe["stochrsi_k"] < {oversold})',
        "signal_short": '(dataframe["stochrsi_k"] > {overbought})',
        "params": {"period": 14, "fastk": 5, "fastd": 3, "oversold": 20, "overbought": 80},
        "param_ranges": {
            "period": [7, 21], "fastk": [3, 8], "fastd": [2, 5],
            "oversold": [10, 30], "overbought": [70, 90],
        },
    },
    {
        "name": "CCI",
        "family": "momentum",
        "indicator_code": 'dataframe["cci"] = ta.CCI(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["cci"] < -{threshold})',
        "signal_short": '(dataframe["cci"] > {threshold})',
        "params": {"period": 14, "threshold": 100},
        "param_ranges": {"period": [7, 28], "threshold": [80, 200]},
    },
    {
        "name": "MOM",
        "family": "momentum",
        "indicator_code": 'dataframe["mom"] = ta.MOM(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["mom"] > 0)',
        "signal_short": '(dataframe["mom"] < 0)',
        "params": {"period": 10},
        "param_ranges": {"period": [5, 20]},
    },
    {
        "name": "ROC",
        "family": "momentum",
        "indicator_code": 'dataframe["roc"] = ta.ROC(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["roc"] > {threshold})',
        "signal_short": '(dataframe["roc"] < -{threshold})',
        "params": {"period": 10, "threshold": 0},
        "param_ranges": {"period": [5, 20], "threshold": [0, 5]},
    },
    {
        "name": "WILLR",
        "family": "momentum",
        "indicator_code": 'dataframe["willr"] = ta.WILLR(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["willr"] < -{oversold})',
        "signal_short": '(dataframe["willr"] > -{overbought})',
        "params": {"period": 14, "oversold": 80, "overbought": 20},
        "param_ranges": {"period": [7, 21], "oversold": [70, 90], "overbought": [10, 30]},
    },
    {
        "name": "ULTOSC",
        "family": "momentum",
        "indicator_code": (
            'dataframe["ultosc"] = ta.ULTOSC(dataframe, '
            "timeperiod1={p1}, timeperiod2={p2}, timeperiod3={p3})"
        ),
        "signal_long": '(dataframe["ultosc"] < {oversold})',
        "signal_short": '(dataframe["ultosc"] > {overbought})',
        "params": {"p1": 7, "p2": 14, "p3": 28, "oversold": 30, "overbought": 70},
        "param_ranges": {
            "p1": [5, 10], "p2": [10, 20], "p3": [20, 40],
            "oversold": [20, 40], "overbought": [60, 80],
        },
    },
    {
        "name": "BOP",
        "family": "momentum",
        "indicator_code": 'dataframe["bop"] = ta.BOP(dataframe)',
        "signal_long": '(dataframe["bop"] > {threshold})',
        "signal_short": '(dataframe["bop"] < -{threshold})',
        "params": {"threshold": 0},
        "param_ranges": {"threshold": [0, 0.3]},
    },
    {
        "name": "CMO",
        "family": "momentum",
        "indicator_code": 'dataframe["cmo"] = ta.CMO(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["cmo"] > {threshold})',
        "signal_short": '(dataframe["cmo"] < -{threshold})',
        "params": {"period": 14, "threshold": 0},
        "param_ranges": {"period": [7, 28], "threshold": [0, 30]},
    },
    {
        "name": "MFI",
        "family": "momentum",
        "indicator_code": 'dataframe["mfi"] = ta.MFI(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["mfi"] < {oversold})',
        "signal_short": '(dataframe["mfi"] > {overbought})',
        "params": {"period": 14, "oversold": 20, "overbought": 80},
        "param_ranges": {"period": [7, 21], "oversold": [10, 30], "overbought": [70, 90]},
    },
    {
        "name": "STOCH",
        "family": "momentum",
        "indicator_code": (
            'dataframe["slowk"], dataframe["slowd"] = ta.STOCH('
            "dataframe, fastk_period={fastk}, slowk_period={slowk}, "
            "slowk_matype=0, slowd_period={slowd}, slowd_matype=0)"
        ),
        "signal_long": '(dataframe["slowk"] < {oversold})',
        "signal_short": '(dataframe["slowk"] > {overbought})',
        "params": {"fastk": 5, "slowk": 3, "slowd": 3, "oversold": 20, "overbought": 80},
        "param_ranges": {
            "fastk": [3, 14], "slowk": [2, 5], "slowd": [2, 5],
            "oversold": [10, 30], "overbought": [70, 90],
        },
    },
    {
        "name": "APO",
        "family": "momentum",
        "indicator_code": (
            'dataframe["apo"] = ta.APO(dataframe, fastperiod={fast}, '
            "slowperiod={slow}, matype=0)"
        ),
        "signal_long": '(dataframe["apo"] > 0)',
        "signal_short": '(dataframe["apo"] < 0)',
        "params": {"fast": 12, "slow": 26},
        "param_ranges": {"fast": [8, 16], "slow": [20, 34]},
    },
    {
        "name": "PPO",
        "family": "momentum",
        "indicator_code": (
            'dataframe["ppo"] = ta.PPO(dataframe, fastperiod={fast}, '
            "slowperiod={slow}, matype=0)"
        ),
        "signal_long": '(dataframe["ppo"] > 0)',
        "signal_short": '(dataframe["ppo"] < 0)',
        "params": {"fast": 12, "slow": 26},
        "param_ranges": {"fast": [8, 16], "slow": [20, 34]},
    },
    {
        "name": "TRIX",
        "family": "momentum",
        "indicator_code": 'dataframe["trix"] = ta.TRIX(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["trix"] > 0)',
        "signal_short": '(dataframe["trix"] < 0)',
        "params": {"period": 30},
        "param_ranges": {"period": [15, 40]},
    },

    # ── Volatility ─────────────────────────────────────────────
    {
        "name": "BBANDS_BREAKOUT",
        "family": "volatility",
        "indicator_code": (
            'dataframe["bb_upper"], dataframe["bb_middle"], dataframe["bb_lower"] = '
            "ta.BBANDS(dataframe, timeperiod={period}, nbdevup={std}, nbdevdn={std}, matype=0)"
        ),
        "signal_long": '(dataframe["close"] > dataframe["bb_upper"])',
        "signal_short": '(dataframe["close"] < dataframe["bb_lower"])',
        "params": {"period": 20, "std": 2.0},
        "param_ranges": {"period": [10, 30], "std": [1.5, 3.0]},
    },
    {
        "name": "BBANDS_REVERT",
        "family": "volatility",
        "indicator_code": (
            'dataframe["bb_upper"], dataframe["bb_middle"], dataframe["bb_lower"] = '
            "ta.BBANDS(dataframe, timeperiod={period}, nbdevup={std}, nbdevdn={std}, matype=0)"
        ),
        "signal_long": '(dataframe["close"] < dataframe["bb_lower"])',
        "signal_short": '(dataframe["close"] > dataframe["bb_upper"])',
        "params": {"period": 20, "std": 2.0},
        "param_ranges": {"period": [10, 30], "std": [1.5, 3.0]},
    },
    {
        "name": "ATR_EXPANSION",
        "family": "volatility",
        "indicator_code": (
            'dataframe["atr"] = ta.ATR(dataframe, timeperiod={period})\n'
            '        dataframe["atr_ma"] = dataframe["atr"].rolling(window={ma_window}).mean()'
        ),
        "signal_long": '(dataframe["atr"] > dataframe["atr_ma"] * {multiplier})',
        "signal_short": '(dataframe["atr"] > dataframe["atr_ma"] * {multiplier})',
        "params": {"period": 14, "ma_window": 50, "multiplier": 1.0},
        "param_ranges": {"period": [7, 21], "ma_window": [20, 100], "multiplier": [0.8, 2.0]},
    },
    {
        "name": "NATR",
        "family": "volatility",
        "indicator_code": 'dataframe["natr"] = ta.NATR(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["natr"] > {threshold})',
        "signal_short": '(dataframe["natr"] > {threshold})',
        "params": {"period": 14, "threshold": 2.0},
        "param_ranges": {"period": [7, 21], "threshold": [1.0, 5.0]},
    },
    {
        "name": "BB_WIDTH",
        "family": "volatility",
        "indicator_code": (
            'dataframe["bb_upper"], dataframe["bb_middle"], dataframe["bb_lower"] = '
            "ta.BBANDS(dataframe, timeperiod={period}, nbdevup=2.0, nbdevdn=2.0, matype=0)\n"
            '        dataframe["bb_width"] = '
            '(dataframe["bb_upper"] - dataframe["bb_lower"]) / dataframe["bb_middle"]'
        ),
        "signal_long": '(dataframe["bb_width"] > {min_width})',
        "signal_short": '(dataframe["bb_width"] > {min_width})',
        "params": {"period": 20, "min_width": 0.04},
        "param_ranges": {"period": [10, 30], "min_width": [0.02, 0.10]},
    },

    # ── Volume ─────────────────────────────────────────────────
    {
        "name": "OBV_TREND",
        "family": "volume",
        "indicator_code": (
            'dataframe["obv"] = ta.OBV(dataframe)\n'
            '        dataframe["obv_ma"] = dataframe["obv"].rolling(window={period}).mean()'
        ),
        "signal_long": '(dataframe["obv"] > dataframe["obv_ma"])',
        "signal_short": '(dataframe["obv"] < dataframe["obv_ma"])',
        "params": {"period": 20},
        "param_ranges": {"period": [10, 50]},
    },
    {
        "name": "AD_TREND",
        "family": "volume",
        "indicator_code": (
            'dataframe["ad"] = ta.AD(dataframe)\n'
            '        dataframe["ad_ma"] = dataframe["ad"].rolling(window={period}).mean()'
        ),
        "signal_long": '(dataframe["ad"] > dataframe["ad_ma"])',
        "signal_short": '(dataframe["ad"] < dataframe["ad_ma"])',
        "params": {"period": 20},
        "param_ranges": {"period": [10, 50]},
    },
    {
        "name": "ADOSC",
        "family": "volume",
        "indicator_code": (
            'dataframe["adosc"] = ta.ADOSC(dataframe, fastperiod={fast}, slowperiod={slow})'
        ),
        "signal_long": '(dataframe["adosc"] > 0)',
        "signal_short": '(dataframe["adosc"] < 0)',
        "params": {"fast": 3, "slow": 10},
        "param_ranges": {"fast": [2, 5], "slow": [7, 20]},
    },

    # ── Overlap / Moving Average ───────────────────────────────
    {
        "name": "EMA_CROSS",
        "family": "overlap",
        "indicator_code": (
            'dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod={fast})\n'
            '        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod={slow})'
        ),
        "signal_long": '(dataframe["ema_fast"] > dataframe["ema_slow"])',
        "signal_short": '(dataframe["ema_fast"] < dataframe["ema_slow"])',
        "params": {"fast": 9, "slow": 21},
        "param_ranges": {"fast": [5, 15], "slow": [15, 50]},
    },
    {
        "name": "SMA_CROSS",
        "family": "overlap",
        "indicator_code": (
            'dataframe["sma_fast"] = ta.SMA(dataframe, timeperiod={fast})\n'
            '        dataframe["sma_slow"] = ta.SMA(dataframe, timeperiod={slow})'
        ),
        "signal_long": '(dataframe["sma_fast"] > dataframe["sma_slow"])',
        "signal_short": '(dataframe["sma_fast"] < dataframe["sma_slow"])',
        "params": {"fast": 10, "slow": 30},
        "param_ranges": {"fast": [5, 15], "slow": [20, 50]},
    },
    {
        "name": "DEMA_CROSS",
        "family": "overlap",
        "indicator_code": (
            'dataframe["dema_fast"] = ta.DEMA(dataframe, timeperiod={fast})\n'
            '        dataframe["dema_slow"] = ta.DEMA(dataframe, timeperiod={slow})'
        ),
        "signal_long": '(dataframe["dema_fast"] > dataframe["dema_slow"])',
        "signal_short": '(dataframe["dema_fast"] < dataframe["dema_slow"])',
        "params": {"fast": 9, "slow": 21},
        "param_ranges": {"fast": [5, 15], "slow": [15, 50]},
    },
    {
        "name": "TEMA_CROSS",
        "family": "overlap",
        "indicator_code": (
            'dataframe["tema_fast"] = ta.TEMA(dataframe, timeperiod={fast})\n'
            '        dataframe["tema_slow"] = ta.TEMA(dataframe, timeperiod={slow})'
        ),
        "signal_long": '(dataframe["tema_fast"] > dataframe["tema_slow"])',
        "signal_short": '(dataframe["tema_fast"] < dataframe["tema_slow"])',
        "params": {"fast": 9, "slow": 21},
        "param_ranges": {"fast": [5, 15], "slow": [15, 50]},
    },
    {
        "name": "KAMA",
        "family": "overlap",
        "indicator_code": 'dataframe["kama"] = ta.KAMA(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["close"] > dataframe["kama"])',
        "signal_short": '(dataframe["close"] < dataframe["kama"])',
        "params": {"period": 30},
        "param_ranges": {"period": [10, 50]},
    },
    {
        "name": "T3",
        "family": "overlap",
        "indicator_code": 'dataframe["t3"] = ta.T3(dataframe, timeperiod={period}, vfactor={vfactor})',
        "signal_long": '(dataframe["close"] > dataframe["t3"])',
        "signal_short": '(dataframe["close"] < dataframe["t3"])',
        "params": {"period": 5, "vfactor": 0.7},
        "param_ranges": {"period": [3, 15], "vfactor": [0.5, 0.9]},
    },
    {
        "name": "WMA_CROSS",
        "family": "overlap",
        "indicator_code": (
            'dataframe["wma_fast"] = ta.WMA(dataframe, timeperiod={fast})\n'
            '        dataframe["wma_slow"] = ta.WMA(dataframe, timeperiod={slow})'
        ),
        "signal_long": '(dataframe["wma_fast"] > dataframe["wma_slow"])',
        "signal_short": '(dataframe["wma_fast"] < dataframe["wma_slow"])',
        "params": {"fast": 9, "slow": 21},
        "param_ranges": {"fast": [5, 15], "slow": [15, 50]},
    },
    {
        "name": "PRICE_VS_EMA",
        "family": "overlap",
        "indicator_code": 'dataframe["ema"] = ta.EMA(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["close"] > dataframe["ema"])',
        "signal_short": '(dataframe["close"] < dataframe["ema"])',
        "params": {"period": 20},
        "param_ranges": {"period": [10, 50]},
    },
    {
        "name": "TRIMA",
        "family": "overlap",
        "indicator_code": 'dataframe["trima"] = ta.TRIMA(dataframe, timeperiod={period})',
        "signal_long": '(dataframe["close"] > dataframe["trima"])',
        "signal_short": '(dataframe["close"] < dataframe["trima"])',
        "params": {"period": 20},
        "param_ranges": {"period": [10, 50]},
    },
]


# ═══════════════════════════════════════════════════════════════
# Library Class
# ═══════════════════════════════════════════════════════════════


class FactorTemplateLibrary:
    """
    Curated library of talib indicator templates for strategy assembly.

    Usage::

        lib = FactorTemplateLibrary()
        factors = lib.sample(n=3, seed=42)
        entry_code = lib.assemble_entry_code(factors)
    """

    def __init__(self):
        self._templates: list[dict] = _TEMPLATES

    def get_all(self) -> list[dict]:
        """Return all factor templates."""
        return list(self._templates)

    def get(self, name: str) -> Optional[dict]:
        """Get a factor template by name. Returns None if not found."""
        for t in self._templates:
            if t["name"] == name:
                return dict(t)  # defensive copy
        return None

    def sample(
        self,
        n: int,
        seed: Optional[int] = None,
        families: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Randomly sample n factor templates.

        Args:
            n: Number of factors to sample.
            seed: Random seed for reproducibility.
            families: Optional filter by family names.

        Returns:
            List of factor template dicts (defensive copies).
        """
        pool = self._templates
        if families:
            pool = [t for t in pool if t["family"] in families]

        rng = random.Random(seed)
        k = min(n, len(pool))
        selected = rng.sample(pool, k)
        return [dict(t) for t in selected]

    def list_names(self, families: Optional[list[str]] = None) -> list[str]:
        """Return sorted list of factor names, optionally filtered by family."""
        pool = self._templates
        if families:
            pool = [t for t in pool if t["family"] in families]
        return sorted(t["name"] for t in pool)

    def render_indicator(self, factor: dict) -> str:
        """
        Render the indicator_code snippet with parameter values substituted.

        Args:
            factor: A factor template dict with ``params`` filled.

        Returns:
            Python code string ready for insertion.
        """
        code = factor["indicator_code"]
        for key, val in factor["params"].items():
            code = code.replace(f"{{{key}}}", str(val))
        return code

    def render_signal(self, factor: dict, direction: str = "long") -> str:
        """
        Render a signal condition with parameter values substituted.

        Args:
            factor: A factor template dict.
            direction: "long" or "short".

        Returns:
            Python boolean expression string.
        """
        template_key = f"signal_{direction}"
        code = factor[template_key]
        for key, val in factor["params"].items():
            code = code.replace(f"{{{key}}}", str(val))
        return code

    def assemble_entry_code(self, factors: list[dict]) -> str:
        """
        Assemble populate_entry_trend code from selected factors.

        Generates AND-combined conditions for long and short entry.

        Args:
            factors: List of factor template dicts.

        Returns:
            Python code string for the entry logic.
        """
        if not factors:
            return ""

        # Render indicator computation
        indicator_lines = []
        for f in factors:
            rendered = self.render_indicator(f)
            for line in rendered.split("\n"):
                stripped = line.strip()
                if stripped:
                    indicator_lines.append(stripped)

        # Render conditions
        long_conditions = []
        short_conditions = []
        for f in factors:
            long_conditions.append(self.render_signal(f, "long"))
            short_conditions.append(self.render_signal(f, "short"))

        long_combined = "\n    & ".join(long_conditions)
        short_combined = "\n    & ".join(short_conditions)

        ind_block = "\n".join(indicator_lines)

        code = f"""\
# ---- Indicators ----
{ind_block}

# ---- Long Entry ----
long_conditions = (
    {long_combined}
    & (dataframe["volume"] > 0)
)
dataframe.loc[long_conditions, "enter_long"] = 1
dataframe.loc[long_conditions, "enter_tag"] = "factor_combo_long"

# ---- Short Entry ----
short_conditions = (
    {short_combined}
    & (dataframe["volume"] > 0)
)
dataframe.loc[short_conditions, "enter_short"] = 1
dataframe.loc[short_conditions, "enter_tag"] = "factor_combo_short"
"""
        return code

    def get_catalog_text(self) -> str:
        """
        Generate a human-readable factor catalog for inclusion in LLM prompts.

        Returns a formatted string listing all available factors by family.
        """
        by_family: dict[str, list[dict]] = {}
        for t in self._templates:
            by_family.setdefault(t["family"], []).append(t)

        lines = ["## 可用因子目录（共 {} 个）".format(len(self._templates))]
        for family in sorted(by_family.keys()):
            lines.append(f"\n### {family.upper()}")
            for t in sorted(by_family[family], key=lambda x: x["name"]):
                params_str = ", ".join(
                    f"{k}={v}" for k, v in t["params"].items()
                )
                ranges_str = ", ".join(
                    f"{k}:{v}" for k, v in t["param_ranges"].items()
                )
                lines.append(
                    f"- **{t['name']}**: {params_str}"
                    f"  (可调范围: {ranges_str})" if ranges_str else
                    f"- **{t['name']}**: 无参数"
                )
        return "\n".join(lines)
