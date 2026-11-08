# 复算、重放、复跑：三种不同的验证

下列命令从仓库根执行。历史标签为 `fair-v2`（冻结正式对照）、`handoff-v1`（早期综合）、`fieldwork-v1`（四项目原始交付）；用 `git show 标签:路径` 可以读取当时的原件。建议使用普通 Git clone 而非下载 ZIP；历史脚本有些要查旧 Git 对象。

## 1 · 只拿公开仓库，无账户、无模型

~~~bash
python3 scripts/check_evidence.py
uv sync --frozen --extra dev --python 3.12
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen loop-lab tasks
~~~

第一条仅用标准库，无需装 uv；其余命令使用锁定依赖，首次安装需联网。项目支持 Python 3.11–3.13；以下 shell 演示假设 macOS/Linux。请在源码检出中使用可编辑安装：实验依赖随仓保存的 skills 和冻结文件，未承诺 wheel 单独部署。查看 tasks 不调用模型。工程测试验证工具、预算、审批恢复、评分与隔离；通过测试不等于证明模型成功率。

公开核验读取[逐次导出](../data/README.md)、正式独立审计、开源项目结果与用量摘要，重建平衡分配并核验冻结源码、知识和题目指纹。预期核心结果：客服 V1 64/64；困难 core 33/48 已启动；正式 4×24 次、475 请求；上游四项目 52 次、229 请求。不能用公开核验来证明未发布原始对话的真实性或重演旧后台状态。GitHub 检查只在手动选择 **Actions → Verify public evidence → Run workflow** 时运行，推送和 PR 均不触发。它在无需模型密钥的 Linux 环境运行公开核验和工程测试；实际执行记录请区分[本地验证与托管运行](records/PUBLICATION.md)。

## 2 · 复做上游的离线工程检查或故障实验

例如，从零取回官方 kit 的固定 Git 对象再跑其测试、冒烟与判分反例：

~~~bash
export LOOP_LAB_REFERENCES="$PWD/runs/upstream-cache"
python3 scripts/upstreams.py kit
kit=fieldwork/2026-11-08/kit
python3 "$kit/native.py" runs/my-kit-offline
python3 "$kit/probe.py" runs/my-kit-offline
~~~

输出目录要尚未存在；若 Python 3.11 较早、不支持 tar 提取过滤，用 3.12。缓存只获取固定提交；已有上游 store 会先验对象，不切分支、不改工作树。默认查找仓库旁的 `../references`，设置环境变量可全部放入本仓忽略的 runs。另三个对象用 `python3 scripts/upstreams.py haty commerce aidless`。各项目的原版与附加验证命令：[haty](../fieldwork/2026-11-08/haty/README.md)、[commerce](../fieldwork/2026-11-08/commerce/README.md)、[aidless](../fieldwork/2026-11-08/aidless/README.md)。SQL 教程单独按内容指纹取得。[教程入口](../fieldwork/2026-11-08/langchain-reference/README.md)

**若持有原始运行材料**，则可做更强的离线验证。例如以下命令直接读 CLI 原始事件、逐次工具返回及合成数据库；公开 clone 默认没有这些目录。冻结回执内有当时机器的路径，迁移后显式指定公开封存文件：

~~~bash
python3 analysis/audit_fair_evidence.py runs/fair-holdout-v2 \
  --sealed reviews/fair-v2-holdout-tasks.sealed.json
python3 fieldwork/2026-11-08/verify.py runs/oss-fieldwork-20261108
python3 scripts/export_observations.py runs runs/new-public-observations
~~~

核验正式批次要看 `final_acceptance` **和** `full_comparison_eligible`，而不只看程序退出码。保留旧文件；新的导出必须写到新目录。

## 3 · 重新取得模型决策：会消耗额度

需要受支持的 Codex CLI、可用的已登录账户和原运行模型；先读[模型桥接代码与局限](../fieldwork/2026-11-08/model_bridge.py)。小型示例只在本地合成系统写入：

~~~bash
codex login status
bash demo.sh d03 runs/my-return-demo
~~~

正式四配置同题回归的调用预算要大得多。**在手动决定运行时**才执行：

~~~bash
uv run --frozen python -m loop_lab.fair.study test --output runs/my-fair-replay \
  --receipt reviews/fair-v2-freeze.json --sealed reviews/fair-v2-holdout-tasks.sealed.json
~~~

配置、题目与模型可见资料被固定，但模型服务、账户权限、CLI 特性、缓存或 SDK 版本可能变化。不要因为模型不可用而静默换模型、把故障算作业务错误或覆盖旧批次。若改模型、工具或评分，建立新批次注明变量；同题重跑不构成新的泛化证据。原生 Claude 在线运行、真实支付与生产延迟不在本仓已验证范围。
