# 因子池随机采样 + 多窗口回测 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Agent 从"固定指标+调参"模式重构为"因子池随机采样+多窗口验证"模式，大幅提升策略探索空间和回测可靠性。

**Architecture:** 
1. 建立因子模板库（~40个 talib 指标模板），每轮随机抽 2-3 个组装成入场逻辑
2. 多时间窗口验证：牛市、熊市、横盘各一段 + 长周期全量，而非固定 3 个月
3. LLM 角色从"在 4 个指标上调参"变为"从因子池选因子+设计组合逻辑+调出入场参数"

**Tech Stack:** Python 3.13, talib, freqtrade, DeepSeek API, pytest

---

## 核心问题分析

### 问题一：因子选择太窄
- **现状**：LLM 在 ADX/BB/RSI/ATR 上来回调参 → 陷入局部最优
- **症状**：close > BB_upper & RSI < 30 逻辑矛盾都识别不出
- **方案**：建因子模板库，每轮 LLM 从 40+ 因子中选 2-3 个组装

### 问题二：回测窗口不全面
- **现状**：IS 只有 2025-9~11（3个月），OOS 只有 12 月
- **症状**：某些月份恰好无行情 → 0 笔交易 → 策略没法优化
- **方案**：6 个窗口覆盖不同市况 + 全量验证

---

## Task 1: 因子模板库（`agent/factor_templates.py`）

**Files:**
- Create: `agent/factor_templates.py`
- Test: `tests/unit/test_factor_templates.py`

### Step 1: 写失败测试

```python
# tests/unit/test_factor_templates.py
import pytest
from agent.factor_templates import FactorTemplateLibrary

class TestFactorLibrary:
    def test_get_all_factors_returns_list(self):
        lib = FactorTemplateLibrary()
        factors = lib.get_all()
        assert isinstance(factors, list)
        assert len(factors) >= 30

    def test_factor_has_required_fields(self):
        lib = FactorTemplateLibrary()
        for f in lib.get_all():
            assert "name" in f
            assert "family" in f       # trend/momentum/volatility/volume/pattern
            assert "indicator_code" in f  # Python code snippet
            assert "signal_long" in f     # condition expression
            assert "signal_short" in f
            assert "params" in f          # dict of tunable params

    def test_sample_returns_n_factors(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=3, seed=42)
        assert len(sample) == 3
        # Should be reproducible
        sample2 = lib.sample(n=3, seed=42)
        assert [s["name"] for s in sample] == [s["name"] for s in sample2]

    def test_sample_with_family_filter(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=2, families=["momentum"], seed=42)
        assert all(s["family"] == "momentum" for s in sample)

    def test_get_by_name(self):
        lib = FactorTemplateLibrary()
        f = lib.get("RSI")
        assert f is not None
        assert f["name"] == "RSI"

    def test_assemble_entry_code(self):
        lib = FactorTemplateLibrary()
        factors = lib.sample(n=2, seed=42)
        code = lib.assemble_entry_code(factors)
        assert "enter_long" in code
        assert "enter_short" in code
        # Should be valid Python syntax
        compile(code, "<string>", "exec")
```

### Step 2: 运行测试确认失败

```bash
pytest tests/unit/test_factor_templates.py -v
# Expected: ModuleNotFoundError
```

### Step 3: 实现因子模板库

创建 `agent/factor_templates.py`，包含 ~40 个因子模板，涵盖：
- **Trend (趋势)**: ADX, AROON, SAR, EMA_cross, SMA_cross, MACD, PLUS_DI/MINUS_DI, HT_TRENDLINE
- **Momentum (动量)**: RSI, STOCHRSI, CCI, MOM, ROC, WILLR, ULTOSC, BOP, CMO, MFI, STOCH
- **Volatility (波动率)**: BBANDS, ATR, NATR, TRANGE, KELTNER (自定义)
- **Volume (量价)**: OBV, AD, ADOSC
- **Overlap (均线系)**: EMA, SMA, DEMA, TEMA, KAMA, T3, WMA

每个因子模板格式：
```python
{
    "name": "RSI",
    "family": "momentum",
    "indicator_code": 'dataframe["rsi"] = ta.RSI(dataframe, timeperiod={period})',
    "signal_long": 'dataframe["rsi"] < {oversold}',
    "signal_short": 'dataframe["rsi"] > {overbought}',
    "params": {"period": 14, "oversold": 30, "overbought": 70},
    "param_ranges": {"period": [7, 21], "oversold": [20, 40], "overbought": [60, 80]},
}
```

### Step 4: 运行测试确认通过

```bash
pytest tests/unit/test_factor_templates.py -v
```

### Step 5: 提交

```bash
git add agent/factor_templates.py tests/unit/test_factor_templates.py
git commit -m "feat: 因子模板库 — 40+ talib 指标模板 + 随机采样"
```

---

## Task 2: 多窗口回测配置

**Files:**
- Modify: `config/agent_config.yaml`
- Test: 手动验证 freqtrade 能在各窗口跑通

### Step 1: 定义 6 个窗口

基于 BTC 行情划分（数据范围 2021-01-01 ~ 2025-12-31）：

