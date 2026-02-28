# Feature Specification: LLM Agent + 回测闭环自动迭代系统

**Feature Branch**: `001-llm-backtest-loop`  
**Created**: 2026-02-28  
**Status**: Draft  
**Input**: User description: "使用 DeepSeek LLM API 基于周内滚仓复利合约策略（每周 100 USDT 起始，ALL IN 滚仓复利到 1000 USDT），通过 Agent 自动修改策略代码 → 跑 backtest/hyperopt → 读取结果 → 再迭代，形成代码生成 + 实验调度 + 结果筛选的闭环，动态调整参数以尽可能提高收益率"

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

### User Story 5 — 报错自动纠错 + 因子生成迭代 (Priority: P2)

当回测因策略语法错误、指标列缺失、配置不兼容、运行时异常导致失败时，系统不应直接跳过该轮，而应进入“故障分诊→自动修复→回测重试”闭环；同时支持在可控范围内自动生成候选因子/过滤器并进行小步实验，像 RD-Agent 一样通过失败反馈驱动下一轮改进。

**Why this priority**: 如果没有自动纠错，迭代会被频繁中断；而“因子候选池 + 实验筛选”是提高搜索效率和收益上限的关键能力。

**Independent Test**: 构造 3 类失败样本（语法错误/指标字段错误/配置错误），系统可在限定重试次数内自动修复并恢复回测；构造 5 个候选因子，系统能完成生成、评估、淘汰、保留流程。

**Acceptance Scenarios**:

1. **Given** DeepSeek 生成的策略代码存在语法错误, **When** 回测前语法校验失败, **Then** 系统自动触发修复 Prompt 并在最多 3 次内修复后重试回测
2. **Given** 回测报错 `KeyError: atr_14` 或类似指标缺失, **When** 系统解析 traceback, **Then** 自动生成补丁添加指标列或修正引用并重试
3. **Given** 某轮连续修复失败超过阈值, **When** 系统判定该变更不可恢复, **Then** 自动回滚到上一稳定版本并记录错误事件
4. **Given** 启用因子生成模式, **When** 系统进入新一轮搜索, **Then** 生成候选因子组合并仅允许 1-2 处小改动进行回测筛选

---

### User Story 6 — 多回测与 Dry Run 对比驱动的动态构建 (Priority: P1)

系统对同一轮候选策略执行“多实验评测”（多时间段、多市场状态、多配置），并与 Dry Run 结果进行偏差对比。LLM 不是只看单次回测，而是读取对比矩阵与目标差距（Story target gap），动态选择下一轮参数方向，逐步逼近 Story 收益目标。

**Why this priority**: 这是“动态构建”的核心。没有多实验与实盘近似对比，LLM 调参很容易过拟合或偏离真实交易表现。

**Independent Test**: 同一候选策略在 3 个回测窗口 + 1 个 Dry Run 窗口下产出对比报告，系统根据目标差距自动给出下一轮参数调整建议并执行。

**Acceptance Scenarios**:

1. **Given** 一个候选策略, **When** 系统执行多回测（bull/bear/sideways）+ Dry Run 对比, **Then** 输出统一对比矩阵（收益、回撤、交易数、偏差）
2. **Given** Story 目标为“周达标率>=25%、月均净利润>0”, **When** 当前结果与目标有差距, **Then** 系统生成目标差距向量并驱动 LLM 仅调整最相关参数
3. **Given** Dry Run 与回测偏差持续超阈值（如成交价偏差>0.5%）, **When** 系统检测到偏差漂移, **Then** 降低该候选优先级或禁止晋级

---

### User Story 7 — 周结算与未达标周处理策略 (Priority: P1)

系统需要明确处理"本周既没有赚到 1000，也没有亏完 100"的情况，避免运行策略时逻辑悬空。默认策略为：到周末强制结算当前持仓，记录本周结果，下一周重置预算为 100 USDT 重新开始。周内采用 ALL IN 滚仓复利（盈利后本金+利润全额再开仓），但跨周不复利。

**Why this priority**: 策略核心是"周内滚仓复利、跨周重置"——周内 ALL IN 滚到目标，周末无论盈亏都结算重置。若未达标周没有明确规则，系统会悄悄变成跨周复利或无限拖延策略。

**Independent Test**: 构造 3 类周末状态（达标/亏完/未达标未亏完），系统均能产出一致且可追溯的周结算动作。

**Acceptance Scenarios**:

1. **Given** 本周滚仓至 400 USDT（未达 1000）, **When** 到达周结算时间, **Then** 系统强制平仓并结束本周，下一周以 100 USDT 新预算重启（周内利润不带入下周）
2. **Given** 本周收益 -100（亏完预算）, **When** 当周内触发亏损阈值, **Then** 立即停机到下个周期
3. **Given** 连续 3 周未达标且净值为负, **When** 系统触发稳健保护, **Then** 下一周进入冷却模式（仅 Dry Run / 不实盘）

---

### Edge Cases

