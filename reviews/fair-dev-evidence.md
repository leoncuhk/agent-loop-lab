# fair-v2/v3 开发证据独立验收

日期：2026-11-07。范围仅为 `runs/fair-dev-v2`、`runs/fair-dev-v3` 的已完成开发运行、各自源码快照及 `reviews/fair-v2-freeze.json`。本次没有调用模型，没有读取留出参数、sealed 任务或正在运行的 holdout 题目，没有修改源码、配置或原始实验记录。

**验收结论：两轮各四方法均为 3/3 完整通过；独立业务重算、原始 CLI 用量、实际拓扑与交付链相互一致，未发现阻止使用 v3 作为冻结开发证据的问题。全部 24 个 trial 实际都没有委派；这些结果不能证明多上下文机制收益。** v2 与 v3 的源码版本不同，不能合并为一个版本的确认实验。

## 实际执行的独立检查

运行只读 `python3 -B` 检查脚本，退出码 **0**，全部断言通过。没有调用项目的评分器、报价实现或 oracle 来替代独立计算。

- 从两个 `tasks.json` 的公开 packets 独立实现整数分计价、仓库折扣/运费、抵扣、库存/平台/日期/仓库/预算约束；穷举开发题全部组合，按 utility、现金、SKU 顺序重求最优。与提交 payload、quote、独立预期三方核对。
- 审计先按 cutoff/kind 筛选，再去重事件 ID、按 branch/ref 取最大 seq，独立生成 approved 事件集合、总额及 pending 异常；同时核对实际提交记录的事件 IDs。不是只比较金额。
- 核对最终 revision、完整目标快照、真实 receipt、全部规范化 facts、证据覆盖，以及分析轮次没有提前提交。
- 逐条解析 **105** 份原始 CLI stdout，核对 `turn.completed.usage` 与 `usage.jsonl`、attempt result、trial 和 manifest；核对模型请求文件、开始/结束账本的数量与 request ID，没有仅复述汇总报告。
- 将模型输出的 **80** 次 `assess_candidates` 参数及 **8** 次 `audit_selection` 参数分别与工具事件作多重集合精确匹配；独立复算 **944** 份已登记 quote 的金额、效用和可行性。
- 核对 24 次最终提交前引用已在模型所见 tool 内容中出现；最后一次 CLI 决策均为独立 stage 调用，`final=null`。随后只有 `host_finalization`，没有新增 CLI 请求或 usage。

## 重算结果与资源

表中输入含 CLI 自报的 cached input 子集，不再把 cached 数重复加到输入；输出中的 reasoning 子集同样不重复累计。所有请求用量均已报告，未知用量、技术恢复及 worker 调用均为 **0**。这里核验保存的 CLI 计量记录，没有将其称为独立服务端账单或美元成本。

| 开发轮次 | 方法 | 独立通过 | 模型调用 | 输入 tokens | 输出 tokens | 合计 tokens | P2/R2/A0 调用 |
|---|---|---:|---:|---:|---:|---:|---|
| v2 | tools | 3/3 | 13 | 164,999 | 3,529 | 168,528 | 3/7/3 |
| v2 | eager | 3/3 | 13 | 226,326 | 4,198 | 230,524 | 3/7/3 |
| v2 | skills | 3/3 | 13 | 162,239 | 3,414 | 165,653 | 3/7/3 |
| v2 | multi | 3/3 | 13 | 220,444 | 4,112 | 224,556 | 3/7/3 |
| **v2 合计** | | **12/12** | **52** | **774,008** | **15,253** | **789,261** | |
| v3 | tools | 3/3 | 13 | 161,178 | 3,151 | 164,329 | 3/7/3 |
| v3 | eager | 3/3 | 14 | 239,115 | 4,274 | 243,389 | 4/7/3 |
| v3 | skills | 3/3 | 13 | 177,950 | 3,747 | 181,697 | 3/7/3 |
| v3 | multi | 3/3 | 13 | 167,086 | 3,359 | 170,445 | 3/7/3 |
| **v3 合计** | | **12/12** | **53** | **745,329** | **14,531** | **759,860** | |

