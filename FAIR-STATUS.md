# 四方法优化后的复测状态

更新：2026-11-07。当前状态：**实现与离线验证完成；真实模型开发集和最终留出复测被账户额度阻塞。没有新的方法效果排名。**

## 已完成的优化

| 范围 | 原实现缺口 | 当前实现 |
|---|---|---|
| 公共业务工具 | SKU、金额、目标和政策可被模型错误拼接 | `assess_candidates`生成不可变quote，`stage_quote`只引用ID；数据版本变化、旧revision、未观察引用、已知更优候选均会拒绝 |
| 工具独立性 | 改进工具可能偷看真值或把难题直接交给评分oracle | World只保存id/family/公开packets；候选由模型提出，工具仅报价和排序；数百个开发组合与独立oracle交叉一致 |
| tools | 容易因为比较接口不可靠被误判成推理不足 | 使用与其他三组完全相同的公共报价/审计工具，无专门流程正文 |
| eager | 无差别执行所有加载的方法 | 预加载同一完整知识包，明确条件性适用范围；不删减对照组中的相同正文 |
| skills | 每次单独加载且可能重复读取 | 批量按需加载，已载内容在同一会话保留；正文与eager相同，测试验证hash内容与共同工具schema |
| multi | 临时角色、重复调查、无收尾配额 | 按完整平台方案/独立账务分支划分，持久worker跨轮复用，单次worker额度+主Agent预留，partial返回已登记成果 |
| 交付 | 最后一轮模型复述可能再拼错数据 | 四组同用真实草案回执生成规范化最终Answer；该部分不计为模型自然语言质量 |
| 审计评分 | 错误事件版本可能碰巧得到相同总额 | 另外检查实际选中的事件ID集合，不能只靠总额相同通过 |
| 故障隔离 | 单次超时导致其他组不完整 | 每trial独立账本，统一一次技术恢复，首次失败原样保留，恢复消耗原任务剩余额度 |
| 系统性故障 | 登录状态正常但额度不足仍继续后续任务 | 服务错误分类，额度/认证错误结束当前平衡块后停止后续块；报告无效，不评价方法优劣 |

公共报价并不搜索全空间。若最终再次全通过，只能说明改进工具消除了该批任务中的部分错误，而不是四种推理架构普遍等价。

## 工程与审查证据

最新 `uv run --frozen --extra dev pytest -q`：**46 passed**。`ruff check .`通过。

测试覆盖报价绑定、版本失效、无gold/未来消息泄漏、同知识正文与共同工具schema、worker上下文复用、收尾额度、partial成果保留、正确事件选择、首次超时与恢复成本、拒绝业务失败重试、重复结果不能假冒平衡覆盖、quota错误停止。

设计阶段已完成Matt code-review的Standards/Spec两轴与codebase-design三种Interface比较：

- [Standards与最小Interface](reviews/fair-v2-standards-design.md)
- [Spec与试验协议](reviews/fair-v2-spec-design.md)
- [常见调用者与方法优化](reviews/fair-v2-strategy-design.md)

这些审查覆盖设计及旧实现。**新版实现的最后一次独立代理复查未完成**：代理运行时仍报告旧令牌刷新失败。后续离线回归和缺陷修复由主实现者完成，不能把它写成最终独立验收已经通过。

## 真实运行发生了什么

第一次试运行 `runs/fair-dev-v1` 的12个请求均被服务拒绝，没有任何有效模型决定。原stdout明确提示usage limit；模型用量未知，不按0成本计。旧结果和原派生报告保留，新增VALIDITY将本批标为不可用于架构比较。

这次拒绝也复现了运行器未对额度错误立即停止的缺陷；修复后新增离线负样本验证：当前平衡块结束即停止，后续未运行清单保留，不再无效遍历全部任务。

用户确认重新登录后，CLI login status显示已登录；随后只做一次最小可用性探针，仍返回 `usage_limit`。记录在 [最小探针](runs/fair-service-probe-20261107T012006Z/usage.jsonl)。因此没有继续发请求，也没有把离线脚本结果冒充模型效果。

## 独立留出与科学比较边界

独立审查者已准备并验证12个新实例参数（3/4/5类采购、多轮修订、3/4分支审计），未按模型成绩筛选；父实现者尚未读取具体seed/gold。文件hash已记录，工作流隔离不是操作系统强隔离。

正式计划每题每方法2次，共96trial。先用旧P2/R2/A0完成四方法相同开发机会，最多两轮；选择明确版本后freeze才读取新实例。eager/skills同知识包；工具与政策相同；多Agent所有主子请求合计同24调用上限，request timeout 240s，最多4个模型请求并发。

严格“最优实现”无法由有限实验保证；可实现的是同等、有界调优机会后最好候选的比较。当前尚未发生有效的新开发模型运行，因此还不能称这四个实现已经充分调优。

库大小在不同留出case中预先分配小/大，对同一case四方法完全一致；它支持固定规模下的方法比较，**不能把不同case的小库/大库成绩差直接当作库规模因果效应**。后者仍需同题交叉库规模的单独实验。

## 额度恢复后的续跑顺序

先恢复当前账号模型额度，或由用户明确选择新的有额度模型/API路线。改变模型需要重新固定条件，不能继续冒充原模型对照。

```bash
cd /Users/leon/Documents/harness/research/evaluation/agent-loop-lab

# 使用新目录。已存在的失败批次保持原样。
uv run --frozen python -m loop_lab.fair.study dev --output runs/fair-dev-v2

# 只有四组开发结果完整、源码相同，且审查通过后才冻结。
uv run --frozen python -m loop_lab.fair.study freeze \
  --output reviews/fair-v2-freeze.json --development runs/fair-dev-v2

# 冻结后才解封新任务，完整运行四组、每题两次。
uv run --frozen python -m loop_lab.fair.study test \
  --output runs/fair-holdout-v2 --receipt reviews/fair-v2-freeze.json
```

如开发集发现新装置缺陷，先保留结果、修复并重做同等开发面板，不能直接用这条命令把尚未审查的实现送入留出集。额度阻塞解除前，以上为续跑入口，不是已完成的效果证明。
