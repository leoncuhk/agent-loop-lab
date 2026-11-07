# fair-v2：Spec 独立审查与灵活 Study/Trial Interface

日期：2026-11-07。审查基准为 `reviews/fair-v2-baseline/`；结合 `HARD-SPEC.md`、`HARD-RESULTS.md` 及当前实现。只写审查/设计及独立留出参数文件，没有改 src、Skills、依赖或运行真实模型。本方案使用 code-review 的 Spec 轴及 codebase-design / DESIGN-IT-TWICE 的 Interface、module、seam、adapter 词汇；是父级并行接口探索中的“灵活性”方案。

## 1. 现有证据能支持什么，不能支持什么

- HARD-RESULTS 已诚实报告 core 不完整、超时联动停止及没有总排名。它给出了开发问题，尚未实现“各方法获得相同调优机会后在新题上公平比较”。不能把旧提示词/旧 domain 委派表现当作其方法类别最优实现。
- 基准 `src/loop_lab/models.py:50-76` 将一次未知用量写到共享 `Budget.unknown_usage`，阻止所有后续 reserve；`experiment.py:160-192` 因而让一个请求超时导致其他方法/任务无法完成。这是预算保守策略，不是业务推理质量；适合诊断，不能继续用作完整科学比较的故障隔离机制。
- 基准 `hard/runtime.py` 将同类模型角色临时嵌入工具，模型调用共享单任务上限，但缺乏显式 worker 返回契约、持久化复用、收尾预算及过度重复调查终止规则。旧 R2 的正确局部解未转化为交付，说明应改善接口，而非固定这个坏实现为多 Agent 的代表。
- 基准 `hard/scoring.py` 的最终状态/最优性检查有价值；没有独立核验每轮分析的正确性。fair-v2 若继续只验最后 revision，指标必须明确叫“最终条件保持与交付”，不要扩大成“每轮分析都正确”。
- 当前只有一模型、一 effort、合成任务和 CLI 传输。实际 tokens、请求启动开销、CPU 和工具结果长度都是架构相关成本，不能只比模型调用次数或只看成功样本延迟。

## 2. 先固定估计对象，再优化四种方法

主研究估计对象：**在相同事实/政策/工具、相同开发调优机会、同一测试推理配置与单任务资源约束下，四种可实际部署的方法配置的交付和成本。** 不是未经控制的“仅 Agent 数量的因果效应”，也不是全局最优架构排名。

四个 MethodSpec 在正式留出前固定：

| 方法 | 保留的机制 | 可在开发集优化的实现 | 不能暗中变化的机制 |
|---|---|---|---|
| tools | 单一模型上下文、共享业务工具，没有待测流程 Skills | 任务目标表达、工具调用/引用约定、收尾策略 | 不能把完整 Skills 偷写进 prompt；不能新增模型 worker |
| eager | 单一上下文、同一方法库正文预加载 | prompt 排布、缓存稳定部分、共享基础流程 | 不能删掉另一组仍需发现的库内容后继续称同库比较 |
| skills | 单一上下文、同库元数据与按需正文加载 | description/触发条件、加载组织、持久加载复用 | 不允许隐藏业务规则只给这一组；不能暗中预加载正文 |
| multi | 统一 coordinator-worker Interface、独立且可复用的 worker 上下文 | 按完整交付 context 划分、委派时机、worker 复用、合并/停机与收尾预算 | 主子推理合计相同预算；不得改为多个不计费独立重试或隐式复制金标 |

第四组采用优化后的统一多 Agent 方法，不把旧 domain 结构锁死：

- 采购可按 A/B 兼容平台的完整可行解空间分工，每个 worker 处理全部政策、目录、费用与目标，返回局部候选；协调者比较同一排序契约。必须离线验证分区并集覆盖全空间；universal-only 情况预定义唯一分区归属，避免漏解/重复归因。
- 审计按独立 branch 分工，共享同一政策和截止时点，返回各分支结果及来源。
- 每个任务在所有 revision 上保留同一批 worker 的消息与证据，新增修订按序广播；不能把旧 case 再读一遍当作最新目标。
- 可允许自适应不委派，记录实际 worker 数、分区与调用路径。未使用 worker 的 trial 不声称观察到多 Agent 优势。
- 旧 `domain` 留作诊断对照；不要与新 `multi` 混同版本。若要单独声称上下文隔离收益，另做同方法正文/同任务分区的单循环控制，不能从上述四个完整系统直接归因。

