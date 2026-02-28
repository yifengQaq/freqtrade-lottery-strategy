# Data Model: LLM Agent + 回测闭环自动迭代系统

**Date**: 2026-02-28  
**Branch**: `001-llm-backtest-loop`

## Entities

### IterationRound

一轮完整迭代的记录。

| Field | Type | Description |
|-------|------|-------------|
| round | int | 轮次编号 (1-based) |
| timestamp | str (ISO 8601) | 迭代开始时间 |
| changes_made | str | 变更摘要 |
| rationale | str | 变更理由 |
| backtest_metrics | BacktestMetrics | 回测结果指标 |
| eval_result | EvalResult | 评估结果 |
| score | float | 综合得分 |
| strategy_version_path | str | 策略备份文件路径 |
| next_action | str | Agent 建议的下一步 |
| status | enum | success / failed / skipped / overfitting |

### BacktestMetrics

从 freqtrade 回测结果中提取的标准化指标。

| Field | Type | Description |
|-------|------|-------------|
| total_profit_pct | float | 总利润百分比 |
| total_profit_abs | float | 总利润绝对值 (USDT) |
| max_drawdown_pct | float | 最大回撤百分比 |
| sharpe_ratio | float | 夏普比率 |
| sortino_ratio | float | 索提诺比率 |
| profit_factor | float | 利润因子 |
| win_rate | float | 胜率 |
| total_trades | int | 总交易次数 |
| avg_profit_per_trade_pct | float | 每笔平均利润% |
| avg_trade_duration_hours | float | 平均持仓时长(小时) |
| stake_limit_hit_count | int | 超限次数 |
| weekly_target_hit_rate | float | 周达标率 |
| total_weeks | int | 覆盖总周数 |
| target_hit_weeks | int | 达标周数 |
| monthly_net_profit_avg | float | 月均净利润 |
| max_monthly_loss | float | 月最大亏损 |

### EvalResult

评估器输出。

| Field | Type | Description |
|-------|------|-------------|
| passed | bool | 是否通过门控 |
| score | float | 综合得分 |
| gate_failures | list[str] | 门控失败项列表 |
| score_breakdown | dict | 得分各分项 |
| is_overfitting | bool | OOS 验证是否判定过拟合 |
| recommendation | str | 评估器建议 |

### AgentConfig

Agent 运行配置。

| Field | Type | Description |
|-------|------|-------------|
| max_rounds | int | 最大迭代轮次 (默认 20) |
| stale_rounds_limit | int | 连续无提升轮次上限 (默认 3) |
| deepseek_model | str | LLM 模型名 |
| deepseek_api_key | str | API Key (环境变量) |
| freqtrade_dir | str | freqtrade 安装目录 |
| strategy_name | str | 策略类名 |
| config_path | str | freqtrade 配置文件路径 |
| timerange_is | str | In-sample 时间范围 |
| timerange_oos | str | Out-of-sample 时间范围 |
| enable_walk_forward | bool | 是否启用防过拟合验证 |

### StrategyVersion

策略版本快照。

| Field | Type | Description |
|-------|------|-------------|
| round | int | 关联轮次 |
| file_path | str | 备份文件绝对路径 |
| timestamp | str (ISO 8601) | 备份时间 |
| score | float | 该版本的评估得分 |
| changes_made | str | 变更摘要 |

### ErrorIncident

一次失败事件与分诊结果。

| Field | Type | Description |
|-------|------|-------------|
| incident_id | str | 错误事件唯一ID |
| round | int | 关联轮次 |
| stage | str | 触发阶段（syntax/backtest/eval） |
| error_type | str | syntax/runtime/config/data |
| traceback | str | 原始错误栈 |
| created_at | str (ISO 8601) | 记录时间 |
| resolved | bool | 是否已恢复 |

### FixAttempt

一次自动修复尝试。

| Field | Type | Description |
|-------|------|-------------|
| incident_id | str | 关联 ErrorIncident |
| attempt_no | int | 第几次修复（1..max） |
| fix_summary | str | 修复内容摘要 |
| patch_applied | bool | 是否成功应用补丁 |
| retry_backtest_ok | bool | 重试回测是否通过 |
| failed_reason | str | 失败原因（可空） |

### FactorCandidate

候选因子及其实验状态。

| Field | Type | Description |
|-------|------|-------------|
| candidate_id | str | 候选唯一ID |
| round | int | 来源轮次 |
| factor_family | str | volatility/trend/momentum/filter |
| params | dict | 参数定义 |
| status | str | active/promoted/quarantined |
| score | float | 最新得分 |

## Relationships

```
AgentConfig ──1:N──▶ IterationRound
IterationRound ──1:1──▶ BacktestMetrics
IterationRound ──1:1──▶ EvalResult
IterationRound ──1:1──▶ StrategyVersion
IterationRound ──1:N──▶ ErrorIncident
ErrorIncident ──1:N──▶ FixAttempt
IterationRound ──1:N──▶ FactorCandidate
```

## State Transitions

```
IterationRound.status:
  
  [start] ──▶ running ──▶ success   (回测正常 + 评估完成)
                      ──▶ failed    (语法错误 / 安全检查失败 / 回测超时)
                      ──▶ skipped   (API 超时后跳过)
                      ──▶ overfitting (OOS/IS < 0.6)
                      ──▶ recovering (进入自动修复链路)
                      ──▶ rolled_back (修复失败后回滚)
```

## Persistence

所有数据以 JSON 文件持久化:

- `results/iteration_log.json`: `list[IterationRound]` 的 JSON 数组
- `results/strategy_versions/round_NNN_*.py`: 策略文件快照
- `results/backtest_outputs/round_NNN_*.json`: 原始回测结果
- `results/error_incidents.jsonl`: ErrorIncident + FixAttempt 记录
- `results/experiments/factor_trials.jsonl`: FactorCandidate 及实验结果账本
