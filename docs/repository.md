# 代码与资产地图：哪些文件对哪种证据负责？

读一条研究结论，先找负责**执行、记账、评分、再核验**的不同文件。仓库保留三代实现，是为定位前后行为，不把当年的装置偷偷换成今天的最佳实现。当前资料导航如下：[实验目录](studies/README.md) · [历史计划与过期状态](records/README.md) · [公开数据](../data/README.md) · [来源](sources.md)。

| 边界 | 入口与具体职责 |
|---|---|
| S1 客服 | [tasks](../src/loop_lab/tasks.py) 构造合成请求；[domain](../src/loop_lab/domain.py) 管业务事实与写入；[runtime](../src/loop_lab/runtime.py) 决定模型能见的工具和会话；[skills](../src/loop_lab/skills.py) 读取[两份实际手册](../skills)；[schema](../src/loop_lab/schema.py) 约束回答／动作格式；[scoring](../src/loop_lab/scoring.py) 核对输出和状态。 |
| S1 运行与共享设施 | [cli](../src/loop_lab/cli.py)、[experiment](../src/loop_lab/experiment.py)、[report](../src/loop_lab/report.py) 分别负责命令、调度、汇总；[models](../src/loop_lab/models.py) 记录真实 CLI 请求／用量并管预算；[integrity](../src/loop_lab/integrity.py) 对代码、依赖和知识计算指纹。[demo.sh](../demo.sh) 是实际调用模型的入口。 |
| S2 困难诊断 | [hard/tasks](../src/loop_lab/hard/tasks.py) 定义采购、修订、审计；[knowledge](../src/loop_lab/hard/knowledge.py) 构建 3/24 项方法库；[runtime](../src/loop_lab/hard/runtime.py)、[world](../src/loop_lab/hard/world.py) 隔开上下文、业务状态与工具；[oracles](../src/loop_lab/hard/oracles.py)、[scoring](../src/loop_lab/hard/scoring.py) 求真值与核对提交；[run](../src/loop_lab/hard/run.py) 处理预算、中断和报告。 |
| S3 冻结对照 | [fair/study](../src/loop_lab/fair/study.py) 产生开发题、封存、分配和运行；[runtime](../src/loop_lab/fair/runtime.py)、[client](../src/loop_lab/fair/client.py) 管主子会话与共享预算；[knowledge](../src/loop_lab/fair/knowledge.py) 维护同源方法库；[world](../src/loop_lab/fair/world.py) 只对模型提的候选报价并核对提交，不能从 gold 取解；[scoring](../src/loop_lab/fair/scoring.py) 验收最终交付。 |
| 不依赖生产评分器的核验 | [audit_fair_evidence](../analysis/audit_fair_evidence.py) 从原始公开题面独立重算真值、核对 CLI／后台事件；[resource comparison](../analysis/fair_resource_comparison.py) 仅对同题比较用量；[progress](../analysis/fair_progress.py) 是监控，不替代终验；[combine](../analysis/combine.py) 防止不同客服协议混算。 |
| U1–U4 与 K1 | [fieldwork](../fieldwork/2026-11-08/README.md) 内每项都有源码 pin、接线、协议或设计说明、结果与复跑命令；[共享传输](../fieldwork/2026-11-08/model_bridge.py) 把模型决定交回各自的工具与循环；[跨项目账本核对](../fieldwork/2026-11-08/verify.py) 读原始收据。上游不作为本包的运行依赖。 |
| 发布与复现 | [公开证据检查](../scripts/check_evidence.py) 仅读公开材料；[白名单数据导出](../scripts/export_observations.py) 从本地原件导出受限列；[upstreams](../scripts/upstreams.py) 在指定缓存取固定 Git 对象；[CI](../.github/workflows/verify.yml) 无密钥运行公开核验、测试和 lint。 |
| 测试、冻结、派生表 | [tests](../tests) 包含业务边界、审批、预算、恢复、数据依赖和评分等回归；[reports](../reports) 是机器摘要，不是原始模型轨迹；[reviews](../reviews) 包含开发审查、封存题／种子与源码指纹；[fair-v2-baseline](../reviews/fair-v2-baseline) 是旧实现与测试的只读快照，不是第四个可修改运行时。 |

**客观审视。** 三种资料的强度并不相同。冻结对照能检查同题同预算与独立验收，但题量有限且共享工具很强；上游机制对照能定位委派的开销，却不能隔离所有提示与接口差异；故障注入能证明某条保护可以失效，不能估计真实事故率。工具查询成功不保证最终表述完整，测试很多也不保证业务保护覆盖全部组合。这些限制决定下一轮应该加什么题，而不是把观察结果说成平台规律。

**工程上的保留与取舍。** S3 的公开指纹涵盖整个 src、两份 Skills、项目依赖与锁文件。更改其代码就不能再称同一个冻结实现，因此公开版改造集中在读者入口、数据导出、可搬迁的上游缓存和独立核验；没有为了目录整齐而改写已测模型逻辑。早期 CLI 的成本和时延含账户、进程启动与排队；本机原始记录不能公开，公开 CSV 与摘要只能做较弱复核。许可证、测试与公开仓库均不代表生产部署或第三方背书。

[逐文件目录](files.md)列出本版每一项已跟踪文档、代码、合成材料和配置，供审阅完备性或查找历史实现；日常阅读不必从清单开始。
