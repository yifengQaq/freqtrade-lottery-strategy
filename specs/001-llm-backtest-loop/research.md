# Research: LLM Agent + 回测闭环自动迭代系统

**Date**: 2026-02-28  
**Branch**: `001-llm-backtest-loop`

## 1. DeepSeek API Integration

**Decision**: 使用 DeepSeek Chat API（OpenAI-compatible 接口）  
**Rationale**: 
- DeepSeek 提供 OpenAI 兼容的 `/v1/chat/completions` 端点
- 支持 `response_format: {"type": "json_object"}` 强制 JSON 输出
- `deepseek-chat` 模型性价比高，适合代码生成场景
- 支持最大 64K context window，足以包含策略代码 + 回测结果  

**Alternatives Considered**:
- OpenAI GPT-4: 更贵，API 格式相同，可作为后备
- 本地 LLM (Ollama): 质量不足以可靠生成策略代码
- Claude API: 质量好但缺乏原生 JSON mode

**Implementation**: 使用 `httpx` 直接调用 REST API，不依赖 `openai` SDK 减少依赖

## 2. Freqtrade Backtest Execution

**Decision**: 通过 `subprocess` 调用 `freqtrade backtesting` CLI  
**Rationale**:
- freqtrade 作为独立进程运行最稳定，避免 Python 导入冲突
- 结果输出为标准 JSON 文件，易于解析
- 支持 `--export trades` 导出详细交易数据  

**Alternatives Considered**:
- 直接 import freqtrade 库: 版本依赖复杂，Python 环境冲突风险
- Docker 容器运行: 增加复杂度，本地开发不便

**Key Parameters**:
- `--timerange`: 控制 IS/OOS 分段
- `--export trades`: 导出交易明细计算周达标率
- `--config`: 指向项目内 config 文件

## 3. 评分公式与门控

**Decision**: 采用 Input/v1_agent_iteration_rules.yaml 中的公式和标准  
**Rationale**: 这是 OP 策略专属评分，侧重周达标率和月均利润

**Score Formula**:
```
Score = monthly_avg_profit * 0.4
      + weekly_target_hit_rate * 100 * 0.3  
      - max_monthly_loss * 0.2
      + (1 / avg_trade_duration_hours) * 0.1
```

**门控标准**:
| 指标 | 阈值 | 理由 |
|------|------|------|
| weekly_target_hit_rate | >= 25% | 每月至少 1 周达标 |
| total_trades | >= 50 | 统计显著性 |
| stake_limit_hit_count | == 0 | 不允许超限 |
| monthly_net_profit_avg | > 0 | 必须整体盈利 |
| max_drawdown_pct | <= 95% | 单周最多亏完预算 |

## 4. 安全约束

**Decision**: 在写入策略前做三层检查  
**Rationale**: 防止 LLM "幻觉" 导致危险参数

1. **语法检查**: `ast.parse()` 验证 Python 语法
2. **安全规则**:
   - 杠杆 <= 20x
   - 止损 >= -0.98
   - WeeklyBudgetController 引用必须存在
   - 不允许复利模式
3. **原子写入**: 先写 .tmp 文件，通过后 rename，失败自动恢复

## 5. Walk-Forward 验证

**Decision**: IS 12 周 + OOS 4 周滚动验证  
**Rationale**: Input/v1_agent_iteration_rules.yaml 指定的标准

**执行方式**: 
- 相同策略在两个 `--timerange` 分别回测
- 各自评分后计算 OOS/IS ratio
- ratio < 0.6 → 过拟合

## 6. 策略版本管理

**Decision**: 文件系统备份 + JSON 日志  
**Rationale**: YAGNI — 不需要 git 子分支或数据库，文件级版本已够用

- 每轮: `results/strategy_versions/round_NNN_YYYYMMDD_HHMMSS.py`
- 全局日志: `results/iteration_log.json`（包含每轮的 round/score/changes/metrics）