## 3. 开发调优与冻结

建议每方法获得相同的已见开发题、两轮候选修改机会和相同 target-model 调用/token/墙钟额度；例如每方法最多 96 次开发模型调用、两轮候选，各轮使用预定同一开发面板。额度需在开跑前共同确定，不能因为某组接近输赢临时追加。调优者的修改、失败、候选评分和累计花费全部进入 DevelopmentLedger；模型调用包含 coordinator、worker、verifier 和技术重试。

可使用旧 P0–P4/R1–R2/A0–A2 及明确标记为开发的新种子；不能读取最终留出参数文件来选 prompt。选择本方法候选的规则预先固定：先满足硬约束/完整交付，再比较可行/最优和资源；接近时保留较简单版本。允许各方法用完额度，也允许提前停止，但不可把某组未用额度转给另一组。

同一主模型、相同 effort、单调用/单任务/整研究限制对各组一致。若研究的是“每方法自行选最优 effort/model”，这属于另一个“预算约束最佳系统”估计对象，应另立 StudySpec，不能把不同推理配置的结果写成纯架构对照。

冻结物至少包括：任务生成器/政策/产品 schema、oracle、评分器、四份方法配置及提示词、共享工具、模型与 CLI、依赖 lock、重试与预算规则、输出 schema、随机排程、留出参数文件 hash。冻结后才由 runner 解封生成最终题。方法调优不得继续；评分器/装置缺陷按版本处理，保留旧原始记录。

## 4. 共享工具改进：先 quote，再考虑 solver

`quote(candidate_ids, request_revision)` 对**指定的一个候选**按业务政策计算，返回不可拆开的 `quote_id / candidate_ids / constraints_digest / policy_version / cash / utility / feasible / breakdown / evidence_refs`。`stage_quote(quote_id, request_revision)` 引用报价，不由模型重新拼金额或 SKU。身份、revision 和过期报价规则仍由工具执行。四方法使用同一实现、相同描述和价格/预算规则。

quote 不是 optimizer：它不搜索候选，也不返回最优 SKU。模型仍需选择、比较和处理用户修订。它把旧实验中“算得对却把金额绑给另一组合”的任务从语言拼装中移到确定性 Interface；这是共同底座变更，要单独版本记录，不能只替落后组加。

若新增精确 solver：必须用模型实际可访问的 packets 和明确目标构建请求，不读 `task.expected`、留出 manifest、scoring 输出或 oracle module；独立实现、独立测试，与评估 oracle 仅可在测试 seam 上对照。求解器计算时间、候选规模与工具调用也入账。所有方法同权使用。若共享 solver 让最优性全部通过，这是“工具解决了形式化子问题”的结果，不能宣称四种推理架构能力相等；因此保留 quote-only 的预注册面板，solver 面板单列。

## 5. 不让单个 CLI 超时破坏整批平衡

### 三层账本与资源租约

StudyLedger 持有总预授权额度；每个 `(case, method, rep)` 在开始前领取相同 TrialLease，覆盖所有 revision、主子调用和有限技术恢复；每个请求再领取 AttemptLease。分配公平不等于强迫花掉额度，未用额度不转给别的方法。研究硬调用额度不足以覆盖全部配置的最坏调用上限时，须提前缩小面板或声明分阶段配额，不在最后根据谁更省而决定谁能跑完。

某次请求超时只将其 AttemptLease 标为 `usage_unknown`，保留占用，不向整个研究广播“所有方法立刻无效”。其他已领取、互不挤占的 TrialLease 可继续。若授权总额度确实不足，冻结后续调度并保留明细；不能把其他组未运行项评分为推理失败。系统性认证故障、模型/CLI 协议漂移、装置完整性损坏才触发全研究暂停。

