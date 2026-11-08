# 实验记录：协议、实现、结果

每项研究只在这里登记一次。下表的“通过”以该行的验收规则为准：账务核对、退货办理、SQL 查询和安全边界不能合并成一个总分。[首页](../../README.md)解释主要结论；本页用于定位实验、复做或追溯修正。

## 自建实验：从基线到冻结对照

| 研究／证据用途 | 任务、对照与判分 | 记录 |
|---|---|---|
| S1 · 客服基线与回归 | 条件退货、订单查询与审批。共同业务工具与状态评分；同版本 16 题，四组各 16/16。修复接线后三个单循环分别在已见 16 题上回归，各 16/16；按需组在其中 13 次主动读手册。两批不拼分数。 | [规格](../../SPEC.md) · [确认计划](../../CONFIRMATION-PLAN.md) · [实现](../../src/loop_lab/runtime.py) · [结果](../../RESULTS.md) · [接线修正](CORRECTION.md) |
| S2 · 困难任务诊断，不用于排名 | 组合采购、多轮修订与预算约束。核心对照预定 48 次、启动 33 次、完整通过 25 次；中断与业务错误分列。它帮助定位报价错配和委派后未提交，不能代表完整四组的相对成功率。 | [规格](../../HARD-SPEC.md) · [预定矩阵](../../HARD-MATRIX.md) · [后续验证计划](../../HARD-FOLLOWUP-PLAN.md) · [代码](../../src/loop_lab/hard) · [操作说明](HARD-README.md) · [失败与缺失](../../HARD-RESULTS.md) |
| S3 · 优化后的正式对照 | 固定模型、公共工具、总预算和评分，比较 tools/eager/skills/multi。冻结后取 12 道新题各跑两遍。独立程序重算报价／账务并验证实际提交；四组各 24/24，这是已完成的架构对照。 | [规格](../../FAIR-SPEC.md) · [运行协议](FAIR-RUNNING.md) · [实现](../../src/loop_lab/fair) · [正式结果](../../FAIR-RESULTS.md) · [逐次验收](../../reports/fair-v2-final-independent-audit.json) · [配对资源](../../reports/fair-v2-resource-comparison.json) |

开发过程另见[客服工程复核](REVIEW.md)、[四组实现审查](FAIR-REVIEW.md)和[开发批次独立核验](../../reviews/fair-dev-evidence.md)。[早期研究综合](../../HANDOFF.md)保留了当时如何从失败修正假设；现在这些题已经公开，再次使用属于回归。

## 固定上游的实跑

| 项目／待检验的变化 | 过程与结果 | 复做入口 |
|---|---|---|
| U1 · Anthropic 迁移 kit：移动执行者 | 两类条件任务、四个后台状态，每组 6 次；自由交接／附证据／主循环都通过，调用 25／25／16。另在上游原版 t11 检查后台动作和客户回执。作者既有统计另列。 | [协议](../../fieldwork/2026-11-08/kit/PREREG.md) · [原版应用追加计划](../../fieldwork/2026-11-08/kit/NATIVE-LIVE-PLAN.md) · [执行、判分与结果](../../fieldwork/2026-11-08/kit/README.md) |
| U2 · hatyibei 日语电商：补工具或折回 | 原专家／补物流工具／主循环分别做同三道题、每题两遍。业务结论均正确；预先规定的说明覆盖为 5/6、2/6、6/6；调用 42、32、28。作者公开数据的复算独立呈现。 | [协议](../../fieldwork/2026-11-08/haty/prereg.md) · [逐题证据与命令](../../fieldwork/2026-11-08/haty/README.md) |
| U3 · commerce-agents：验证批准与写入 | 模型运行导购和库存草案各一场。独立负例中，两张基于旧价 49 的调价草案依次获批，第二笔实际从 40 到 58（涨 45%）。验证的是执行时价格约束，不是架构成功率。 | [工程检查、后端状态与复现](../../fieldwork/2026-11-08/commerce/README.md) |
| U4 · aidless：验证门控及最终回答 | 三种条件（可发、被拒、未达阈值）各两遍，无／有手册均 6/6，调用 16／22。脚本化故障另证实：最终 verifier 可以扣下回答，但不会撤销先前写入。 | [协议](../../fieldwork/2026-11-08/aidless/PROTOCOL.md) · [执行与故障探针](../../fieldwork/2026-11-08/aidless/README.md) |
| K1 · LangChain SQL 附项：改变资料来源 | 两个领域问题，同一 Agent，通过 Skill 或普通工具读相同正文，或不提供资料。两个有资料的组均 2/2，无资料组未交付正确查询；每条 SQL 在两份数据上执行。 | [教程版本、实验实现与 SQL 验收](../../fieldwork/2026-11-08/langchain-reference/README.md) |

[跨项目原始回执的核验口径](../../fieldwork/2026-11-08/README.md) · [公开数据](../../data/README.md) · [复算与复跑](../reproduce.md) · [公开研究版验收](PUBLICATION.md)。

## 需要查看旧实现时

公平复测前的[完整审查基线](https://github.com/leoncuhk/agent-loop-lab/tree/20074a820c3cc6a3536b38cf69450d794b0f4611/reviews/fair-v2-baseline)保存在公开标签 `fair-v2` 的原路径。当前分支保留[当时的校验清单](../../reviews/fair-v2-baseline/manifest.json)。从完整 Git 检出可将快照恢复到独立目录，不改变工作树：

~~~bash
dest=$(mktemp -d)
git archive fair-v2 reviews/fair-v2-baseline | tar -x -C "$dest"
~~~

当时的[设计备选与审查原文](https://github.com/leoncuhk/agent-loop-lab/tree/51438671cc8fb8a1cabd9ff0546ce9fa89a6e633/reviews)、[运行中的状态和进度快照](https://github.com/leoncuhk/agent-loop-lab/tree/51438671cc8fb8a1cabd9ff0546ce9fa89a6e633/docs/records)在固定版本可读。它们解释当时如何决策，不替代 S3 最终验收。
