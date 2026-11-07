# Agent Loop Lab

**最终结果：[四种优化方法的完整公平复测](FAIR-RESULTS.md)。** 12道独立准备的新题、四方法、每题两次，共96/96首次通过；正式原始证据独立验收无不一致。工具单循环平均tokens最低，按需相对全量在较大手册库的同题子集省31.5% tokens，多Agent在6/24次实际委派且没有观察到质量增益。

Git 版本保留实验代码、锁定依赖、测试、合成题与冻结摘要、评估报告和核验脚本。`runs/` 和 `runtime/` 是本机原始证据（模型请求/回复、事件、数据库等），不纳入 Git；文档中指向这些目录的链接仅在保留原始证据的工作区可打开。已经使用过的合成留出题可用于复现，不应在后续调优时再当成全新的盲测题。

过程入口：[冻结方案](FAIR-RUNNING.md)、[审查与修复](FAIR-REVIEW.md)、[开发阶段额度阻塞时的历史检查点](FAIR-STATUS.md)。额度已恢复，正式运行已经结束。

**上一轮诊断：[困难任务实践与认知变化](HARD-RESULTS.md)。** 旧批次启动33/48次后保护停止；其失败用于设计改进，不作方法排名。实现说明见 [HARD-README](HARD-README.md)。下文简单任务结果保留为更早的证据。

用真实模型对照实验判断：何时保留独立 Agent，何时共享上下文，以及专业知识应按需还是预先加载。

这是独立的研究实验与可运行应用骨架。业务数据均为合成订单，动作写入本地 SQLite；没有连接客户数据、付款、真实退款或生产系统。冻结设计与验收见 [SPEC](SPEC.md)，选型背景见 [实现比较](../docs/agent-loop-alternatives-2026-11-06/README.md)。

真实重复实验发现并修复了一项桥接批次上限缺陷；旧记录和新协议分开保留，见 [v2 修正与补充实验](CORRECTION.md)。

**已完成的实际结果与推荐配置：[RESULTS.md](RESULTS.md)。** 当前合成业务范围默认tools主循环；Skills和multi保留为可比较选项。

## 四个可执行候选

| 名称 | 实现 | 要检验的问题 |
|---|---|---|
| multi | LangChain 协调者 + shipping/returns 独立循环；专家保留原始请求；主 Agent 执行动作 | 独立上下文和交接是否值得 |
| tools | 单主循环 + 业务工具 | 强模型与可靠工具本身能完成多少 |
| skills | 同一主循环 + Skill 元数据 + load_skill | 按需专业方法能否提升完成质量 |
| eager | 同一主循环 + 同样的 Skill 正文全部预加载 | 知识增益与渐进披露收益能否分开 |

四种方案共享用户事实、业务工具、审批、评分、模型和单任务调用上限。只有三类只读业务工具和一个写工具，本轮不以工具搜索增加额外复杂度，也不声称验证大工具库发现能力。

## 本地运行

最小应用入口：`bash demo.sh`。默认用 tools 主循环执行 d03 条件退货；它调用真实模型，动作只落在本地合成数据库。可传任务ID和新输出目录，例如 `bash demo.sh h03 runtime/my-demo`。

```bash
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen loop-lab tasks
```

测试在真实 LangGraph、工具与 SQLite 边界运行，只有模型边界使用脚本替身。它们验证接线、策略和评分器，不用于推断模型效果。

真实模型使用已登录的 Codex CLI。无需读取或复制认证令牌；需先有正常的 `codex login` 状态。CLI 自有 shell、apps、记忆、外部 Skills 等能力关闭。模型收到序列化的消息和业务工具 schema，返回动作 JSON；LangGraph 执行实际业务工具。模型只是决策者，不能读取评分答案或直接操作数据库。

