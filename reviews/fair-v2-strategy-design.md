# Fair v2：面向最常见 caller 的四方法优化设计

设计日期：2026-11-07。Design It Twice 第三方案：让批次 runner 和应用会话 caller 尽量简单，把报价绑定、方法配置、worker 生命周期、预算和回执集中在一个 deep module 内。本文只读现有实现与冻结基准，不修改 src，不运行模型；以下 Interface 与配置是提议，尚未实现或验收。

已读 `codebase-design/SKILL.md`、`DESIGN-IT-TWICE.md`、`DEEPENING.md`。依据以 `reviews/fair-v2-baseline` 为准；核查时当前 `hard/runtime.py` 与 `hard/world.py` 与该快照字节相同。

## 1. 具体问题与设计目标

`HARD-RESULTS.md` 的失败需要分别处理：P2/tools 已正确算出最优候选现金但最终选了次优；P2/eager 把别的候选金额移到当前候选；R2/domain 的专家已有正确局部结果，却在24次共享调用内没有留下最终草案。它们分别指向候选选择、实体与结果绑定、交接与收尾预算。没有证据能把三种问题都归为“某方法无效”。[结果](../HARD-RESULTS.md)

当前 `_child()` 每次都用随机 reader 名、新建无 checkpointer 的图；委派 Interface 是 `delegate(specialist, assignment)`，政策/采购/审计角色的资料范围高度重叠。主 Agent 在 domain 组不能直接使用全部读工具，且各次重复委派重新建立上下文。这个 Interface 把协调复杂度留给模型，当前不是完整工作包的深度委派。[冻结 runtime](fair-v2-baseline/src/loop_lab/hard/runtime.py#L68)、[child 生命周期](fair-v2-baseline/src/loop_lab/hard/runtime.py#L157)

建议保留一个深 module：**HardExecution**。其 Interface 继续服务两个实际 caller：完整任务批次 `.run()`，以及交互会话 `.advance(message)`。caller 不负责创建专家、分配 thread_id、拼报价数字、合并 budget 或猜测是否已完成提交。

## 2. 常见 caller 的 Interface

示意用法（不是已经存在的新参数）：

```python
# 最常见 caller：四组用同一入口，只有 method 变化。
with HardExecution(
    task,
    method,                     # tools | eager | skills | multi
    model_client,               # 现有真实 bridge 或脚本模型 adapter
    output_dir,
    protocol="fair-v2",
) as session:
    outcome = session.run()

# 应用 caller：同一实例连续接收用户修订。
with HardExecution(task, "multi", model_client, output_dir, protocol="fair-v2") as session:
    first = session.advance("[revision 0] 先分析，不提交")
    second = session.advance("[revision 1] 平台改为B，其余条件保留")
```

兼容当前 `trial()` 的 `answer / turn_answers / world` 返回结构；增加 `method_contract`、`actual_topology` 和 `budget_summary`，不要求 caller 自行扫描各 child 的内部状态。`run()`、`advance()` 与资源释放即可，不引入独立的 plan/start_worker/poll_worker/synthesize/finalize caller 流程。[现有 caller](fair-v2-baseline/src/loop_lab/hard/run.py#L21)

Interface 必须公开的规则：

- 每个 session 只对应一个 task、一份方法配置、一组冻结工具与知识版本；用户 revision 顺序进入同一主会话。
- worker 只能调查与返回候选，主 Agent 独占最终草案写入；worker 没有 stage 工具。
- 同一预算包含主 Agent、worker、返工和所有用户轮次；调用用量未知继续沿用停止批次的规则。
- 任务额度不足、基础设施错误、分支未完成、没有可行候选、成功交付分别返回/记录；不能以一个空成功答案掩盖。
- 应用可见材料仅从公开 `packets` 和已收到的用户消息构建。初始化时生成白名单 `PublicCase`，之后 runtime 不保留 `expected`、`latest_case`、oracle 或未来用户修订内容的引用。

最后一点尤其重要：当前 task 同时携带模型材料与评分真值。新增工具不能为了“正确计算最新条件”直接取 `task.latest_case`；这会把独立评分真值变成业务工具输入。[任务生成结构](fair-v2-baseline/src/loop_lab/hard/tasks.py#L133)

## 3. 所有组共用的报价、比较与提交 module

建议把业务计算和候选 registry 放在 `HardWorld` 内部或其私有 module；不是在四组各写一份，也不能 import `hard.oracles`。规则按公开政策独立实现；oracle 继续作为另一个 implementation 作验收。

| 工具 Interface | 实际语义 | 必须避免 |
|---|---|---|
| `quote_bundles(candidates, requirements, request_revision, policy_ref)` | 为模型明确提交的每组 SKU 算报价，返回稳定 quote_id、SKU、效用、逐仓金额、现金、条件检查、来源、policy/version 与需求 hash。可批量降低机械调用，但四组完全相同。 | 从隐藏 latest_case 补全需求；自动穷举全目录；返回 expected、全局最优答案或正确答案提示。 |
| `compare_quotes(quote_ids)` | 对同一需求/政策版本、已登记的报价按公开目标排序；返回这些候选中的最好项与差异。 | 把“比较过的候选中最好”表述为“全局最优”；接受混合 revision/policy 的报价直接排序。 |
| `stage_result({quote_id, request_revision})`（采购类） | 根据持久 registry 物化完整草案，返回事实和回执；由模型选择 quote_id，不再让模型手抄现金和效用。 | 再接受模型覆盖 quote 对应的金额、SKU 或政策版本。 |

`requirements` 必须是模型从实际用户消息整理的当前需求快照，或应用真正提供的公开结构化用户事件。quote 对其验证的是“在所提交需求下可行”，不是替模型证明它已经正确理解自然语言修订。评分器仍用图外最新条件检查语义正确性。若给所有组增加结构化用户 patch，应声明这是公共输入 Interface 改进，并另标协议；不能悄悄消除本来要测的 revision 理解能力。

quote registry 需保存 canonical SKU 集、policy hash、requirements hash、revision、来源与计算明细。相同输入幂等；不同需求或政策不复用旧 quote。`stage_result` 检查 quote 属于当前 session/revision，再物化现有 payload，以尽可能复用现有独立评分口径。审计任务保留同一份 totals/flagged 提交契约；可以给其 evidence report 注册稳定 ID，但不要在本次偷偷加入直接求出账务真值的工具。

报价工具可以内部读取权威公开资料，但应记录真实读取事件；它返回的来源是 runtime 产生的 provenance，不能从模型自报的 evidence_ids 无条件继承。若工具直接使用内部数据，需明确改变“模型必须亲自读每份原始资料”的要求为“决策使用了由权威工具计算并带来源的结果”；不能为了凑现有评分静默伪造 read_packet 轨迹。

当前固定算术限制继续披露。采用多个报价对象及批量比较以后，不需要鼓励模型用 10^15 打包排序或构造超大算术表达式。[算术与草案基线](fair-v2-baseline/src/loop_lab/hard/world.py#L11)

**关于精确优化工具：** 如果未来新增直接枚举或求解完整组合的公共 optimizer，四组都可以使用，但它改变了问题：测量的是“模型调用确定性求解器的能力”。应作为单独工具条件，不与本次 quote/compare 的结果合并，以免把规划难度完全外包后仍声称验证了架构的推理优势。

## 4. 四方法的可防御优化

### tools

保持主循环、全部公共业务工具和公共 Interface 说明。共同 BASE 可以说明金额单位、版本、输入类型、调用限制、来源和提交契约；不要加入“先按平台分组、逐步枚举、用哪些启发式搜索、何时重规划”的完整采购流程。这些属于待测专业方法。

可优化工具命名、参数形状、错误信息、报价绑定、批量能力和有限结果裁剪。这些改善必须同时给其他三组。报价工具返回“某候选超预算”等业务事实是允许的；工具描述变成一套只服务 tools 的流程包则越界。

### eager

使用一个 `KnowledgePack` 的全部正文，统一顺序、统一模板；保持方法库大小声明。可对正文做一次面向实际失败的重写：强调 quote 引用、候选比较证据、修订后失效、覆盖与终止条件。但正文不得包含 P2/R2 的正确 SKU、特定金额、task ID 或 seed，也不得从 score 输出生成专题答案。

同一正文必须给 skills；eager 不另获“减少一步加载”的专门补丁或摘要。发布时记录正文整体 hash 和各 Skill hash，使“同正文、不同加载时机”可核验。

### skills

仅先给名称/清晰描述及 `load_skill`；正文与 eager 字节相同。description 提供触发范围、输入对象和与邻居的区分，不能提前泄露整套方法，使按需组实际上已经预加载核心算法。

可以让 `load_skill` 支持一次加载多个确定相关名称、同 session 去重、返回已加载状态，并记录真实加载成本；这仍是模型选择按需加载。若加载由宿主按任务类型自动决定，应另外标为 preload/prefetch 条件，不能继续称为本次模型自主选择的 skills。

知识优化目标是明确但有界的流程：候选与报价绑定、覆盖的证明、何时停止探索、发现 partial 时如何诚实收尾。不要用“必须加载”强制每个任务都支付 Skill 成本；未加载的 run 仍保留在 skills 组并记录实际使用。

### multi

改为完整可交付工作包，主 Agent 保有全部公共工具，避免为了迫使其委派而剥夺工具。workers 使用同样公开事实与对应专业正文；不会有独占业务政策。可交付子任务示例：

- 采购：在一个兼容平台范围内，完成硬过滤、组合搜索、报价比较，返回该范围的最佳已验证 quote 与覆盖/未完成说明；不要再让 policy、procurement、audit 对同一判断反复传话。
- 当前数据的 base/battery 不存在 universal，可按其平台 A/B 划分完整解空间；通用配件在各平台内参加组合。未来如果允许全部产品 universal，需事先定义该组合的唯一归属或统一去重，不能重复当成两个独立方案。
- 审计：每个 branch 完整完成该分支所有 ref 的版本选择、金额与异常计算，返回 branch report。汇总者检查覆盖和版本一致，不能把每个子步骤再拆成专家。
- 多轮修订：平台从 any 改 B 后，A worker 结果变为不适用；B worker 接到 revision delta 及尚有效的材料。无需重新问与最新范围无关的专家。

worker 以 `(task_id, scope_id)` 持久存在，稳定的 graph/thread/checkpoint；调用第二次是继续同一 worker，而不是新的 UUID 专家。相同 scope 不并发推进两个 revision；过期 revision 的结果不可流入当前选择。工作者持久化是实现能力，模型效果仍需实测。

建议 child 的 Interface 为 `investigate(scope_id, request_revision, assignment)`，而不是角色名称；scope 必须来自公开资料/当前约束。结构化返回：

```text
scope_id, request_revision, policy_version
status: complete | partial | infeasible | budget_exhausted | error
best_quote_id | audit_report
evidence_ids: runtime-verified source IDs
coverage: considered IDs / exclusions / unresolved work
remaining_uncertainty
```

`complete` 与 coverage 是待核验的主张，不能因为 schema 合法就判最优。采购最终比较引用真实 quote；审计汇总检查所有 branch 和字段，独立 oracle 在图外核对。

## 5. 允许 multi 不委派，并保留四组可比性

**主应用对照应允许。** 如果主 Agent 判断任务无需分开，应直接处理，不通过“每题至少创建一次 child”制造纯开销。四组比较的是预先声明的可用能力与执行政策，即 assigned method 的全量结果；不能事后将 multi 的零委派成功移到 tools 组，或只保留真实委派的失败/成功来计算主分数。

每条记录至少写：

```text
assigned_method: multi
actual_topology: single | delegated_serial | delegated_parallel
worker_count, delegated_scopes, child_calls, main_calls
shared_budget, reserve_for_main, worker_stop_reasons
```

主表保留按分配组汇总，附表提供实际拓扑与委派采用率。actual_topology 的子集比较是描述性结果，因为模型选择是否委派会受任务难度影响；不能从这种事后分层直接归因拓扑效应。

**如果要回答纯拓扑问题，另做预先定义的机制对照。** 对确有两个以上独立完整分支的任务，冻结分支清单和相同知识，比较单循环处理所有分支与确定性 fan-out；此时强制分支 worker 是实验处理，不是给主四方法偷偷加硬要求。审计 family 最适合；采购按平台划分也可，但先限定好互斥且覆盖完整解空间的条件。资格在运行前依据公开数据定义，不能看到模型表现后挑题。

这样能同时避免强迫无用委派，也避免把四个配置名误当四种实际拓扑。当前 HARD-SPEC 本来已明确“子 Agent 若未被使用，不把配置名称当实际拓扑”；v2 应让报告兑现这条规则。[冻结规格](fair-v2-baseline/HARD-SPEC.md)

## 6. 共享预算与主 Agent 收尾

保留当前 `ModelRun`/`Budget` 的总账，增加内部 role-aware budget lease。worker 额度是一份总预算中的使用权，不是新的预算；每次真实模型调用都在同一锁下检查和记账。当前仅有总额度，没有给主 Agent 留收尾空间。[总账](fair-v2-baseline/src/loop_lab/models.py#L118)

可以先冻结一个明确的起始策略：24次任务额度内预留主 Agent 4次用于比较/纠错、stage 与最终 Answer；每个新 worker 的上限不超过6次，也不得超过扣除主 reserve 后按未完成分支分配的额度。这里的4/6只是可测试的初始配置，不是已证明最优值；主 Agent不需要委派时仍可使用全部预算。

worker 停止时返回已完成 quote/report 与 `partial/budget_exhausted`，而不是将局部 TaskBudgetExceeded 直接抛穿整个主图；主 Agent可用剩余额度完成已有证据上的工作，或者报告不能保证完成。**SharedBudgetExceeded、unknown usage、源码身份改变等基础设施停止仍应上抛**，不能当普通 worker partial 后继续偷偷发请求。

worker 的稳定 session 与预算必须一起持久化；重启或继续调查不重置额度。主 reserve 只能防止 child 抢光额度，不能保证主 Agent 一定会正确使用剩余调用，须在报告中保留实际交付失败。

## 7. Seam、依赖与测试

**External seam：** `HardExecution.run/advance`。批次 runner、应用 caller 和会话测试都跨同一 Interface；隐藏复杂度后 caller 不需要知道 worker checkpoint 或预算 lease。

**Internal seams：** 公共业务工具的报价/比较/提交 Interface；模型决策 Interface；本地存储 Interface。它们确有真实实现与测试 stand-in，不为假想未来创建插件体系。

| 依赖 category | 处理方式 | 有意义的验证 |
|---|---|---|
| In-process：政策算术、quote排序、修订/工具契约校验 | 保持纯计算；公开政策独立实现；oracle只在图外 | 手算边界与独立 oracle交叉核对；同效用/同现金字典序；退役政策、混合revision、错误quote引用拒绝 |
| Local-substitutable：SQLite检查点、quote registry、事件与草案文件 | 当前本地存储；测试用tmp_path真实SQLite/文件，避免只mock字典 | 重启后quote仍能引用；worker继续同scope；原子回执、幂等、过期结果、并发同scope拒绝 |
| True external：Codex CLI模型调用 | 注入现有模型 Interface；production CodexBridge/ModelRun 与 scripted adapter 两种 | 用脚本模型推进真实LangGraph，验证传递/加载/预算/终止；另用真实模型实验验证实际效果 |

优先行为测试：

1. `run()` 对同一模型报价产生的quote使用真实金额，禁止“SKU A + Quote B现金”的提交。
2. eager正文与skills实际加载正文hash一致；tools输入没有专门方法正文；共同工具schema一致。
3. 对 task 的 `expected/latest_case` 注入独特哨兵值，任何模型输入/业务工具输出都不能出现；quote只能从公开资料和本轮显式需求取得数据。
4. 同一worker连续收到revision0/1，第二次保留已有材料但收到新需求；旧revision quote不能提交。
5. worker用尽其lease后主Agent仍有预留额度执行stage+final；重新委派/恢复不能获得新总额度。
6. multi可零委派且记录actual_topology=single；有界分支真实委派记录serial/parallel，不靠配置名推断。
7. worker partial不得伪装complete；缺分支不能被汇总过程静默忽略；unknown usage仍停止后续外部调用。
8. quote compare只在传入候选集排序，不创建新候选，不调用oracle。最优性由独立评分判定。

测试应落在这些 Interface，而不是绑定内部 prompt 某一句中文、UUID样式或具体helper调用次数。已有测试中真正覆盖公开算术、revision、独立oracle和事件语义的部分保留；只在新行为测试替代了旧浅层实现测试后再讨论删除，并遵守工作区备份要求。

## 8. 实施取舍与复测解释

本设计的 leverage 在于：批次 runner 与交互 caller 均只使用 session；四方法共用报价身份、证据、版本、草案与额度约束。Locality 集中在 `HardExecution` 的策略装配及 `HardWorld` 的业务工具，不把重试/收尾补丁散落到四套prompt。

代价是 HardExecution 内部会更深，需要有清晰私有模块；如果一次把所有建议都实现，v2与v1的差异将同时包含公共工具、知识、委派和预算策略，不能说成绩变化由某一个因素单独造成。推荐保留：公共工具统一版本；eager/skills同知识版本；multi新策略版本；每组方法契约hash，以及可做必要消融的配置。

已见P2/R2用于检查具体失败是否恢复，属于开发回归；新的同族seed/修订/分支数据用于冻结后的复测。新一轮仍必须报告未启动、用量未知、预算内失败与有效配对覆盖，不用部分完成批次给总体冠军。

优先顺序：公共 quote/compare/stage 与真值隔离 → 四组知识契约 → 完整分支worker/持久化/预算 → 真实小批接线 → 冻结新案例平衡复测。若前三类公共工具已使四组饱和，应如实报告工具改善消除了当前难度差异，再单独设计更能检验知识或上下文的任务，不从现有结果硬造方法排名。
