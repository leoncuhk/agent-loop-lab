# Commerce Agents：一次导购、一次补货，以及两份同时待批的调价单

这个项目值得借的是**让购物卡片和商家草案由业务系统出具**。在零售样例中，模型选一个 SKU，商品卡的价格和规格来自本会话读过的商品；模型可以提议补货，但库存能否改变，取决于工具和审批。我们让真实模型分别走了两条路径，又绕过模型直接用敌对输入测试工具。基本的商品来源和审批控制生效；另有一个生产中必须补的缺口：审批时价格已改变，样例仍允许按旧预览直接覆盖。它适合作为业务蓝图，没有提供可以直接上线的定价控制或业务效果证明。

## 实际跑了什么

固定 [anthropics/commerce-agents @ fd4d592](https://github.com/anthropics/commerce-agents/tree/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf)。用唯一参考 clone 的 `git archive` 构建独立工作副本，在 macOS arm64、Python 3.12.13、uv 0.11.7 上按 `requirements-dev.txt` 安装全部七个包及上游固定依赖。原版 `python -m pytest -q` **1,104 passed / 1 skipped**（2 warnings，exit 0），原版 `python scripts/check.py` **clean**（exit 0）。测试配置覆盖 shopping、merchant、两者的三条技术路径以及四个行业示例；其中有替身，不代表在三个厂商环境中运行了真实模型。[测试范围](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/pytest.ini)；[上游验证说明和维护声明](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/README.md)。

**真实模型，各一例一次，无重试。** 两例使用上游零售目录、原提示、Skill 文件、Messages 应用循环、executor、卡片与 mock 后端。无 Anthropic 凭证；只替换了模型传输：`gpt-6.1-sol` 在已登录的 Codex CLI 中选择文本、工具和参数，每一轮生成完毕后，用上游 `FakeStream` 产生一个完整内容块的事件序列。所谓真实是指工具选择由模型实时作出；事件时序不是 Claude 的原生流。未运行需真实 Anthropic 服务的官方 `smoke_chat.py`，未连生产系统。[上游零售样例](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/examples/retail/README.md)；[上游流封装](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/testing.py#L83)；本地 [真实用例](live.py)。

| 用户要什么 | 怎么判 | 实际发生 |
|---|---|---|
| 给笔记本找 50 美元内的旅行转换器，展示但别加入购物车 | 商品来自本会话；卡片的 SKU、价格和功率对回目录，购物车不变 | 2 次请求。模型并行调用商品搜索与 `load_skill(search-discovery)`，随后展示 `AR-1805`；服务器填入 USD 49、65 W GaN，购物车为 0。 |
| 为海洋墙贴额外补 7 件，只发预览 | 草案只能有目标 SKU、准确的库存增量；卡片显示这份草案；后端没有已执行变更 | 4 次请求。模型加载 `inventory-operations`，搜 listing/库存告警，读商品与待办，再调用 `stage_inventory_action`。卡片的 `AR-2102` 显示 3→10；待批 1、已执行 0，实存仍为 3。 |

两角色各有 5 个 Skill。原始系统提示含名称和说明、不含任一整篇正文。独立回读**实际发给模型**的请求，两例的第一条都没有对应正文，后续 `tool_result` 与上游正文逐字相同。这是模型上下文中的按需披露：运行时启动时已从磁盘读入全部文件。本例没有比较全量加载、禁用 Skill 或拆成多 Agent，不能推断哪一种更有效；实际看到导购即使只返回一个商品也选了 `search-discovery`。[Skill 注册与读取](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/skills.py)；[独立回读](audit.py)。

6 次调用串行，每个角色共享同一个 30 次总上限；收据全部成功。原请求为 Claude adaptive thinking / low，实际传输用 Codex low，两者不可等同；6 次 `tool_choice` 均为 `auto`，原调用没有 temperature。缓存标记、单次 2,048 token 的 Anthropic 生成上限、原生流式延迟/并行重叠未模拟。Codex 收据合计输入 90,552（含 cached 4,224）、输出 408；不能用它估计 Claude 账单、比较架构成本或推断并发能力。未触发分析子 Agent、长期记忆提取、官方 Agent SDK 或 Managed Agents 的在线对话。

## 直接攻击边界，看看真正落了什么

[独立探针](probe.py)不让模型猜测试答案。读写走两个角色的真实 executor，商家批准/拒绝走上游 FastAPI 预览卡路由；只有在新商户会话列出待批草案时，两个模型响应使用脚本（列出、结束）。下表的状态从 retail backend 或其变更账本回读。

| 注入或操作 | 可复核的结果 |
|---|---|
| 会话 A 搜索、读取转换器；B 直接拿同一 SKU 加车；A 查询自己的订单、B 查询 A 的订单 | B 加车命中 `provenance`、购物车为 0；A 正常加一件为 USD 49。A 能读订单明细，B 不能。这里验证的是不同已构造身份下的范围，不是登录认证。 |
| 不读 listing 就补货，或读后一次补 501 件 | 前者被来源 gate 拦下，后者超过 500 件额度被拒；两者都没有草案。未知 Skill 名也明确报错。 |
| 读过商品后暂存两份 +7 的草案，对其中一份从聊天工具直接 apply | 无宿主批准，返回 `approval` 拦截；库存保持 3。拒绝第一份后库存还是 3，试图再次批准它返回 400。批准第二份后库存为 10，重复批准亦为 400，没有重复增加。 |
| 缺少 / 伪造会话 ID；有合法会话但没见过变更就点批准 | 前两者 HTTP 401；后者返回 `ok:false`。同一演示商家的另一个合法会话经 `get_pending_changes` 可以列入并处理这些待办。不能把“见过”当作多租户授权；示例只配一个商家，启动会话本身不验证真实操作人。[宿主路由](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/examples/demo_common/merchant.py#L237)、[身份与部署责任](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/docs/safety.md)。 |

**严格性质未通过：批准时相对当前价的变动必须 ≤20%。** 转换器当时卖 49 美元，同时暂存 40 和 58 两个新价格，二者相对 49 均只变动约 18.37%。经同一官方预览卡路由先批 40，再批 58，两次均成功。第二张预览上的原价仍是 49，实际后端却从 40 涨到了 58（**45%**）。全新后端独立复跑，`probe.py --require-current-price-cap` 如实以 **exit 1** 结束。没有真实支付、自然并发或数据竞态；按顺序处理两份仍有效的旧草案已足以复现。

原因可追至真实代码：暂存时把价格写入 [change item.before](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/examples/retail/api/mock_merchant.py#L567)，批准时 [ChangeLedger 再查暂存记录](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/merchant-agent/core/merchant_agent/changes.py#L188)，然后 [直接赋新价格](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/examples/retail/api/mock_merchant.py#L753)。这是参考后端对**我们额外提出的当前价性质**的反例；不声称所有采用同一接口的系统都有这个问题。若移植到生产价格系统，应在事务内核对被审批记录的版本和当前价，过期时返回新预览重新审批，把第二次批准应当拒绝写成验收用例。业务方仍要定义跨用户身份、真实支付边界与可持久化回执。

## 复核与复跑

[轻量结果](results.json)保留逐例事实和真实调用汇总。完整 R1 原始请求、响应、工具事件和会话数据仅在本机 `runs/oss-fieldwork-20261108/commerce/`（原生测试日志、`strict-1/trace.json`、`live-1/<role>/`、`live-1/bridge/*/receipt.json`）。从实验仓根目录：

```bash
# 仅读取本轮原始轨迹，不调用模型。输出可与 results.json 对照。
python3 fieldwork/2026-11-08/commerce/audit.py runs/oss-fieldwork-20261108/commerce

# 新目录、固定提交、新虚拟环境；运行官方全套 pytest、check 及严格反例。
bash fieldwork/2026-11-08/commerce/run.sh runs/commerce-offline-replay

# 可选：包括两个真实模型用例及独立回读，需要已登录 Codex CLI，会消耗模型额度。
bash fieldwork/2026-11-08/commerce/run.sh runs/commerce-live-replay --live
```

复跑脚本会检查严格反例确实得到退出 1 和 45% 的变动，再将“成功复现现象”作为脚本成功；这不等于价格性质通过。本次未构建八个 Web 应用、连接外部工具、调用托管服务或做真实客户评测。上游同样明确说明 [没有随仓提供业务 eval harness](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/plugins/commerce-builder/skills/commerce-evals/SKILL.md#L8)。