```bash
# 新目录，保留完整开发集四方案对照；会消耗模型计划额度。
uv run --frozen loop-lab run --output runs/my-dev \
  --split dev --variants multi tools skills eager --reps 1 --jobs 3 \
  --max-calls 160 --max-tokens 1800000

# 单个可阅读业务案例：确认到货日期后才退旧订单。
uv run --frozen loop-lab run --output runs/my-demo \
  --split dev --tasks d03 --variants skills --reps 1 --jobs 1 --max-calls 12

# 从保留的模型/任务/源码身份恢复所有当前待审动作。
uv run --frozen loop-lab resume runs/my-dev/d07-skills-0 --decision approve

# 根据保存的每次运行生成逐题报告；不重新调用模型。
uv run --frozen loop-lab report runs/my-dev
```

每次实验写入新目录。人工恢复属于该会话的后续操作，不改写已冻结的原试验 `result.json` 与批次 `results.jsonl` 得分。恢复会复用原模型配置、累计调用计数和源码身份；源码已变、身份不同或无待审动作时拒绝恢复。

## 数据、证据和验收

- `manifest.json`：模型/effort/依赖/源码摘要、计划执行顺序、预算、实际用量、未运行清单。
- `tasks.json`：该批次的合成输入与独立预期；不会作为模型请求内容。
- `results.jsonl`：每个已启动任务的输出、真实工具事件、回执、失败原因、延迟与 tokens。
- 每次任务下的 `model/`：逐模型调用的请求、CLI stdout/stderr 与用量账本。
- `world.sqlite` / `checkpoint.sqlite`：业务回执与审批恢复状态。
- `REPORT.md` / `summary.json`：逐方案、逐任务、配对任务聚类比较。预算未完成、用量未知或源码改变时，不给出完整架构比较结论。

判分直接核验结构化事实的 key/value/source 与权威工具返回。子 Agent 的摘要不能自证；条件动作还要证明对应事实已进入决策上下文、且在动作前由权威工具取得。实际写入集、回执、拒绝后重试和多余动作都参与判分。

8 个开发案例用于接线与候选筛选；16 个确认案例在实现冻结后使用。它们均由本实验作者构造，属于保留合成任务，不是独立真实业务样本。自然语言回复质量、客户转化、生产并发与大工具库不在此轮结论范围。

## 预算与代价口径

模型为 `gpt-6.1-sol`，默认低推理配置；每个任务最多 12 次模型调用，主 Agent 与专家共享。批次并行数可配置，按轮换顺序交错候选。

调用次数上限可直接执行；token 采用已知用量加在途预留，每调用预留 50,000。CLI 不提供本实验可验证的服务端 token 硬上限，因此此处是停止阈值，不能保证任何情况下绝不超额。用量缺失即停止新请求，保留部分输出与未执行项。单任务用尽 12 次属于该方案在等预算下失败；整批预算耗尽则列为不完整实验。

CLI 启动、认证及自有基础上下文包含在耗时与用量中。这不是原生 API 的绝对性能测试。登录态计划消耗没有核实的逐次美元价格，因此 `cost_usd=null`，不把它写成零成本。

## 工程验证与来源

本轮采用 TDD 公开边界测试：审批后才写入、重复动作幂等、跨用户拒绝、过期拒绝、事实证据、额外动作、审批目标、条件时序、拒绝后重试、跨进程恢复、多 interrupt ID 恢复、共享预算及未运行记录。

独立 Standards/Spec 审查先后发现并修复了：动作后补查条件仍通过、拒绝后重试漏判、全局预算和单任务预算混淆、用量未知仍继续调用、恢复身份/预算重置、多审批中断与在途预算调度问题。真实效果实验在这些修复后冻结。

设计参考：[LangChain Skills 示例](https://docs.langchain.com/oss/python/langchain/multi-agent/skills-sql-assistant)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[原文迁移 kit](https://github.com/anthropics/single-agent-loop-kit)。本实验没有复制参考 kit 的业务数据或将其示例成绩当作自己的结果。