- DeepSeek API 超时或返回空响应 → 重试 3 次后进入降级模式（保持上一版本，仅记录失败），不中断全局循环
- Freqtrade backtesting 运行超过 10 分钟 → 超时终止，标记本轮无效
- DeepSeek 返回的代码删除了 `WeeklyBudgetController` 集成 → 安全检查拒绝，不写入
- 回测结果中 `stake_limit_hit_count > 0` → 该轮结果标记为无效
- 所有交易对数据缺失 → 报错退出，提示用户下载数据
- 修复链路连续失败（例如 3 次）→ 自动回滚上一稳定版本，标记该策略候选为 quarantined
- 多回测表现优秀但 Dry Run 偏差异常 → 不允许直接晋级，需进入校准轮次
- 周末未达标未亏完 → 强制结算并重置预算，不允许跨周复利（周内滚仓复利是正常行为）

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
- **FR-012**: Agent 不可修改：周预算 100 USDT 起始、stake_amount = "unlimited"（ALL IN 滚仓）、custom_stake_amount 方法、WeeklyBudgetController 核心逻辑、max_open_trades = 1
- **FR-013**: 系统 MUST 对失败进行错误分诊（syntax/runtime/config/data），并将 traceback/错误日志结构化存档
- **FR-014**: 系统 MUST 在失败后自动进入修复循环（生成修复补丁→校验→回测重试），每轮最多重试 3 次
- **FR-015**: 系统 MUST 使用“错误上下文 Prompt”驱动 LLM 纠错，输入至少包含：失败代码片段、报错栈、上轮变更摘要
- **FR-016**: 系统 MUST 在修复失败达到阈值后自动回滚至上一稳定版本，并将失败候选标记为 quarantined
- **FR-017**: 系统 MUST 支持候选因子生成与实验（如波动率过滤、趋势过滤、动量过滤），并限制每次只引入小步变化
- **FR-018**: 系统 MUST 维护实验账本（candidate_id、变更点、错误类型、得分、是否晋级），用于后续轮次决策
- **FR-019**: 系统 MUST 支持同一候选策略的多回测实验批次（至少覆盖 bull/bear/sideways 三类窗口）
- **FR-020**: 系统 MUST 接入 Dry Run 结果并与回测结果进行统一对比，输出偏差指标（价格偏差、信号频率偏差、PnL 偏差）
- **FR-021**: 系统 MUST 计算 Story Target Gap（目标与当前结果差距向量），并将其作为 LLM 下一轮调参输入
- **FR-022**: 系统 MUST 提供候选策略排名机制，综合考虑回测分数、稳健性、Dry Run 偏差与过拟合风险
- **FR-023**: 系统 MUST 在目标接近时切换到“微调模式”（仅允许更小参数步长）以减少震荡
- **FR-024**: 系统 MUST 将“多回测对比 + Dry Run 对比 + 目标差距”记录为可追溯报告，供每轮迭代复用
- **FR-025**: 系统 MUST 实现周结算状态机（TARGET_HIT / BUDGET_EXHAUSTED / WEEK_END_SETTLED）并在周末强制落地一个状态
- **FR-026**: 当周末处于"未达标未亏完"时，系统 MUST 强制平仓并重置到下一周预算（周内滚仓复利是正常行为，但跨周不复利）
- **FR-027**: 系统 MUST 支持冷却机制：连续 N 周未达标且净值恶化时，自动切换为 Dry Run 观察周期
- **FR-028**: 系统 MUST 输出周级别结算报告（周收益、状态、是否冷却、下周动作）并纳入迭代输入

### Key Entities

- **IterationRound**: 一轮迭代的完整记录——轮次号、变更描述、理由、回测指标、得分、策略代码快照
- **BacktestResult**: freqtrade 回测输出的标准化指标集（利润率、最大回撤、夏普比、交易次数、周达标率等）
- **StrategyVersion**: 策略文件的版本快照——轮次、时间戳、文件路径、关联得分
- **EvalResult**: 评估结果——通过/失败、得分、门控失败项、过拟合标记、建议动作
- **AgentConfig**: Agent 运行配置——最大轮次、DeepSeek 模型、freqtrade 路径、时间范围等
- **ErrorIncident**: 一次失败事件——错误分类、traceback、触发阶段、关联轮次
- **FixAttempt**: 一次自动修复尝试——修复补丁、校验结果、重试序号、是否成功
- **FactorCandidate**: 候选因子——因子类型、参数、来源轮次、实验状态（active/quarantined/promoted）
- **ExperimentTrial**: 一次候选实验——candidate_id、实验配置、回测指标、得分、晋级结论
- **ComparisonMatrix**: 多实验对比矩阵——不同窗口下的收益/回撤/交易数/稳健性指标
- **DryRunSnapshot**: Dry Run 统计快照——成交价偏差、信号偏差、PnL 偏差
- **TargetGapVector**: Story 目标差距向量——目标指标与当前指标的差值集合
- **WeeklySettlementReport**: 周结算报告——周状态、周收益、是否达标、是否触发冷却、下周执行动作

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 系统能在无人干预下完成至少 5 轮完整的"分析→修改→回测→评分"迭代循环
- **SC-002**: 每轮迭代从 LLM 调用到回测完成在 15 分钟内（不含数据下载）
- **SC-003**: 安全检查能 100% 拦截杠杆 > 20x、止损 < -0.98、删除 WeeklyBudgetController 的修改
- **SC-004**: Walk-forward 验证能正确检测 OOS/IS < 0.6 的过拟合情况并自动回退
- **SC-005**: 经过多轮迭代后，最终策略的综合得分相较于初始版本有可衡量的提升
- **SC-006**: 所有迭代历史可追溯，任意版本可回滚恢复
- **SC-007**: 对可修复错误（语法/字段/配置）自动恢复成功率达到 >= 70%
- **SC-008**: 单轮失败后的自动修复链路在 3 次尝试内完成或明确回滚，不出现无限重试
- **SC-009**: 因子生成模式下，每 10 个候选至少产出 1 个可晋级候选（门控通过且得分不低于当前基线）
- **SC-010**: 多回测与 Dry Run 联合评分后，晋级候选中至少 80% 在 OOS 与 Dry Run 上不劣于基线
- **SC-011**: 系统在 10 轮内将 TargetGapVector 的加权范数下降至少 30%
- **SC-012**: 周结算状态覆盖率 100%（每周必须归档为达标/亏完/结算三态之一）
- **SC-013**: 跨周复利持仓发生率为 0%（周内 ALL IN 滚仓复利是设计行为，不违反此指标）
