# 多维度优化 Prompt 重设计

**日期**: 2026-03-01  
**状态**: 已确认，待实施  
**影响范围**: `agent/prompts/system_prompt.md`, `agent/deepseek_client.py`, 新增 `agent/dimension_templates.py`

## 问题

当前 system_prompt.md 和 `_build_iteration_prompt` 90%+ 内容围绕因子/指标选择，导致 LLM 在 46 轮迭代中：
- 85% 的修改仅改入场指标参数
- 78% 是换指标组合
- 仅 4% 探索了 trailing stop
- 0% 探索交易方向
- 0% 探索时间框架切换

## 方案: 诊断驱动(C) + 分层架构(B)

### 两层分离

1. **system_prompt.md（稳定核心层，~120行）**
   - 角色定义、策略铁律、6维度概览、评分体系、talib语法、可用数据、输出格式、滚仓数学
   - **不再包含**因子目录和组合示例（按需注入）

2. **_build_iteration_prompt（动态任务层）**
   - 当前代码 + 回测结果 + 历史变更 + 诊断结论 + 本轮指定维度 + 该维度专属模板

### 维度诊断引擎 (`_select_focus_dimension`)

| 指标条件 | 诊断结论 | 指定维度 | 紧迫度 |
|---------|---------|---------|--------|
| total_trades == 0 | 无交易，入场矛盾 | 入场信号 | 100 |
| total_trades < 50 | 交易太少 | 信号逻辑结构 | 90 |
| max_drawdown > 60% | 回撤过大 | 风控参数 | 85 |
| win_rate < 30% | 胜率极低 | 入场信号 | 80 |
| profit_factor < 1.0 | 盈亏比差 | 出场策略 | 75 |
| avg_profit < 0 且 win_rate > 40% | 赢多输大 | 出场策略 | 70 |
| sharpe < 0 | 负夏普 | 风控参数 | 65 |
| 其他指标尚可 | 基础OK | 最少探索维度 | 50 |

**防循环**: 连续3轮某维度无提升 → 紧迫度×0.3，被降权。

**冷启动**: Epoch前3轮强制: 入场→出场→风控。

### 6个优化维度

1. **入场信号**: 因子选择、参数范围、因子家族覆盖
2. **出场策略**: ROI梯度(3套模板)、trailing stop配置、stoploss值
3. **风控参数**: 杠杆倍数、stoploss宽度、安全组合表
4. **交易方向**: 只做多/只做空/双向、方向选择指导
5. **时间框架**: 15m vs 1h 的适用场景和切换时机
6. **信号逻辑结构**: AND/OR组合、多组条件、分级过滤器

### 输出格式更新

新增必填字段:
- `focus_dimension`: 本轮聚焦维度名称
- `dimension_changes`: 该维度的具体修改项列表

## 实施计划

1. 新建 `agent/dimension_templates.py` — 6个维度模板 + 诊断引擎
2. 重写 `agent/prompts/system_prompt.md` — 稳定核心层
3. 重写 `agent/deepseek_client.py` 的 `_build_iteration_prompt` 和 `generate_targeted_adjustment`
4. 更新测试
