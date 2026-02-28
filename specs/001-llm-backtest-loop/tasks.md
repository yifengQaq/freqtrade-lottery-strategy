# Tasks: LLM Agent + 回测闭环自动迭代系统

**Input**: Design documents from `/specs/001-llm-backtest-loop/`  
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1=单轮迭代, US2=多轮闭环, US3=walk-forward, US4=版本管理, US5=自动纠错+因子生成, US6=多回测+DryRun动态构建)

## Path Conventions

- Agent core: `agent/`
- Strategies: `strategies/`
- Controllers: `controllers/`
- Config: `config/`
- Entry: `scripts/`
- Tests: `tests/unit/`, `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化、目录结构、依赖管理

- [ ] T001 创建项目根目录结构：`agent/`, `strategies/`, `controllers/`, `config/`, `scripts/`, `results/strategy_versions/`, `results/backtest_outputs/`, `tests/unit/`, `tests/integration/`
- [ ] T002 创建 `pyproject.toml`，声明依赖：httpx, pyyaml, pytest
- [ ] T003 [P] 创建 `agent/__init__.py` 导出公共接口
- [ ] T004 [P] 创建 `.gitignore`，忽略 `results/backtest_outputs/`, `results/strategy_versions/`, `.env`, `__pycache__/`
- [ ] T005 [P] 创建 `.env.example` 示例环境变量文件（DEEPSEEK_API_KEY, FREQTRADE_DIR）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有 User Story 共享的不可修改基础组件

**⚠️ CRITICAL**: 策略文件和资金控制器是所有迭代的基础

- [ ] T006 创建 `controllers/weekly_budget_controller.py` — 周内滚仓复利资金控制器：update_balance() 更新余额、get_stake_amount() 返回全部余额、should_stop() 检测达标/亏完、progress 属性追踪滚仓进度（不可修改基础）
- [ ] T007 创建 `strategies/LotteryMindsetStrategy.py` — 基于滚仓复利模型的 FreqTrade IStrategy 子类，stake_amount="unlimited"，custom_stake_amount() ALL IN 当前余额，集成 WeeklyBudgetController，包含 ADX + Bollinger + ATR 指标体系
- [ ] T008 创建 `config/config_backtest.json` — Freqtrade 回测配置（futures, isolated, stake_amount: "unlimited", dry_run_wallet: 100, max_open_trades: 1, StaticPairList 15 对）
- [ ] T009 [P] 创建 `config/iteration_rules.yaml` — 从 Input/v1_agent_iteration_rules.yaml 复制，迭代规则定义
- [ ] T010 [P] 创建 `config/agent_config.yaml` — Agent 运行配置（max_rounds, model, freqtrade_dir, timeranges）
- [ ] T011 [P] 创建 `agent/prompts/system_prompt.md` — 从 Input/v1_agent_system_prompt.md 复制，DeepSeek 系统提示词

**Checkpoint**: 基础设施就绪——策略文件可被 freqtrade 回测，Agent 配置完整

---

## Phase 3: User Story 1 — 单轮 Agent 迭代 (Priority: P1) 🎯 MVP

**Goal**: 实现完整的单轮"分析→修改→回测→评分"流程

**Independent Test**: 给定初始策略 + 模拟回测结果，系统完成一轮迭代输出结构化 JSON

### Tests for User Story 1

- [ ] T012 [P] [US1] 编写单元测试 `tests/unit/test_deepseek_client.py` — 测试 API 调用封装、JSON 提取、重试逻辑（用 mock）
- [ ] T013 [P] [US1] 编写单元测试 `tests/unit/test_evaluator.py` — 测试门控检查、评分公式、推荐生成
- [ ] T014 [P] [US1] 编写单元测试 `tests/unit/test_strategy_modifier.py` — 测试语法验证、安全检查（杠杆/止损/WeeklyBudgetController）、原子写入
- [ ] T015 [P] [US1] 编写单元测试 `tests/unit/test_backtest_runner.py` — 测试命令构建、结果解析、周达标率计算

### Implementation for User Story 1

- [ ] T016 [US1] 实现 `agent/deepseek_client.py` — DeepSeek API 客户端：chat(), chat_with_history(), generate_strategy_patch()，支持 JSON mode、重试、超时
- [ ] T017 [US1] 实现 `agent/backtest_runner.py` — Freqtrade 回测执行器：run() 调用 subprocess，_extract_metrics() 解析 JSON，_calc_weekly_metrics() 计算周达标率
- [ ] T018 [US1] 实现 `agent/evaluator.py` — 结果评估器：_gate_check() 门控检查，_calculate_score() 评分公式，_generate_recommendation() 建议生成
- [ ] T019 [US1] 实现 `agent/strategy_modifier.py` — 策略修改器：_validate_syntax() 语法检查，_safety_check() 安全规则，apply_patch() 原子写入+备份
- [ ] T020 [US1] 运行 US1 全部单元测试，确认全部通过

**Checkpoint**: US1 — 所有模块独立可测，单轮迭代的每个步骤均可独立工作

---

## Phase 4: User Story 2 — 多轮自动迭代闭环 (Priority: P2)

**Goal**: 编排单轮能力为自动多轮循环，含终止条件

**Independent Test**: 设置 `--rounds 5`，系统自动完成 5 轮或提前终止

### Tests for User Story 2

- [ ] T021 [P] [US2] 编写集成测试 `tests/integration/test_orchestrator.py` — 测试多轮循环逻辑、终止条件（连续无提升/最大轮次）、过拟合回退

### Implementation for User Story 2

- [ ] T022 [US2] 实现 `agent/orchestrator.py` — 主循环编排器：
  - `run_iteration_loop()`: 循环调用 DeepSeek → Modifier → Runner → Evaluator
    - 回测报错时调用 ErrorRecovery 执行“分诊→修复→重试”
  - 终止条件检测：连续 3 轮 score 无提升 / 达到 max_rounds
  - 过拟合回退：OOS/IS < 0.6 时回退上一版本
  - 每轮结果写入 `results/iteration_log.json`
  - 每轮输出结构化日志到终端
- [ ] T023 [US2] 创建 `scripts/run_agent.py` — CLI 入口：argparse 解析 `--rounds`, `--walk-forward`, `--list-versions`, `--rollback N`, `--dry-run`
- [ ] T024 [US2] 运行集成测试（用 mock 替代真实 API/backtest），确认循环逻辑正确

**Checkpoint**: US2 — 用户可执行 `python scripts/run_agent.py --rounds 5` 完成自动迭代

---

## Phase 5: User Story 3 — Walk-Forward 验证与防过拟合 (Priority: P3)

**Goal**: 对优化后策略执行 IS/OOS 分段验证

**Independent Test**: 在两段时间范围分别回测，自动计算 OOS/IS 比值并判定

### Tests for User Story 3

- [ ] T025 [P] [US3] 补充 `tests/unit/test_evaluator.py` — 测试 compare_is_oos() 方法，覆盖通过/不通过场景

### Implementation for User Story 3

- [ ] T026 [US3] 在 `agent/orchestrator.py` 中添加 `run_walk_forward()` 方法 — 分 IS/OOS 时间范围各执行一次回测，调用 evaluator.compare_is_oos()
- [ ] T027 [US3] 在 `scripts/run_agent.py` 中接入 `--walk-forward` 参数，在每轮迭代后自动触发 WF 验证
- [ ] T028 [US3] 运行 US3 测试确认通过

**Checkpoint**: US3 — walk-forward 验证可在每轮自动执行，过拟合策略被回退

---

## Phase 6: User Story 4 — 策略版本管理与回滚 (Priority: P4)

**Goal**: 用户可查看所有版本、对比得分、回滚

**Independent Test**: 3 轮迭代后 `--list-versions` 显示版本列表，`--rollback 1` 恢复

### Tests for User Story 4

- [ ] T029 [P] [US4] 补充 `tests/unit/test_strategy_modifier.py` — 测试 list_versions(), rollback() 方法

### Implementation for User Story 4

- [ ] T030 [US4] 确认 `agent/strategy_modifier.py` 的 list_versions() 和 rollback() 已实现（Phase 3 应已包含）
- [ ] T031 [US4] 在 `scripts/run_agent.py` 中接入 `--list-versions` 和 `--rollback N` 子命令
- [ ] T032 [US4] 运行 US4 测试确认通过

**Checkpoint**: US4 — 版本管理完整，用户可查看和回滚

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: 文档、清理、端到端验证

- [ ] T033 更新 `README.md` — 项目说明、架构图、安装指南、使用示例
- [ ] T034 [P] 清理 `agent/` 目录中之前上一轮直接写的代码（如有重复），确保与 plan 结构一致
- [ ] T035 [P] 创建 `results/.gitkeep` 和 `results/strategy_versions/.gitkeep`
- [ ] T036 端到端验证：用 `--dry-run` 模式跑完整 3 轮迭代，确认日志输出正确
- [ ] T037 Git commit 全部实现代码

---

## Phase 8: User Story 5 — 报错自动纠错 + 因子生成迭代 (Priority: P2)

**Goal**: 回测失败时自动修复恢复；引入候选因子实验池，像 RD-Agent 一样“失败反馈驱动下一轮”

**Independent Test**: 人工注入语法错误/指标缺失/配置错误，系统能自动修复或有界回滚；因子候选能被生成并筛选

### Tests for User Story 5

- [ ] T038 [P] [US5] 编写单元测试 `tests/unit/test_error_recovery.py` — 覆盖错误分诊、修复 Prompt 生成、最大重试、失败回滚
- [ ] T039 [P] [US5] 编写单元测试 `tests/unit/test_factor_lab.py` — 覆盖候选因子生成、去重、晋级/隔离判定
- [ ] T040 [P] [US5] 扩展集成测试 `tests/integration/test_orchestrator.py` — 注入 backtest 失败，验证“自动纠错→重试→恢复”链路

### Implementation for User Story 5

- [ ] T041 [US5] 实现 `agent/error_recovery.py` — ErrorRecoveryManager：
    - `classify_error(logs)`：syntax/runtime/config/data 分诊
    - `build_fix_prompt(...)`：拼接错误上下文给 LLM
    - `attempt_fix(max_retries=3)`：修复补丁 → 校验 → 回测重试
    - `rollback_on_exhausted()`：超过阈值自动回滚并打 quarantined 标记
- [ ] T042 [US5] 实现 `agent/factor_lab.py` — FactorLab：
    - 生成候选因子（波动率过滤/趋势过滤/动量过滤）
    - 限制每轮 1-2 小改动（遵守 change_scope）
    - 记录实验账本 `results/experiments/factor_trials.jsonl`
    - 根据门控与得分决定 promoted / active / quarantined
- [ ] T043 [US5] 更新 `agent/deepseek_client.py`，新增 `generate_fix_patch()` 与 `generate_factor_candidates()` 接口
- [ ] T044 [US5] 更新 `agent/orchestrator.py`，接入 ErrorRecovery + FactorLab 的主循环策略
- [ ] T045 [US5] 更新 `scripts/run_agent.py`，新增 CLI 参数：`--auto-repair`, `--repair-max-retries`, `--enable-factor-lab`, `--factor-candidates`
- [ ] T046 [US5] 运行 US5 测试并验证自动恢复成功率统计（目标 >=70%）

---

## Phase 9: User Story 6 — 多回测 + Dry Run 对比驱动动态构建 (Priority: P1)

**Goal**: 基于多窗口回测与 Dry Run 对比结果，动态调整策略参数并逼近 Story 目标

**Independent Test**: 单个候选在 bull/bear/sideways + Dry Run 下产出对比矩阵，并自动生成下一轮参数动作

### Tests for User Story 6

- [ ] T047 [P] [US6] 编写单元测试 `tests/unit/test_comparator.py` — 覆盖多回测结果聚合、稳健性评分、Dry Run 偏差计算
- [ ] T048 [P] [US6] 编写单元测试 `tests/unit/test_target_optimizer.py` — 覆盖目标差距向量计算、微调模式切换、参数步长控制
- [ ] T049 [P] [US6] 扩展集成测试 `tests/integration/test_orchestrator.py` — 验证“对比矩阵→目标差距→LLM调参建议”主链路

### Implementation for User Story 6

- [ ] T050 [US6] 实现 `agent/comparator.py`：
    - 聚合多窗口回测（bull/bear/sideways）
    - 接入 Dry Run 快照并计算偏差（价格/信号/PnL）
    - 输出 `results/comparisons/comparison_matrix.json`
- [ ] T051 [US6] 实现 `agent/target_optimizer.py`：
    - 计算 TargetGapVector（Story 目标差距）
    - 生成参数调整方向与步长
    - 接近目标时进入微调模式（更小步长）
    - 记录 `results/comparisons/target_gap_history.jsonl`
- [ ] T052 [US6] 更新 `agent/deepseek_client.py`，新增 `generate_targeted_adjustment()`，输入对比矩阵与目标差距
- [ ] T053 [US6] 更新 `agent/orchestrator.py`：每轮先跑 comparator，再跑 target optimizer，再调用 LLM 执行目标导向调参
- [ ] T054 [US6] 更新 `scripts/run_agent.py`，新增参数：`--multi-backtest`, `--comparison-windows`, `--dryrun-input`, `--target-profile`
- [ ] T055 [US6] 执行 US6 测试并验证目标逼近效果（TargetGapVector 加权范数下降）

---

## Phase 10: User Story 7 — 周结算与未达标周处理 (Priority: P1)

**Goal**: 明确"未达标未亏完"周的处理规则，保证周内滚仓复利、跨周重置的一致性

**Independent Test**: 三态周结果（达标/亏完/未达标未亏完）都能稳定输出结算动作和下周策略

### Tests for User Story 7

- [ ] T056 [P] [US7] 编写单元测试 `tests/unit/test_weekly_settlement.py` — 覆盖三态判定与周末强制结算
- [ ] T057 [P] [US7] 扩展集成测试 `tests/integration/test_orchestrator.py` — 覆盖连续未达标触发冷却

### Implementation for User Story 7

- [ ] T058 [US7] 实现 `agent/weekly_settlement.py`：
    - 周状态机 `TARGET_HIT / BUDGET_EXHAUSTED / WEEK_END_SETTLED`
    - 未达标未亏完时周末强制结算并下周 `reset_budget_100`
    - 连续未达标净值恶化触发 `cooldown_dryrun`
- [ ] T059 [US7] 更新 `agent/orchestrator.py` 接入周结算报告并将报告写入 `results/weekly/weekly_settlement_reports.jsonl`
- [ ] T060 [US7] 更新 `scripts/run_agent.py` 增加周治理参数：`--week-settlement-policy`, `--cooldown-threshold-weeks`
- [ ] T061 [US7] 运行 US7 测试并验证“跨周复利持仓发生率=0%”

---

## Dependencies

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundation: strategy + config)
    ↓
Phase 3 (US1: 单轮迭代 — T016-T020 可并行开发各模块)
    ↓
Phase 4 (US2: 多轮循环 — 依赖 US1 的所有模块)
    ↓
Phase 5 (US3: walk-forward — 依赖 US2 的 orchestrator)
    ↓
Phase 6 (US4: 版本管理 — 依赖 US1 的 strategy_modifier)
    ↓
Phase 8 (US5: 自动纠错+因子生成 — 依赖 US2/US3/US4)
    ↓
Phase 9 (US6: 多回测+DryRun动态构建 — 依赖 US2/US3/US5)
    ↓
Phase 10 (US7: 周结算与冷却治理 — 依赖 US2/US4)
    ↓
Phase 7 (Polish — 依赖所有 above)
```