**不能用未知 token=0 伪造预算。** 当前 CLI 无可证实的每调用总 token 硬上限，150k 预留只是停止阈值，不自动成为未知消耗的严格上界。若要求严格的总 token 硬预算，需要 TransportAdapter 提供可核验的输入/输出限制与超时用量恢复；否则主约束明确采用调用数、相同 effort 和请求/任务墙钟上限，tokens 记已知值、未知次数及保守预留，不宣称实现了严格相等的实际推理计算量。

### 首次表现与技术重试

- 主表永久保存预定所有 96 个 trial 的首次 attempt：首次完整交付率、首次业务成功、技术故障率分别列出。未观察到业务输出的技术故障是“未交付”，不是已证实的语义失败。
- 每 trial 最多一次公共技术恢复，只针对预先列举的 transport timeout/临时服务错误；不得因低分、次优、工具参数错、业务拒绝或 TaskBudgetExceeded 重试。所有方法同一规则。
- 恢复以独立 attempt_id、相同方法/任务/配置，从干净业务世界和图开始，避免接用已经消费/部分写入的状态；全重放成本计入该 trial 的恢复预算。首次原始日志/评分不替换。若支持可靠 checkpoint continuation，另立同样适用于各组的恢复策略版本。
- 第二张表报告“统一恢复策略下交付率”和恢复代价；不得择优选首次/重试。技术失败与方法耗时可能相关，因此 complete-case 语义分数只能作次要描述，同时给全样本上下界，不能删超时后宣布某方法最强。
- 每个 task/rep 是平衡 block：四方法全部尝试后才进入下一 block，内部顺序随机且跨重复轮转；最多四个真实请求并发。若某 cell 长任务结束较慢，其他方法不得占用它的推理额度。相同并发负载与等待时间分别记录。

## 6. 灵活但可核验的 Interface

推荐将深 module 放在 **Study orchestration seam**：让业务 runner 不必知道重试、故障隔离、账本恢复和版本规则。允许实际变化的 TaskFactory / MethodAdapter / TransportAdapter / OutcomeEvaluator 注入；不为尚无第二实现的每个函数创造 plugin 层。

```python
StudySpec(
    methods: tuple[MethodSpec, ...],
    development: DevelopmentPolicy,
    task_source: FrozenTaskSource,
    resource_policy: ResourcePolicy,
    recovery_policy: RecoveryPolicy,
    metrics: MetricContract,
)

study = Study(spec, task_factory, method_registry, transport, evaluator, artifact_store)
receipt = study.freeze()       # hashes + declared capabilities; no final model runs
run = study.run(receipt)       # immutable attempt ledger; resumable scheduler
report = study.report(run.id)  # first-attempt + recovery + paired coverage + cost bounds

MethodAdapter.start(context: TrialContext) -> TrialSession
TrialSession.advance(message: RevisionMessage) -> TurnOutcome
TrialSession.snapshot() -> SessionCheckpoint
TrialSession.close() -> None

TransportAdapter.invoke(request: ModelRequest, lease: AttemptLease) -> ModelOutcome
OutcomeEvaluator.grade(case: EvaluatorCase, trace: TrialTrace) -> Grade
```

### Interface 不变量

1. `case_id/method_id/rep/attempt_id/protocol_hash` 唯一；MethodAdapter 只见 PublicCase，不见 EvaluatorCase.expected 和私有生成参数路径。
2. revision 单调，所有主/子消息标明原始时序；报价/草案使用 constraints_digest，旧 revision 不能通过覆盖字符串伪装成现行。
3. 任何成功返回都带 artifact_refs、actual topology、所有 call 使用量及完整性状态；没有输出的超时也产出 ModelOutcome，不能抛异常后丢掉记录。
4. 每笔副作用先有可恢复的 intent_id；同 attempt 同 intent 重放幂等。跨 attempt 世界隔离，避免重试继承既有金标/草案。
5. 事实来源由真实工具记录形成；stage 时保存的是模型已经看到的引用，而非世界里另一个线程刚读过但尚未返回的材料。
6. evaluator 不调用模型，不由 MethodAdapter 提供“是否最优”；保存引用评分器版本，重新评分必须形成新派生结果。
7. worker 预算、恢复预算、并行度归属显式；主子累计不超 TrialLease。收尾调用配额在多 Agent 内部预留，不从其他方法借。

