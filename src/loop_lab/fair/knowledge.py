"""One versioned knowledge pack; eager and progressive views use identical bodies."""

import copy
import hashlib
import json

from loop_lab.hard.knowledge import skill_library

CORE = {
    "bundle-planning": {
        "description": "组合采购搜索：平台兼容、跨仓折扣、运费和预算之间的非线性取舍。",
        "body": """# 完整组合规划
把用户最新约束整理成budget/deadline/excluded/platform，未被修订的条件仍保留。
读取当前政策和全部相关目录，先剔除缺货、逾期、禁用仓库和平台不符的产品。
按兼容平台考虑完整组合，universal配件可参加；不要把单件效用/价格最高等同组合最好。
使用assess_candidates批量评估候选，让同一quote绑定SKU、现金、效用、版本和来源。
特别检查同仓满折、折后包邮和可用抵扣的临界组合；价格较低的替换可能释放预算，提高其他部件效用。
先最大化总效用，再最小化现金，最后按SKU字典序。直接使用评估结果的排序，避免把某组合金额复制到另一组合。
工具只对你提出的候选排序；best不证明全空间最优。检查仍有竞争力的替代组合，再决定结束搜索。
在同样目标下，空候选列表可取已经评估过的最好结果，不要重复计算同一候选。
用户明确要求提交后，以当前revision的quote_id调用stage_quote；模型不再重写金额。
完成标准：目标未被旧快照覆盖、最终引用可验证，且没有仍未比较的明显更优候选。""",
    },
    "revision-control": {
        "description": "用户多轮修改预算、平台、仓库或日期后，保留未变条件并替换旧约束。",
        "body": """# 需求修订与成果复用
case是初始快照。按时间顺序应用用户修订，区分替换清单、增加限制和撤销限制。
每次评估明确传入完整的当前需求与request_revision；不能只改变revision号码而沿用旧预算。
原始商品、政策未变化时可复用已读材料；旧quote仍绑定旧目标，不能直接用于新revision。
优先重新评估仍有竞争力的旧组合，再探索新条件允许或要求的新组合。
分析轮次不提交最终草案。最后一次明确请求提交时，检查所有保留条件和新条件。
委派完整可交付子问题时，传递同一当前需求和修订序列，保留稳定scope，避免每轮从零调查。
quote的amount、SKUs、policy和目标是一个整体，修改目标应重新生成quote，而不是编辑报价数字。
完成标准：最后交付对应当前需求，先前未被撤销的限制没有遗失，没有提前提交。""",
    },
    "ledger-snapshot": {
        "description": "账务快照：时间截断、事件去重、每分支每引用最新版本与异常标记。",
        "body": """# 有来源的账务快照
先确认政策的cutoff和flag_threshold；各分支使用同一版本。
先去掉kind不是posting和时间超过cutoff的观察，再为每个branch/ref选择seq最大的版本。
相同事件ID是重复观察，不重复累计；不同branch中的同名ref不合并。
只把最终approved事件的ID交给audit_selection求和，保留负数；pending和void不进入总额。
最终pending且amount严格大于阈值的项目列为branch/ref。不要先过滤approved再挑最新版。
audit_selection只合计你选择的事件，不会替你判断最新状态；核实事件ID与ref的区别。
材料独立时可分支调查，返回带revision、policy和来源的audit record ID。
汇总只合并完整、不重叠的分支记录。
stage_audit引用实际audit record IDs，不重新手抄各分支金额。只在用户要求交付时提交。
完成标准：全部分支覆盖、有效事件选择正确、异常完整、时点与版本一致。""",
    },
}


def knowledge_pack(size="small"):
    pack = copy.deepcopy(skill_library(size))
    pack.update(copy.deepcopy(CORE))
    return pack


def pack_hash(size="small"):
    return hashlib.sha256(
        json.dumps(knowledge_pack(size), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
