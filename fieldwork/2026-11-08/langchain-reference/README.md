# 附实验：相同知识，通过 Skill 或普通查询工具送达，有区别吗？

这是前次讨论另提到的 [LangChain SQL Skills 官方教程](https://docs.langchain.com/oss/python/langchain/multi-agent/skills-sql-assistant)，单列，不计入四个上游项目。文章会随时间变化；[脚本](probe.py) 从官方 Markdown 的完整示例中直接提取 Skill、两份领域手册、load_skill 和 SkillMiddleware 四个定义，按 SHA-256 校验快照；执行原生 create_agent 和 InMemorySaver，用实验仓锁定的 LangChain 1.4.9、LangGraph 1.2.20。在 SQL 生成之后由外部 SQLite 验证，Agent 无查库或读评分数据权限。

| 同一主 Agent 收到什么 | 活跃客户 | 库存补货 | 合计模型调用 |
|---|---|---|---:|
| 原版 Skill 目录，模型选择 load_skill | 两份数据库均通过 | 两份数据库均通过 | 4 |
| 普通词典工具，返回与该 Skill 逐字相同的业务正文 | 两份均通过 | 两份均通过 | 4 |
| 不给目录、词典、表结构或规则 | 返回询问口径的字符串，不是客户 ID | 询问口径，未给可执行 SQL | 2 |

**变化的是资料能否取得。** “活跃”在这个示例特指 status=active **且**注册满 90 天；“补货”要跨仓库汇总，恰好等于阈值也要补，停售品排除。两种有来源的条件首轮都没有完整正文，模型各使用正确的业务域一次。分别改变一条客户状态或一个仓的数量后，预期 ID 集合随之改变，四条生成的 SQL 均跟随变化。无来源时主动要求澄清是合理行为；0/2 指没交付可用查询，不是恶意或不安全。

共两道题、每种一次、每条 SQL 在两份数据上跑（不是四道独立新题）；模型没被给答案，只有小型清单，没有无关技能干扰。实验支持“同一份业务定义也能由普通工具传达”，不支持“资料规模大时两者同样好用”。全量预加载、多人维护更新、工具搜索与权限均未比较。

[摘要](results.json) · [离线复核](audit.py)。在 agent-loop-lab 根运行：

~~~bash
d=fieldwork/2026-11-08/langchain-reference
raw=runs/oss-fieldwork-20261108/langchain-reference
uv run --frozen python "$d/audit.py" "$raw"
# 另开新目录，才会调用模型。若教程更新，源码指纹检查会停止。
uv run --frozen python "$d/probe.py" "$raw/upstream.md" runs/my-sql-panel
~~~
