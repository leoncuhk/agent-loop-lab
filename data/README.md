# 公开数据：从结论走回每一次观察

[observations.csv](observations.csv) 是三批**逐次**记录，不含自然语言输入输出、工具参数、身份资料或数据库。每行由批次、合成任务 ID、方法和重复号唯一标识。在 [导出器](../scripts/export_observations.py) 中只有固定字段能离开原批次，可用 [输入 SHA-256](source-hashes.json) 识别来源。哈希不能替代缺失的原始证据。

| 字段 | 意义 |
|---|---|
| batch / task / method / rep | 原版四组客服、修复后的三组客服、未完成的困难批次分开统计；rep 从 0 起 |
| outcome | pass 是原任务契约通过（有些客服题正确等待批准也算通过）；business_fail 是有效交付未达标；task_budget 是本任务用尽预算；request_timeout 是外呼超时；shared_stop 是共享保护停止 |
| calls / input_tokens / output_tokens | 原运行器记账；usage_complete=0 时只代表已知部分，不能换算成完整费用 |
| skill_loads / delegations | 原轨迹中对应事件列表的长度；不是理解了手册的评分，也不是独立工作者产生价值的评分 |

客服确认组 64 行、修复后回归 48 行、困难组 33 行。困难组另有 15 行原计划未启动，不编造成失败观察。正式留出实验的 96 行独立审计已在 [trials](../reports/fair-v2-final-independent-audit.json) 中公开，不再复制。开源实跑各项目的逐格／逐组数据见[现场记录](../fieldwork/2026-11-08/README.md)。aidless 的公开记录是分组摘要，不是逐次对话；Haty 的作者数据是从公开 CSV 复算，也不是本机重跑 Claude。

无模型、无需安装依赖：在仓库根目录执行 `python3 scripts/check_evidence.py`。它验证分母、重复、分组计数、部分用量与代码和封存任务的指纹；不会从这些派生表还原模型发过什么。能接触原始记录时，可另做[逐动作、逐回执和独立求解验收](../analysis/FAIR-EVIDENCE-AUDIT.md)。
