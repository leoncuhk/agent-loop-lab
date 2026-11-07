# 公平复测证据验收

[audit_fair_evidence.py](audit_fair_evidence.py) 只用 Python 标准库，从已有文件读证据，输出 JSON 到 stdout。它不导入项目评分器、报价模块或 oracle，不调用模型，不改实验目录。进入项目根目录运行：

```bash
# 复核冻结回执引用的开发批次（12 项）。
python3 -B analysis/audit_fair_evidence.py runs/fair-dev-v3

# 运行中：以已 flush 的 trial 行诊断，永不形成最终验收。
python3 -B analysis/audit_fair_evidence.py runs/fair-holdout-v2 --partial

# 正式批次结束后：核对全部 96 项，或核实停止依据及未运行项。
python3 -B analysis/audit_fair_evidence.py runs/fair-holdout-v2
```

最终模式核对 freeze receipt、运行源码快照、正式的 sealed 文件哈希、公开运行题面与 gold 副本及固定知识库分配。仅在正式批次的 manifest 已关闭且 summary 已生成时才打开 sealed 原件；迁移目录后可用 `--freeze PATH` 和 `--sealed PATH` 指向原文件。部分模式不打开 sealed 原件，只对运行目录中已落盘 trial 对应的题求解。脚本本身不写报告；保存正式输出时使用新的审查文件，不覆盖实验记录。

| 退出码 | 含义 |
|---:|---|
| 0 | 该模式的检查通过。只有 `final_acceptance=true` 才是最终证据验收；`--partial` 即使覆盖全量也不会给出该标记。 |
| 1 | 证据不一致、数据无法核验或停止原因需要人工检查；看 `issues`。模型给出有效但次优的方案，若独立评分同样判为失败，不是证据不一致。 |
| 2 | 批次尚未关闭/报告尚未生成，不形成验收。 |

输出的 `first_attempt` 只计首次 attempt；`after_timeout_policy_cumulative_cost` 使用统一恢复后的最后一次结果，费用指标累计该 trial 的全部 attempt。两张表均保留各方法全量分母；`missing` 是未取得 trial 行的分配项，不当作已观察的业务错误。已核实的全局额度、认证或源完整性停止可以验收记录，但 `full_comparison_eligible=false`。未识别的停止原因不会当作合法提前结束。

检查包括：逐项预分配与重复、共享调用和一次超时恢复额度、原始 CLI stdout/用量/开始与结束账本/manifest 对账、实际 worker 调用角色和委派来源，以及 host 收尾是否另算模型请求。独立采购计算从公开目录穷举，用整数分重算每份登记 quote 的价格、目标、来源身份和最终最优解；审计从账本重建按 cutoff/版本保留的事件 ID，核对异常与分支总额。每个业务失败的原始原因仍保留在 `trials[].attempts[]`。

服务未返回用量的调用仍是未知，不能还原服务端账单；开始账本与 CLI usage 没有同一个 request ID，因此分别核验开始/结束 ID 集、角色计数及带编号的原始 CLI 请求和响应。脚本不评价中间推理、自然语言质量、生产效果或总体架构优劣。