**Parallel Opportunities**:
- Phase 1: T003/T004/T005 并行
- Phase 2: T009/T010/T011 并行
- Phase 3 Tests: T012/T013/T014/T015 全部并行
- Phase 3 Impl: T016/T017/T018/T019 可并行（不同文件）

## Implementation Strategy

1. **MVP = Phase 1 + 2 + 3**: 实现后即可手动触发单轮迭代
2. **Full Loop = + Phase 4**: 自动化多轮循环
3. **Resilience = + Phase 8**: 自动纠错 + 因子实验闭环
4. **Adaptive Build = + Phase 9**: 多回测 + Dry Run 对比驱动动态构建
5. **Weekly Governance = + Phase 10**: 周结算三态 + 冷却机制，确保跨周不复利、周内滚仓复利的一致性
6. **Production = + Phase 5 + 6 + 7 + 8 + 9 + 10**: 防过拟合 + 版本管理 + 自修复 + 动态构建 + 周治理 + 文档

## Summary

| Metric | Value |
|--------|-------|
| Total Tasks | 61 |
| Phase 1 (Setup) | 5 |
| Phase 2 (Foundation) | 6 |
| Phase 3 (US1 MVP) | 9 |
| Phase 4 (US2 Loop) | 3 |
| Phase 5 (US3 WF) | 4 |
| Phase 6 (US4 Versions) | 4 |
| Phase 8 (US5 Recovery+Factors) | 9 |
| Phase 9 (US6 Multi-BT+DryRun) | 9 |
| Phase 10 (US7 WeeklySettlement) | 6 |
| Phase 7 (Polish) | 5 |
| Parallel Opportunities | 22 tasks |
| MVP Scope | US1 (Phase 1-3, 20 tasks) |
