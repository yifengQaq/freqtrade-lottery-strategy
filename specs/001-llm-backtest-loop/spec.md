# Feature Specification: LLM Agent + 回测闭环自动迭代系统

**Feature Branch**: `001-llm-backtest-loop`  
**Created**: 2026-02-28  
**Status**: Draft  
**Input**: User description: "使用 DeepSeek LLM API 基于 OP 彩票心态合约策略，通过 Agent 自动修改策略代码 → 跑 backtest/hyperopt → 读取结果 → 再迭代，形成代码生成 + 实验调度 + 结果筛选的闭环，动态调整参数以尽可能提高收益率"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — 单轮 Agent 迭代（代码生成 + 回测 + 评估） (Priority: P1)

用户启动系统后，Agent 读取当前策略代码和回测结果，调用 DeepSeek API 分析弱点并提出 1-2 个参数/逻辑修改，自动将修改写入策略文件，执行 freqtrade backtesting，解析回测结果并按 OP 策略评分公式打分，输出本轮变更摘要和得分。

**Why this priority**: 这是整个系统的核心原子操作——没有单轮迭代能力，闭环无从谈起。实现这一个 story 就可以手动触发单次优化。

**Independent Test**: 给定一个初始策略文件 + 模拟回测结果 JSON，系统能完成一轮完整的"分析→修改→回测→评分"流程，输出结构化 JSON 结果。

**Acceptance Scenarios**:

1. **Given** 初始策略文件和 freqtrade 环境已配置, **When** 用户执行 `python run_agent.py --rounds 1`, **Then** 系统调用 DeepSeek API 提出修改、写入策略文件、运行回测、输出包含 `score`, `changes_made`, `rationale` 的 JSON 结果
2. **Given** DeepSeek 返回不合法代码（语法错误）, **When** 系统尝试应用修改, **Then** 拒绝写入、保留原策略、报告语法错误详情
3. **Given** DeepSeek 返回杠杆 > 20x 的修改, **When** 系统校验安全规则, **Then** 拒绝修改并提示违反安全限制

---

### User Story 2 — 多轮自动迭代闭环 (Priority: P2)

用户设置最大迭代轮次（默认 20），系统自动连续执行多轮迭代，每轮读取上一轮结果决定下一步修改方向。当满足终止条件（连续 3 轮无提升 / 达到上限 / 过拟合检测触发）时自动停止。

**Why this priority**: 在 P1 单轮能力上构建自动化循环，是"闭环"的核心价值——用户只需一次启动，无需逐轮干预。

**Independent Test**: 设置 `--rounds 5`，系统自动完成 5 轮迭代（或提前终止），每轮结果被保存且得分趋势可追溯。

**Acceptance Scenarios**:

1. **Given** 迭代上限为 5 轮, **When** 5 轮中第 3/4/5 轮 score 均无提升, **Then** 系统在第 5 轮后触发"连续 3 轮无提升"终止条件并停止
2. **Given** 第 N 轮 OOS score < IS score 的 60%, **When** 系统执行过拟合检测, **Then** 回退至第 N-1 轮版本并标记过拟合
3. **Given** 20 轮迭代全部完成, **When** 系统达到最大轮次, **Then** 强制停止并输出全局最优版本

---

### User Story 3 — Walk-Forward 验证与防过拟合 (Priority: P3)

系统支持对策略执行 walk-forward 验证：将数据分为 in-sample（12 周）和 out-of-sample（4 周）两段，分别回测并比较得分。OOS/IS 比值低于 0.6 时判定为过拟合，自动回退。

**Why this priority**: 防止 Agent 过度拟合历史数据产生"幻觉收益"，这是策略可信度的关键保障。

**Independent Test**: 给定一个已优化策略，在 IS 和 OOS 两段时间范围分别回测，系统能自动计算 OOS/IS 比值并输出判定结论。

**Acceptance Scenarios**:

1. **Given** IS 得分 80, OOS 得分 55, **When** 系统计算 OOS/IS = 0.69, **Then** 判定通过（>= 0.6）
2. **Given** IS 得分 80, OOS 得分 40, **When** 系统计算 OOS/IS = 0.50, **Then** 判定过拟合，回退上一版本

---

### User Story 4 — 策略版本管理与回滚 (Priority: P4)