总计 **105 次调用、1,549,121 tokens**。每个 trial 均只有 attempt-0，首次结果与恢复后结果相同；每组均覆盖相同 P2/R2/A0，没有缺项或重复项。

| 开发题 | 独立重算的最终结果 | 24 个 trial 中对应结果 |
|---|---|---|
| DEV-P2 | B2/C6/T2；现金 9,958；utility 168；全空间最优 | 两轮四方法全部一致 |
| DEV-R2 | B2/C4/T6；现金 11,649；utility 182；最新预算 11,649、平台 B、截止 2026-11-19、无排除仓库 | 两轮四方法全部一致，revision=2 |
| DEV-A0 | east=5,100、west=4,400、north=7,650；异常 east/I05、north/I01、north/I11 | 两轮四方法金额、异常和实际事件集合全部一致 |

原始入口：[v2 manifest](../runs/fair-dev-v2/manifest.json)、[v2 trials](../runs/fair-dev-v2/trials.jsonl)、[v3 manifest](../runs/fair-dev-v3/manifest.json)、[v3 trials](../runs/fair-dev-v3/trials.jsonl)。每个 trial 下的 `attempt-0/model/` 保留逐次 request、stdout、usage 和请求账本。

## 报价、实际拓扑与 host 收尾

**金额由工具计算，候选来自模型。** 80 次报价调用的 SKU 列表与 revision 均逐条来自原始模型决策；模型参数没有手填现金或 utility。所有保存的 quote 的 SKU 集合都可追溯至模型提交的候选；944 份记录的定价独立重算一致。所有报价结果仍声明 `global_optimality=not_established`。本次验收在图外穷举得到最优，只用于核验结果，不能倒推为运行时工具替模型枚举了全空间。

例如 [v3/P2/tools 的最后一次原始模型输出](../runs/fair-dev-v3/DEV-P2-tools-r0/attempt-0/model/call-003-main.stdout.jsonl) 仅调用 `stage_quote` 并传入已见 ID；对应 [result](../runs/fair-dev-v3/DEV-P2-tools-r0/attempt-0/result.json) 由真实草案回执形成最终 facts。24 个 trial 均按此链结束；105 份 usage 就是 105 份请求/原始响应，24 次 host 格式化没有被多算为模型调用。

**实际拓扑全部为 single。** 所有 usage role 都是 main；`delegation_spans` 为空，工具事件没有 delegate，worker_count/worker_calls 均为零。multi 组主循环实际按需加载了 bundle-planning 或 ledger-snapshot；skills 的 R2 还加载 revision-control。eager 的正文预加载不表现为 load_skills 事件，不能把其空 `skills_loaded` 误解为没有方法知识。

因此，开发证据验证了 multi 配置在允许零委派时可以完成这三个任务，未实测其 worker 在真实模型上的协作贡献。没有理由在已经完成的开发题上为了制造委派而改动冻结方法。

## 版本与结论边界

- 两轮 `tasks.json` 完全相同，settings 与 small/large knowledge hashes 相同；各自 source-snapshot 的所有文件哈希均匹配本轮 manifest。
- v2→v3 仅 `fair/world.py` 与 `fair/study.py` 的源码哈希不同。两轮均无 source_updated 事件，因此此前资金更新引用缺陷没有在这些固定开发题中被触发。
- 当前冻结源码逐文件匹配 freeze receipt，v3 manifest fingerprint 也完全相同；freeze 保留的 v3 manifest/trials SHA-256 均与现文件一致。未打开 sealed 数据来重算其 hash。
- v3 manifest SHA-256：`f5596ba9dbe6ec4905c4bdfe831212d0a1374e66de13d00ad48d68fc9dbf9c9b`；v3 trials SHA-256：`793097afbc30f2e1ea0a3a0c8b0b39a58c68775186a7805a0fa7c66ee7e352ed`。
- 这是三个已见合成开发题的最终交付验收，不验证每轮中间分析正确性、真实客户效果、自然语言质量或普遍架构等效。未将两轮相同任务当作六个独立业务场景，未据调用/token 小差异宣布冠军。
- 本次没有重跑 pytest/ruff；“48 tests 通过”不作为本报告独立执行的证据。正在运行的留出实验不在本次验收范围内。
