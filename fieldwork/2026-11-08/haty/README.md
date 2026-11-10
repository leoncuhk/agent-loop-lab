# hatyibei：保留退货专家但给它物流工具，能省掉谁？

源项目是针对 Anthropic「什么时候把子 Agent 折入主循环」文章写的 [日本电商客服实验](https://github.com/hatyibei/zenn-articles/tree/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold)。店铺时钟固定在 2026-10-24。询问“衣服不合身，还来得及退吗？”时，订单可查到商品和运单号，只有物流工具知道签收日，只有政策工具说明服饰退货期限。从订单日计算会误判已过期。

**本机结论：给退货专家物流权限，就足以让它在两次“还来得及退吗”中独立拿全事实。** 原架构 A 为此花了 10、14 次模型调用；补工具后的 A1 都是 6 次，折到主循环的 B 都是 5 次。但用不用额外专家仍受协调者选择影响：作者原生 Claude 上的 A1 在这道题三次都重复叫了物流专家。也不能据此断言 B 每题省调用：已拆封商品可以直接按政策拒退，本机 T04 上 A 两次各 5 次，比 A1、B 都少 1 次。

## 本机实际结果

[逐格结果与账本核验](results.json)。18/18 格全部结束，102/130 次真实 CLI 请求均有成功回执，无补跑、无宿主工具执行、无未完成行。18 份日语答复逐一对照固定数据人工复阅，主要业务结论均正确：T03 的截止日是 10/28，T04 开封后因个人喜好不能退并且没有承诺退款，T06 黑色无货、11/2 补货、白色有 12 件。下表“严格覆盖”另要求把预先列明的解释事实写入最终答复，并核对取数和工具回送。

| 配置（3 题各两次） | 上游确定性通过 | 取得必需数据证据 | 严格覆盖通过 | 模型调用 | CLI 输入 / 输出 tokens 合计 | 本机每格平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| A 专家按领域分工具 | 6/6 | 6/6 | 5/6 | 42 | 302,824 / 4,657 | 86.5 |
| A1 退货专家加物流 | 6/6 | 6/6 | 2/6 | 32 | 231,779 / 3,372 | 66.1 |
| B 单主循环按需读手册 | 6/6 | 6/6 | 6/6 | 28 | 218,108 / 1,690 | 51.0 |

| 题（两次调用次数） | A | A1 | B | 观察到的区别 |
|---|---:|---:|---:|---|
| T03，服饰签收起算退货 | 10、14 | 6、6 | 5、5 | A 第一遍退货→物流；第二遍退货→物流→退货，同一身份和订单各查三次。A1 两次都只有退货专家查物流。B 直接取原始物流、政策和订单。 |
| T04，电子商品已拆封 | 5、5 | 6、6 | 6、6 | A 只按开封与政策拒绝，两次均未查物流、未声称确切到期日。A1、B 均额外查物流，正确给出 10/15 到期日。 |
| T06，查询耳机库存 | 4、4 | 4、4 | 3、3 | A、A1 都只调用商品专家。B 两次先读商品手册，再查库存，少一层回传。 |

**漏项发生在哪里。** A1/T03 的两份退货摘要都给了 9/28 签收和 10/28 截止，协调者最终只保留截止日；A1/T04 的两份摘要都给了 14 天规则和 10/15 到期日，协调者只保留后者。A/T04 第二遍的摘要也有“未开封、14 天”，最终只写了开封品拒绝。这五个严格未过的格子，所缺事实都在协调者收到的摘要内。把它们记成“专家没查到”“摘要传丢了”或“错误退款”，都会选错改进位置。若产品只要求正确拒绝，T04 可直接过；若需要客户看到推算依据，再约束最终回复列出依据。全程没有真实退款操作。

输入 tokens 含 CLI 上下文与缓存读取（A/A1/B 分别 143,616、97,152、105,600 缓存读取 tokens）。每组 6 格的用量从原始回执重新累加，不填未经核实的美元价格；串行按格执行时仍与其他项目共享 CLI 并发槽，墙钟时间含排队、模型和启动时间。这里只有 3 道刻意选择的公开题；两遍说明路线是否重复，不提供总体胜率、显著性、跨模型结论或生产 ROI。A1/B 的差异还包括隔离上下文、交接和手册呈现方式，不能把 4 次调用净差全部归于某一个因素。

## 哪些代码真的跑了

原 [bench.py](https://github.com/hatyibei/zenn-articles/blob/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/bench.py) 定义全部三种架构。A 的协调者只可叫五种专家，各自只能看收到的委派文字、对应完整手册、所分到的数据工具以及自己获得的返回。A 的退货专家可验证身份、查订单与政策，却没有物流查询。A1 保留协调者与专家，单给退货专家增加物流工具，协调者的权限表随之更新。B 在同一主循环一开始就看到六个工具，按需调用 load_skill 获取五份手册中的正文；没有服务端工具搜索。业务工具是无网络、确定性的模拟查询，退款、退货和物流都无写入接口。

[预注册](prereg.md) 固定 T03、T04、T06 三题与轮换顺序、两次重复、最多 130 次真实请求；题目直接取上游日语文本。[接线](panel.py) 保留归档副本的 run_multi、run_single、run_loop、提示词、业务函数、工作线程、专家与协调者的返回方式、轮数上限，以及 [run.py](https://github.com/hatyibei/zenn-articles/blob/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/run.py) 的 det_checks。接线仅跳过导入时两个原生 SDK 客户端的创建，将 create_with_retry 指到 [共享 CLI 传输](../model_bridge.py)，修正 trace 中绑定旧默认值的模型名称，保留源标签。脚本不作模型重试；模型原始 JSON 决策由传输层验证工具名，并交回原循环执行。所有变体是同一 gpt-6.1-sol、low、同一传输/数据。请求形状转译、受约束 JSON、平台缓存及 CLI 启动均不同于 Claude API；本机耗时、token 与美元不能用来重现原文绝对值。原来的 max_tokens=2048 只被记录，桥接不提供同值的服务端输出限制。

本机评分有三个层次：① 上游工具名/日语必现词、禁词（其 det_pass **不含** 单列的越过身份认证标志）；② 逐题核验身份、实体、实际工具返回及用户可见事实；③ 对照原始 CLI 决策、每个角色后续收到的结果与原程序 trace，验证工具权限、相同返回值、手册全文和专家交接进入了正确上下文，并与逐请求用量核账。第二层衡量预先列出的事实覆盖，不代替整体日语服务体验。T04 答案只给正确到期日但省略“14 天”，可同时是业务决定正确、严格覆盖未过；不将此类情况称作“错误退款”。

## 作者结果：复算，非本机运行

[复算脚本](author_recount.py) 比较公开 runs.csv 与 summary.json 的成功、判分、模型调用、平均延迟和价格；同时逐项检查 ext_runs.csv 与 ext_summary.json 的样本数、成功、确定性检查与物流工具出现次数。[可重算输出](author-recount.json)。两个 CSV 行包含作者已给的分数及已四舍五入的价格/耗时；没有重新评分当年答案。

| 作者原生主实验（12 题 × 3 次） | 成功 | 确定性通过 | 平均模型调用 | 平均用时（秒） | 每题模型美元（CSV 均值） |
|---|---:|---:|---:|---:|---:|
| A 协调者+专家 | 34/36 | 36/36 | 7.17 | 13.495 | 0.02408 |
| B 主循环+手册 | 35/36 | 36/36 | 3.53 | 6.305 | 0.00932 |

三份失败记录 A/T04-r0、A/T09-r2、B/T09-r1 来自作者的质量评审；两个组按确定性检查全部通过，不能把 34 对 35 当作可靠的质量差别。作者用 Sonnet 5.5 答、Haiku 5.5 参照 data.json 与手册给每个答案三票取多数。模型费用列不含 judge；其去缓存估算分别为 0.03727、0.02221 美元。这里核对的是[作者公开数据](https://github.com/hatyibei/zenn-articles/tree/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/results)，作者没有公开原始轨迹。主实验 72 次属于作者。补充 CSV 另有 73 行（串行 24、A1 6、A2 18、难化 25，难化不做 judge），不能拼成同一配对样本。

作者原生 T03 的 A 每次 9 次模型调用、B 每次 4 次。作者追加 A1 的路线本身很有信息：T03 三次都叫退货和物流两个专家，各自验证身份、查订单、查同一票物流，共 10 次模型调用；T04 三次都只叫退货专家，它自己拿物流，均 6 次模型调用。给权限解决“能不能查”，并不指令协调者“一定别另叫物流”。数据来自[追加逐行 CSV](https://github.com/hatyibei/zenn-articles/blob/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/results/ext_runs.csv)。

私有面板还保留 plan.json 指纹、逐格 result.json、逐次请求/原始 CLI 事件/回执，以及与启动时 SHA-256 一致的 panel-launched.py。公开 panel.py 在启动后仅整理了导入/排版及 lint 注释；对照、提示和执行逻辑不变。

## 复核或复跑

从 agent-loop-lab 根执行，REF 是已有唯一 clone。不覆盖旧面板；要重复花费额度时设置新的输出目录名称。

```bash
EX=fieldwork/2026-11-08/haty
BASE=runs/oss-fieldwork-20261108/haty
REF="$(python3 scripts/upstreams.py --show-cache)/zenn-articles"
SHA=d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5
# 新机器或新工作目录才需要展开固定快照、准备 SDK（仅用于导入原模块）。
mkdir -p "$BASE"
git -C "$REF" archive "$SHA" bench/subagent-fold | tar -x -C "$BASE"
SRC="$BASE/bench/subagent-fold"
uv venv "$BASE/venv" --python 3.12
uv pip install --python "$BASE/venv/bin/python" anthropic==2.6.0
# 仅复算作者及现存本机面板，无模型调用。
"$BASE/venv/bin/python" -B "$EX/author_recount.py" "$SRC"
"$BASE/venv/bin/python" -B "$EX/local_audit.py" "$SRC" "$BASE/panel-1"
# 实际调用登录态 CLI，空输出目录须尚未存在；失败不覆写旧输出。
"$BASE/venv/bin/python" -B "$EX/panel.py" "$SRC" "$BASE/panel-new"
```

Claude 原生命令应在另一份干净的固定上游归档里、有有效 Anthropic key 时运行。按作者 [README](https://github.com/hatyibei/zenn-articles/blob/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/README.md)：安装 anthropic、matplotlib 后，python run.py 3 执行全主实验；**先** cp results/runs.jsonl results/runs_grader_v1.jsonl 再运行 python regrade.py（[固定脚本](https://github.com/hatyibei/zenn-articles/blob/d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5/bench/subagent-fold/regrade.py) 实际读后一个名称），然后 python analyze.py。追加权限实验为 python ext.py a1、python ext_summarize.py。原 run.py 向已有文件追加；用新目录。固定版本现用自写重试，作者表明其主实验当年使用了 SDK 自动重试。本次未运行这些 Claude/judge 命令。