| 窗口标签 | 时间范围 | 市况 | 作用 |
|----------|---------|------|------|
| bull_2021 | 20210101-20210501 | 牛市（BTC 30k→60k） | IS 训练 |
| bear_2022 | 20220501-20221001 | 熊市（BTC 40k→20k） | IS 训练 |
| sideways_2023 | 20230601-20231001 | 横盘（BTC 25k~30k） | IS 训练 |
| recovery_2024 | 20240101-20240601 | 恢复/牛（BTC 40k→70k） | IS 训练 |
| recent_2025 | 20250101-20250601 | 最近半年 | OOS 验证 |
| full_2y | 20240101-20251231 | 全量 2 年 | 最终验证 |

### Step 2: 更新 agent_config.yaml

```yaml
agent:
  # 原有 IS/OOS 改为多窗口
  timerange_is: "20210101-20251231"  # 全量（默认，单次快速验证用）
  timerange_oos: "20250701-20251231" # OOS 半年
  
  # 启用多窗口
  enable_multi_backtest: true
  comparison_windows:
    bull_2021: "20210101-20210501"
    bear_2022: "20220501-20221001"
    sideways_2023: "20230601-20231001"
    recovery_2024: "20240101-20240601"
    recent_2025: "20250101-20250601"
  
  # 启用因子实验
  enable_factor_lab: true
  factor_candidates: 5
```

### Step 3: 验证各窗口可回测

```bash
# 逐窗口快速验证
for tr in 20210101-20210501 20220501-20221001 20230601-20231001 20240101-20240601 20250101-20250601; do
  freqtrade_env/bin/freqtrade backtesting \
    --config config/config_backtest.json \
    --userdir user_data --strategy LotteryMindsetStrategy \
    --timerange $tr 2>&1 | tail -1
done
```

### Step 4: 提交

```bash
git add config/agent_config.yaml
git commit -m "config: 多窗口回测 — 牛/熊/横/恢复/近期 5 个 IS 窗口"
```

---

## Task 3: 重构 System Prompt — LLM 变「因子选择者」

**Files:**
- Modify: `agent/prompts/system_prompt.md`

### Step 1: 重写 system prompt

核心变化：
1. LLM 不再自由改代码，而是**选因子+设参数+设计组合逻辑**
2. 输出 JSON 中 `code_patch` 改为 `factor_selection`（因子列表+参数+组合方式）
3. 或者保持 `code_patch` 但给 LLM 因子目录和选择模板

**推荐方案**：保持 `code_patch` 全量代码输出（LLM 仍然写完整策略），但 prompt 里提供因子目录和组合指导，这样改动最小。

### Step 2: 提交

```bash
git add agent/prompts/system_prompt.md
git commit -m "prompt: 重构为因子池探索 + 多维度指导"
```

---

## Task 4: 改造迭代 Prompt — 注入因子目录

**Files:**
- Modify: `agent/deepseek_client.py` → `_build_iteration_prompt()`
- Modify: `agent/orchestrator.py` → `_run_single_round()`
- Test: 已有 mock 测试覆盖

### Step 1: 在迭代 prompt 中注入可用因子目录

`_build_iteration_prompt()` 增加一段"可选因子"列表，告诉 LLM 可以从哪些指标中选。

### Step 2: orchestrator 启用 Comparator 多窗口验证

当 `enable_multi_backtest=true` 时，每轮回测后追加多窗口验证，只有在多窗口上都 OK 才算 success。

### Step 3: 提交

```bash
git add agent/deepseek_client.py agent/orchestrator.py
git commit -m "feat: 迭代 prompt 注入因子目录 + 多窗口验证闭环"
```

---

## Task 5: 更新测试

**Files:**
- Modify: `tests/unit/test_deepseek_client.py`
- Modify: `tests/unit/test_orchestrator.py`
- Modify: `tests/unit/test_strategy_modifier.py`

### Step 1: 更新 mock 测试适配新 prompt 格式
### Step 2: 增加因子库集成测试
### Step 3: 运行全量测试

```bash
pytest tests/ -v
```

### Step 4: 提交

```bash
git commit -am "test: 适配因子池+多窗口重构"
```

---

## Task 6: 端到端验证

### Step 1: 跑 3 轮验证

```bash
python scripts/run_agent.py --rounds 3 -v
```

验证目标：
- [ ] LLM 每轮选择不同的指标组合而非只调 ADX/BB/RSI
- [ ] 每轮输出中 changes_made 包含新因子名
- [ ] 多窗口回测全部执行（日志中有 5 个窗口的结果）
- [ ] 0 笔交易情况显著减少

### Step 2: 跑 10 轮看收敛

```bash
python scripts/run_agent.py --rounds 10
```

### Step 3: 提交最终状态

```bash
git commit -am "验证: 因子池+多窗口 10轮迭代通过"
```

---

## 风险与回退

| 风险 | 概率 | 缓解 |
|------|------|------|
| LLM 仍然倾向选同一组因子 | 中 | prompt 明确要求每轮至少换 1 个因子；在 prompt 中注入"已尝试/已淘汰"列表 |
| 多窗口回测太慢（5窗口 × 15对 × 15m） | 中 | 默认用 1h timeframe，只在 final 验证用 15m；或减少币对 |
| 新因子组合产生更多回测错误 | 高 | auto_repair 已启用（3次修复机会），加上 FactorLab 记录失败因子 |
| LLM 输出的因子代码有 import 错误 | 中 | strategy_modifier 做语法检查；因子模板库限定只能用 talib |