每轮迭代的策略代码自动备份并编号。用户可以查看所有版本、对比得分、手动回滚到任意版本。

**Why this priority**: 当 Agent 迭代走偏时，用户需要快速恢复到已知良好版本。

**Independent Test**: 执行 3 轮迭代后，用户运行 `python run_agent.py --list-versions`，能看到 3 个版本及各自得分；运行 `--rollback 1` 能恢复到第 1 轮版本。

**Acceptance Scenarios**:

1. **Given** 已完成 3 轮迭代, **When** 用户请求列出版本, **Then** 输出每轮的 round / timestamp / score / changes_made
2. **Given** 当前为第 3 轮版本, **When** 用户执行 `--rollback 1`, **Then** 策略文件恢复为第 1 轮版本

---

### Edge Cases

- DeepSeek API 超时或返回空响应 → 重试 3 次后跳过本轮，保留上一轮结果
- Freqtrade backtesting 运行超过 10 分钟 → 超时终止，标记本轮无效
- DeepSeek 返回的代码删除了 `WeeklyBudgetController` 集成 → 安全检查拒绝，不写入
- 回测结果中 `stake_limit_hit_count > 0` → 该轮结果标记为无效
- 所有交易对数据缺失 → 报错退出，提示用户下载数据

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 调用 DeepSeek Chat API (OpenAI-compatible) 发送策略代码 + 回测结果，接收结构化 JSON 修改建议
- **FR-002**: 系统 MUST 在写入策略文件前验证 Python 语法和安全规则（杠杆 ≤ 20x，止损 ≥ -0.98，WeeklyBudgetController 完整）
- **FR-003**: 系统 MUST 调用 `freqtrade backtesting` 子进程并解析结果 JSON
- **FR-004**: 系统 MUST 按评分公式计算综合得分：`Score = monthly_avg_profit * 0.4 + weekly_target_hit_rate * 100 * 0.3 - max_monthly_loss * 0.2 + (1/avg_trade_duration_hours) * 0.1`
- **FR-005**: 系统 MUST 在每轮迭代前备份当前策略文件（带轮次编号和时间戳）
- **FR-006**: 系统 MUST 支持 walk-forward 验证（IS 12w + OOS 4w），OOS/IS < 0.6 判定过拟合
- **FR-007**: 系统 MUST 执行门控检查：周达标率 ≥ 25%、总交易次数 ≥ 50、超限次数 = 0、月均净利润 > 0
- **FR-008**: 系统 MUST 在连续 3 轮 score 无提升或达到 20 轮时自动终止
- **FR-009**: 系统 MUST 限制 Agent 每轮只修改 1-2 个参数或 1 个逻辑块
- **FR-010**: 系统 MUST 输出每轮结构化日志：round / changes_made / rationale / score / metrics / next_action
- **FR-011**: 系统 MUST 支持回滚到任意历史版本
- **FR-012**: Agent 不可修改：周预算 100 USDT、ALL IN 逻辑、不复利原则、WeeklyBudgetController 核心逻辑

### Key Entities

- **IterationRound**: 一轮迭代的完整记录——轮次号、变更描述、理由、回测指标、得分、策略代码快照
- **BacktestResult**: freqtrade 回测输出的标准化指标集（利润率、最大回撤、夏普比、交易次数、周达标率等）
- **StrategyVersion**: 策略文件的版本快照——轮次、时间戳、文件路径、关联得分
- **EvalResult**: 评估结果——通过/失败、得分、门控失败项、过拟合标记、建议动作
- **AgentConfig**: Agent 运行配置——最大轮次、DeepSeek 模型、freqtrade 路径、时间范围等

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 系统能在无人干预下完成至少 5 轮完整的"分析→修改→回测→评分"迭代循环
- **SC-002**: 每轮迭代从 LLM 调用到回测完成在 15 分钟内（不含数据下载）
- **SC-003**: 安全检查能 100% 拦截杠杆 > 20x、止损 < -0.98、删除 WeeklyBudgetController 的修改
- **SC-004**: Walk-forward 验证能正确检测 OOS/IS < 0.6 的过拟合情况并自动回退
- **SC-005**: 经过多轮迭代后，最终策略的综合得分相较于初始版本有可衡量的提升
- **SC-006**: 所有迭代历史可追溯，任意版本可回滚恢复
