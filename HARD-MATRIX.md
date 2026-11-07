# 困难实验矩阵（正式模型运行前冻结）

日期：2026-11-07。开发接线题为P0、R0、A0；确认题为P1–P4、R1–R2、A1–A2。所有案例由作者构造，模型看不到oracle，非真正独立业务留出集。

| 批次 | 案例 | 方案 | 库/表示 | 重复 | 目的 |
|---|---|---|---|---:|---|
| core-small | P1–P4、R1–R2 | tools/eager/skills/domain | small/compact | 2 | 可行与最优、领域交接、多轮条件保持 |
| library-large | P1–P4 | eager/skills | large/compact | 1 | 与core同题比较知识库规模效应 |
| audit-raw | A1–A2 | tools/audit_single/context | small/raw | 1 | 流程指导与独立上下文 |
| audit-compact | A1–A2 | tools/audit_single/context | small/compact | 1 | 无损字段投影与上下文隔离的交互 |

正式矩阵为68次任务运行。若达到600模型调用/1200万tokens停止阈值则保留未完成项。代表性失败的额外复测必须另记录，不替换这些结果。

说明：audit_single与context都预加载同一份ledger-snapshot方法；context工作者没有额外Skill菜单。tools是知识消融，不用于单独归因上下文隔离。raw/compact保留同一行集合与所有业务字段，compact只移除observability。

所有审计组由宿主一致预加载廉价case简介，避免只有context享受免一次模型查case的优势；政策与账目仍需检索。context的分支拆分由case固定清单决定，属于确定性fan-out，不声称模型自主发现了最佳拆分。接线阶段还将草案输入改为普通JSON对象，避免“JSON字符串内再嵌JSON字符串”的转义负担影响方案比较。

独立审查已用另一个实现重算P1、P3和A1，结果与oracle一致。多轮指标只证明最终条件保持与提交时机，不宣称前两轮分析都已完整正确。初始计划将每任务预算定为24次，所有用户轮次、主Agent和子Agent共享；模型请求并发固定上限4。

源码、任务、知识和评分每批写入哈希与完整快照。未经过新的未见案例验证的提示词调整，不称为泛化提升。
