# 来源与归属

起点是 Diane Sarkis 的 [Building agents: When to fold subagents into your main agent loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)。它提出把部分执行决策折回主循环，并保留有独立价值的边界；不是在所有任务上宣判多 Agent 无效。本仓作者自行设计了客服、组合采购和账务实验，并对以下固定上游做了独立实跑。“官方”指来源，不代表官方认可本仓结论。

| 第一手来源（固定对象） | 在本仓中的身份 | 取用了什么／未测什么 |
|---|---|---|
| [anthropics/single-agent-loop-kit · c437962](https://github.com/anthropics/single-agent-loop-kit/tree/c43796287b83bec847db923f07131d1cc8de750d) | 原文配套迁移 kit | 原测试、模拟商店、作者日志和判分，以及两套原样应用。另建的三臂机制对照明确区分；没有逐项验证所有迁移模式。 |
| [hatyibei/zenn-articles · d8a1d81](https://github.com/hatyibei/zenn-articles/tree/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold) | 针对文章的独立电商实验 | 复算作者公开表格；在其三种特定循环中取三题、两次重复。未以本机新模型代称作者 Claude 判分。 |
| [anthropics/commerce-agents · fd4d592](https://github.com/anthropics/commerce-agents/tree/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf) | 同方向业务蓝图，不是同一迁移复现 | 原购物、商家循环，模拟后端、权限与批准流程。只运行了指明场景，无托管服务或真实交易。 |
| [aidless/agent-loop-safety · c504861](https://github.com/aidless/agent-loop-safety/tree/c504861ac65229a852deb44123210c675a67a9b8) | 直接借鉴文章及 kit 的安全循环 | 主循环、工具、门控、verifier 与评估器；故障探针不同于统计真实事故率。 |
| [LangChain SQL Skills 教程](https://docs.langchain.com/oss/python/langchain/multi-agent/skills-sql-assistant) | 额外的实现对照，不计入四项目 | 执行原版 Skill 定义、加载工具及中间件；SQL 在新 SQLite 数据上验算。[内容指纹与接线](../fieldwork/2026-11-08/langchain-reference/probe.py)。 |

自建架构对照用了 [LangChain](https://github.com/langchain-ai/langchain) 和 [LangGraph](https://github.com/langchain-ai/langgraph)，具体版本由 [uv.lock](../uv.lock) 固定。四个上游按它们各自的循环运行，没把它们改写成 LangGraph。其他工具或框架提供类似能力并不能证明业务效果，本仓不对未实际进行同题比较的框架排名。

本项目原创代码与文档采用 [Apache License 2.0](../LICENSE)。**该授权不替第三方重新许可。** 本仓发布研究接线、合成数据观测、数值重算和索引；上游源码、外部教程全文、依赖包与商业产品不随研究仓分发，复做时按固定来源另取。kit、commerce、aidless 的固定上游各有 LICENSE，aidless 另有 NOTICE；haty 的固定仓根未见 LICENSE，所以这里提供原始链接，不将作者文章、手册或源码声明为本仓 Apache-2.0 作品。项目名称和商标仍属于各自权利人。
