# Fair v2：Standards 审查与最小 Interface 方案

审查日期：2026-11-07。固定基准为 `reviews/fair-v2-baseline/`，无 Git，未创建 tracker。依据 root AGENTS 的独立环境、资产保留和证据边界，以及 HARD-SPEC、HARD-RESULTS 和用户最新的“四种方法先优化、再公平实测”要求。只读源码与旧失败记录；未改 src，未调用模型。

## Standards 审查发现（400 字以内）

1. **[P2，设计判断／possible Primitive Obsession]** `stage_result` 接受自填金额并包装成回执事实；P2/eager 已发生跨候选错配。应绑定候选、版本与报价，按引用提交。[world.py:110](../src/loop_lab/hard/world.py#L110)
2. **[P2，设计判断]** 24 次共享额度无协调者收尾预留；R2/domain 专家第 23 次已答对，仍无交付。应限制子任务并预留收尾。[runtime.py:173](../src/loop_lab/hard/runtime.py#L173)
3. **[P2，可靠性]** 外呼前未落盘 attempt-start，恢复只读完成日志；进程强制终止后可能复用调用号并漏记在途消耗。[models.py:97](../src/loop_lab/models.py#L97)
4. **[P2，可靠性]** 初始化在 trial 的 try 外，`future.result()` 无兜底；异常可中断汇总与 manifest 收尾。[experiment.py:186](../src/loop_lab/experiment.py#L186)
5. **[P2，设计判断]** 专家每次重建，仅收到用户消息，缺少此前已验证候选与计算引用。应保留带版本成果，减少重复调查。[runtime.py:157](../src/loop_lab/hard/runtime.py#L157)

第 1、2、5 项是针对新一轮优化的设计判断，不倒推为旧 SPEC 的违规。第 3、4 项是恢复与完整留痕的可靠性缺口。未报告由 ruff 覆盖的样式问题。

## 本轮核查证据及未误报的行为

- P2/eager/rep1（本机 `runs/hard-core-v1/P2-eager-1/result.json`）：提交 `B3/C4/T5`，utility 169，现金错误地填为另一个候选的 9830；真实现金 10222 超出预算。评分器已拒绝，不能把这条记录称为通过。
- P2/tools/rep1（本机 `runs/hard-core-v1/P2-tools-1/result.json`）：方案可行但次优，utility gap=4；不能只做“金额与候选绑定”便声称解决全局最优。
- R2/domain/rep0（本机 `runs/hard-core-v1/R2-domain-0/result.json`）：24 次调用全部已计量；第 23 次审计专家有正确结果，第 24 次由采购专家消耗，主协调者未保存草案。需要验证预算分配，而非给 domain 更大的未计入总数额度。
- 当前预算使用锁、在途预留与 shared cap，`ThrottledModelRun` 限制实际请求并发；未发现绕过这两者的普通主/子调用路径。
- 超时后未知用量导致停止新请求，是 HARD-SPEC 明确的保守策略，不作为缺陷。不能为了完整矩阵删去该错误、把未知用量设为零，或偷偷清零 Budget 重跑。
- 当前 Codex CLI 是原生 Mach-O 可执行文件，不是已知会遗留 native 子进程的 npm wrapper。本次没有证据支持“超时一定留下后台计费进程”的断言。
- 计算器已有节点/深度/常数限制并把它们公开到工具说明；此轮没有重报旧已修缺陷。报价 module 应优先使用整数分，不再依赖模型编写价格表达式。

## 最小 Interface 设计：ProposalDesk

设计约束是 1–3 个操作入口。本方案选择两个：`assess` 和 `stage`。它是采购候选到可核验草案的 deep module，模型工具层和测试跨过同一个 seam。读取原始资料、管理主/子 Agent 拓扑仍由外层执行器负责；不得把所有 Agent 编排塞进该 module。

### Interface

示意类型，不是已验证可运行代码：

```python
desk = ProposalDesk(
    request: RequestContext,
    evidence: EvidenceBundle,
    store: LocalProposalStore,
)

assessment = desk.assess(candidates: list[Candidate]) -> Assessment
receipt = desk.stage(selection_ref: SelectionRef, idempotency_key: str) -> ProposalReceipt
```

构造配置也属于 Interface：

- `RequestContext`：task/session 身份、当前用户 revision、用户消息来源、结构化约束、提交意愿；同一 revision 内不可变。
- `EvidenceBundle`：允许访问的政策/产品事实、明确的 source ID、内容摘要和政策版本。不能包含 `task.expected`、`latest_case` 或 oracle 结果。
- `Candidate`：排序、去重后的 SKU ID 集合。调用方不得传金额、效用、折扣或自称可行性。
- `Assessment`：每个候选的不可变 Quote、费用分解、约束检查、排序后的候选、已检查范围和 `selection_ref`。
- `SelectionRef`：module 生成的不可猜测或内容寻址引用，绑定 request revision、约束摘要、政策和资料摘要、被比较的 quote 集合，以及该集合中最优 quote。
- `ProposalReceipt`：真实落盘回执、完整绑定的最终值、证据引用、重复请求标志。最终 Answer 使用它，不让模型重新拼装数字。

`assess` 只评价模型提交的候选，不暗中枚举整个组合空间；输出明确标记 `best_among_evaluated`。若只有一个候选，就只有“该候选可行/不可行”的证据，不能宣称全局最优。自动穷举或优化器应单独成为预先声明的新实验条件，不能悄悄嵌入此工具。

### Invariants、顺序与错误

1. 当前 RequestContext 和资料版本建立后，才可调用 `assess`；每次用户修订建立新的 context，旧引用不能跨 revision 提交。
2. 报价从 source 数据计算，费用全部使用整数分；政策门槛、取整、运费和资金抵扣在 module 内统一处理。
3. `stage` 从 `selection_ref` 加载保存的选择；禁止同时传入可替换 SKU/金额/版本的字段。
4. 身份、revision、政策/资料摘要、提交意愿、引用存在性和幂等键，在实际写入前再次检查。
5. 相同幂等键与相同引用返回原回执；同一键对应不同选择返回 conflict。失败不写草案，失败尝试仍进入审计事件。
6. 可预期错误返回类型化结果：`unknown_product`、`invalid_candidate`、`no_feasible_candidate`、`stale_selection`、`submission_not_requested`、`idempotency_conflict`。I/O 故障作为运行错误保留，不能包装成业务拒绝。
7. 给候选批次设置公开上限，返回已处理范围。不得静默截断后把部分排序称为完整最优。

RequestContext 的语义尤其需要保留实验公平：更新后的约束必须来自合法用户消息及所有变体一致的解析契约，不能把 evaluator 的 `latest_case` 注入业务工具。若由模型提出约束，则保存其解释与来源，由独立 evaluator 判错；不能因为 Quote 计算正确就视为用户意图理解正确。

“用户只要求分析”是否由工具硬拒绝，需在四组共同协议中预先固定。硬拒绝可以防副作用，但违规提交尝试仍应计入方法表现，不能用工具保护掩盖模型错误。

### Usage

```python
# 模型从公开目录提出候选；不存在手工传 cash_cents 的入口。
assessment = desk.assess([
    Candidate(["B1", "C5", "T5"]),
    Candidate(["B2", "C6", "T2"]),
])

# assessment 明示“已检查候选中最优”，不保证全集最优。
# 用户当前轮已授权提交时，引用本次实际比较生成的选择。
receipt = desk.stage(assessment.selection_ref, idempotency_key="turn-2-final")

# 最终 Answer 直接引用 receipt.facts / receipt.receipt_id。
```

相比独立的 `quote_candidate`、`compare_quotes`、`stage_payload`，本方案把报价注册、比较集合、赢家和提交值绑在一个引用中。模型仍决定调查、候选生成与是否需要扩大搜索，但不再负责跨消息复制金额。

### Implementation 藏在 seam 后面的内容

- SKU 规范化与身份范围校验。
- 当前数据/政策快照、内容摘要与版本关联。
- 费用与效用计算、约束检查和稳定排序。
- 不可变 Quote / Selection 登记、引用查找、revision 失效。
- 草案事务、幂等处理、回执构造和可审计错误。
- 本地事件序列及恢复；提交草案和写事件采用一致事务，导出的 JSON 是数据库状态的表示。

删除该 module 后，这些复杂性会重新散落到模型提示、四套 runtime、工具函数、提交和评分接线中，因此通过 deletion test；它不是简单转发的 Middle Man。

## 依赖策略与测试 seam

| 依赖 | 类别 | 处理方式 |
|---|---|---|
| 金额、排序、约束判断、摘要计算 | in-process | module 内直接实现；通过 `assess` 测试，不为纯计算新增 adapter |
| Quote/草案/事件存储 | local-substitutable | 内部 store seam；正式用 SQLite，测试使用临时 SQLite/目录，跨同一公共 Interface 验证 |
| Codex CLI/模型提供方 | true-external | 外层注入 `DecisionModel` port；真实 CLI adapter 与 ScriptedModel adapter 已有实际需要 |
| 用户请求与原始事实 | 输入数据 | 有类型、有来源的快照；不伪装成需要联网的 service |

模型 port 应返回标准化的 `DecisionOutcome`，包括决定、usage（可为 unknown）、attempt ID、传输状态和原始记录位置。预算、重试策略和请求顺序由执行器管理，不能藏在 CLI adapter 内偷偷增加调用。CLI adapter 可替换而业务 Quote 规则不变。

公共 Interface 的重点测试：错候选金额无法注入、费用边界、同效用平局顺序、未覆盖全集不宣称全局最优、旧 revision 引用、政策/资料变更、非法提交、幂等冲突、事务失败无部分草案、重开 store 恢复。测试独立 oracle 应继续位于 evaluator；不得把现有 `oracles.evaluate_plan` 直接注册成运行工具，再用同一函数宣布独立验证通过。

替换式测试迁移应保留现有资产与历史证据：先证明新 Interface 测试覆盖原公开行为，再讨论删减只重复内部细节的测试；遵守 root AGENTS 的删除资产规则。

## 执行器的必要配套改进

### 收尾预算与上下文成果

为每任务分配“可委派工作额度 + 协调者收尾额度”，仍计入同一个 24 次总上限。子任务拿到剩余额度和明确截止条件；接近限额时返回最佳已验证结果、尚未完成项和引用。所有方法都知道剩余预算，避免只给 domain 隐藏优惠。

优先传递版本化 Quote/Selection/证据引用；暂不为了缓存而给每个专家建立长期会话。若仍需跨轮专家上下文，先试带来源的紧凑成果缓存，只有回归显示不足再引入有界持久专家。每次用户 revision 变化检查资料是否仍有效，不能把旧结论直接当作新约束下的事实。

### 超时、重试与完整记录

每次外呼先原子登记 `attempt-start` 和预算 reservation，再运行 CLI；成功、失败、超时分别登记结算。恢复时发现未结算 attempt，标记未知消耗并阻止静默重用调用号。

不要自动重跑整个业务任务。若协议允许重试，应仅重试尚未执行业务动作的模型决策请求，使用新的 attempt ID，保留旧输入/输出、用量和状态；所有重试计入调用数。未知消耗仍遵守已约定的停止规则，放宽必须成为新的、明确记录的预算协议。不能为了消除一条超时而把它从统计中移走。

把 trial 初始化、运行、结果写入和状态收尾集中在一个生命周期；batch 对 runner 的意外异常生成结构化失败记录，并在 finally 中保留终止原因与未完成清单。文件系统已不可写时应至少明确报错，不宣称完整结果已落盘。

## Trade-offs 与公平实测要求

**Depth / Leverage**：两个入口解决报价、比较、revision、幂等与回执绑定，四种方法共享一份业务实现；调用方不再学习裸金额的手工关联规则。

**Locality**：政策算术、报价与草案改变集中在 ProposalDesk。模型 transport、预算和 Agent 拓扑仍是外层职责，避免把一个实验 harness 做成无边界的大 module。

**代价**：此 module 含有可执行业务规则，维护成本高于裸 calculator；它改变了模型承担的计算任务，因此新结果必须标记工具协议版本，不能直接与旧 v1 成绩合并宣称架构进步。第一版只处理采购，不强行把账务审计塞进同一个 Candidate 类型。

**公平**：四种方法同时获得同一 `assess/stage` 契约、相同事实、预算和评分；Skills 仍只提供如何利用工具的方法。旧 P2/R2 可以作为开发诊断，不再当作新的独立确认样本。冻结后使用未参与修补的新案例，比较候选覆盖、正确提交、预算收尾、token/延迟，以及真实发生的委派和 Skill 加载。