### 错误及结果交付

ModelOutcome 的 status 至少区分 `ok / transport_timeout / provider_error / protocol_error / task_budget / study_budget / cancelled`；模型格式不合协议属于可观察的模型/桥接交付结果，不自动等同临时网络错误。`usage` 用 `known / partial / unknown`，不能把空字典当零用量。

Grade 分开记录 final feasibility、objective optimum、cash accuracy、revision compliance、audit branch accuracy、evidence grounding、first delivery 和 recovery delivery。高层 `passed` 仅为预注册完整交集。Report 同时交付全量 case/method/rep/attempt 表、配对覆盖、任务级翻转、逐族结果、token/延迟完整性和未完成列表，不能只交一张总排行榜。

### 依赖策略与成本

- 模型传输是外部依赖：当前 Codex CLI Adapter 保留，测试用 ScriptedTransport；实际 CLI/原生 API 延迟不可混为同一估计对象。
- LangGraph 是执行依赖：保留真实 create_agent 与 checkpoint；mock 只替模型 seam，不替换成脚本给出正确动作后声称真实模型实验通过。
- TaskFactory / quote 是本地确定性依赖；evaluator/oracle 独立代码路径，不共用 solver 作为正确性的唯一来源。用人工算例、另写穷举、变形验证及边界样例交叉检查。
- ArtifactStore 默认本地目录+SQLite可实现，不要求新服务；只追加原始证据、原子写 manifest、锁住 freeze。锁定依赖，CLI版本和可用功能写入 capability receipt。
- 该设计的 leverage 是重试/公平调度/错误语义/留出保护集中，新的方法只实现 TrialSession；代价是比当前 run_batch 多了显式生命周期和账本。当前无需通用 DAG/plugin runtime，避免 Interface 跟实现一样复杂。

## 7. 已独立锁定的最终留出参数文件

文件：`reviews/fair-v2-holdout-seeds.json`。SHA-256：`adaffb990d8f953c46b0c5154ba836e2ffd84a64c4dc208c1e9f5cd35a8bdd59`。

一次性生成 12 个新种子，不按任何方法成绩筛选；文件仅包含 seed、family 与 group_count/branch_count，没有答案。题面预定构成为：6 个组合（3/4/5 类各两个）、3 个多轮（3/4/5 类各一个）、3 个审计（一个 3 分支、两个 4 分支）；每题两重复，四方法共 96 trial。具体 seed 和 gold 不写入本报告，也不发给父级调优者；最终 freeze 后由 runner 解析。

当前生成器可支持的 7 个条目已离线确认可生成合法输入和可行方案；另 5 个条目需要通用 5 类/4 分支生成器扩展。它们没有被换种子或偷偷降规模。扩展后应对原条目重新验证输入、参考解及分区覆盖；若无解或实现不支持，显式记录 generator defect 并按预注册无模型参与的修复规则处理，不能看某方法成绩再剔题。题目生成预算必须明确落在至少一个可行组合上，但不得用方法响应调整任务难度。

这份留出保护是操作隔离，不是密码学不可访问；同工作区调优者理论上可以打开文件。冻结后首次运行才称留出；任何提前读取须登记，不能继续称独立未见测试。

## 8. 要补的测量缺口

- 先实现通用生成器、共享 quote、持久多 Agent 与受控恢复；再对每方法给同等开发预算。未经这些改动不能标“已尽可能优化”。
- 同一最终题两次重复仍是小样本；区间按 case 聚类，family 结果单列，不能把 96 trial 当 96 个独立业务场景。
- 24 项库规模与 raw/compact 表示属于额外因子。主面板先固定一个库规模及表示；交叉面板使用预先预留的额外预算和相同题目，避免把方法的全部调优机会花在不同因子上。
- 若每轮分析也要验收，需先新增结构化 provisional_plan 和逐 revision 独立预期；当前只测最终条件保持。若不新增，继续清楚声明该范围。
- 技术恢复后还可能全部通过；这不证明四方法没有质量差异，也不要求为了区分度而改 gold 或故意制造失败。
