"""Deterministic synthetic cases. Oracle/expected values stay outside the tool packets."""

import copy
import random

from loop_lab.hard.oracles import audit_truth, evaluate_plan, optimal_plan

PLAN_POLICY = {
    "version": "purchase-Q4-7",
    "discount_threshold": 9300,
    "discount_bps": 1350,
    "free_shipping_threshold": 8500,
    "shipping": {"W1": 480, "W2": 720, "W3": 390},
    "credit_bps": 2300,
    "interpretation": [
        "每个required group恰好选一个SKU。除universal外，平台必须相同；若客户限定平台则须匹配。",
        "库存至少1；所有ETA不晚于deadline；排除仓库不能使用。",
        "先按仓库分别汇总原价；每仓达到discount_threshold才折扣，折扣按bps计算向下取整到分。",
        "折后每仓金额达到free_shipping_threshold才免运费，否则每仓收一次shipping费用。",
        "抵扣=min(posted_credit,全部折后商品金额*credit_bps//10000)；不含运费。pending_credit不可使用。",
        "现金=全部折后商品金额+运费-抵扣，不能超过客户budget。",
        "目标先最大化utility之和，再最小化cash_cents；仍并列时选排序后的SKU列表字典序最小者。",
        "case是初始需求快照；后续用户revision覆盖对应字段，未修改的条件仍有效。",
    ],
}


def _products(seed, groups):
    rng = random.Random(seed)
    rows = []
    for gi, group in enumerate(groups):
        for i in range(6):
            price = (2400, 3200, 4100, 5100, 6400, 7800)[i] + rng.randrange(-250, 251, 50)
            rows.append(
                {
                    "id": f"{group[:2].upper()}{i + 1}",
                    "group": group,
                    "price": price,
                    "utility": 20 + price // 140 + rng.randrange(-7, 8),
                    "warehouse": f"W{rng.randrange(1, 4)}",
                    "platform": "universal"
                    if group in {"charger", "case"} and i % 3 == 0
                    else ("A" if i % 2 == 0 else "B"),
                    "stock": 0 if (gi + i) % 11 == 9 else rng.randrange(1, 9),
                    "eta": f"2026-11-{rng.randrange(14, 20):02d}",
                }
            )
    return rows


def plan_task(task_id, seed, *, revision=False, four_groups=False):
    groups = ["base", "battery", "charger"] + (["case"] if four_groups else [])
    # IDs use category prefixes that must remain unique (base and battery differ).
    products = _products(seed, groups)
    for p in products:
        p["id"] = {"base": "B", "battery": "T", "charger": "C", "case": "H"}[p["group"]] + p["id"][
            -1
        ]
    policy = copy.deepcopy(PLAN_POLICY)
    policy["discount_threshold"] += seed % 5 * 200
    policy["credit_bps"] += seed % 3 * 150
    case = {
        "groups": groups,
        "requirements": {
            "budget": 100000,
            "deadline": "2026-11-18",
            "excluded": [],
            "platform": "any",
        },
        "funds": {"posted_credit": 3700 + seed % 4 * 100, "pending_credit": 6000},
        "policy_ref": "policy.current",
        "catalog_refs": [f"catalog.{g}" for g in groups],
        "snapshot_revision": 0,
    }
    # Set a meaningful budget from feasible costs, without consulting any model result.
    from itertools import product

    choices = [[p["id"] for p in products if p["group"] == group] for group in groups]
    costs = sorted(
        v["cash_cents"]
        for ids in product(*choices)
        if (v := evaluate_plan(case, policy, products, ids))["feasible"]
    )
    if not costs:
        raise ValueError("Generated case has no feasible combinations")
    case["requirements"]["budget"] = costs[len(costs) // 2]
    latest = copy.deepcopy(case)
    final_revision = 0
    output = (
        "提交最优草案。stage_result的result对象包含selected_ids（排序SKU列表）、"
        "cash_cents（整数分）、utility（整数）、policy_version和request_revision；"
        "最终Answer引用草案事实和真实草案receipt_id。"
    )
    messages = [f"[revision 0] 请读取case和其中引用的有效政策/目录，按当前要求选择组合。{output}"]
    if revision:
        latest["requirements"]["platform"] = "B"
        latest["requirements"]["deadline"] = "2026-11-19"
        latest["requirements"]["excluded"] = ["W3"] if seed % 2 else ["W1"]
        latest["requirements"]["budget"] = 100000
        costs2 = sorted(
            v["cash_cents"]
            for ids in product(*choices)
            if (v := evaluate_plan(latest, policy, products, ids))["feasible"]
        )
        if not costs2:
            latest["requirements"]["excluded"] = []
            costs2 = sorted(
                v["cash_cents"]
                for ids in product(*choices)
                if (v := evaluate_plan(latest, policy, products, ids))["feasible"]
            )
        latest["requirements"]["budget"] = costs2[len(costs2) // 2]
        final_revision = 2
        excluded = ",".join(latest["requirements"]["excluded"]) or "无"
        messages = [
            "[revision 0] 读取case，分析一个可行组合，暂不保存草案。可用facts描述分析。",
            f"[revision 1] 平台限定为B，仓库排除清单改为{excluded}；"
            "截止日期改为2026-11-19，其余条件先保持。继续分析，不要保存。",
            f"[revision 2] 现金上限最终改为{latest['requirements']['budget']}分。"
            f"revision 1的平台、仓库和日期要求继续有效。{output}",
        ]
    packets = {
        "case": case,
        "policy.current": policy,
        "policy.retired": {
            **policy,
            "version": "purchase-Q3-retired",
            "credit_bps": 8000,
            "validity": "retired, do not apply",
        },
    }
    for group in groups:
        packets[f"catalog.{group}"] = {
            "group": group,
            "products": [p for p in products if p["group"] == group],
        }
    return {
        "id": task_id,
        "family": "revision" if revision else "planning",
        "messages": messages,
        "packets": packets,
        "latest_case": latest,
        "policy": policy,
        "products": products,
        "final_revision": final_revision,
        "expected": optimal_plan(latest, policy, products),
        "required_packets": ["case", "policy.current", *case["catalog_refs"]],
    }


def audit_task(task_id, seed):
    rng = random.Random(seed)
    branches = {}
    for branch in ("east", "west", "north"):
        rows = []
        for ref in range(1, 13):
            for seq, at in enumerate((8, 18, 28), 1):
                row = {
                    "kind": "posting",
                    "id": f"{branch}-{ref}-{seq}",
                    "ref": f"I{ref:02d}",
                    "seq": seq,
                    "at": at + rng.randrange(-3, 4),
                    "state": rng.choice(["approved", "approved", "pending", "void"]),
                    "amount": rng.randrange(-800, 3400, 25),
                }
                rows.append(row)
                if ref % 4 == 0 and seq == 2:
                    rows.append(dict(row))
        for n in range(45):
            rows.append(
                {
                    "kind": "heartbeat",
                    "id": f"hb-{n}",
                    "ref": "-",
                    "seq": n,
                    "at": n,
                    "state": "approved",
                    "amount": 999999,
                }
            )
        for row in rows:
            row["observability"] = {
                "transport": (
                    "collector retry, ingestion checkpoint retained; "
                    "trace records are not financial postings. "
                )
                * 3,
                "source_node": f"{branch}-collector-{rng.randrange(1, 9)}",
                "queue_ms": rng.randrange(50, 5000),
                "diagnostic": (
                    "Local event storage confirms receipt; "
                    "business state is only in the named posting fields. "
                )
                * 2,
            }
        rng.shuffle(rows)
        branches[branch] = rows
    cutoff, threshold = 23, 1500
    policy = {
        "version": "audit-snapshot-4",
        "cutoff": cutoff,
        "flag_threshold": threshold,
        "rules": [
            "先排除kind不是posting、at超过cutoff的观察。",
            "相同事件id的重复观察只计一次；每个branch/ref保留剩余观察中seq最大的版本。",
            "各分支total只合计最终state=approved的amount，保留负数。",
            "最终state=pending且amount严格大于flag_threshold的项目列入flagged，格式branch/ref。",
            "void不计总额，也不进入flagged。observability不影响任何业务判断。",
        ],
    }
    packets = {
        "case": {
            "branches": list(branches),
            "policy_ref": "policy.audit",
            "ledger_refs": {b: f"ledger.{b}" for b in branches},
        },
        "policy.audit": policy,
    }
    packets.update({f"ledger.{b}": {"branch": b, "rows": rows} for b, rows in branches.items()})
    return {
        "id": task_id,
        "family": "audit",
        "packets": packets,
        "branches": branches,
        "policy": policy,
        "final_revision": 0,
        "messages": [
            "[revision 0] 按case引用的审计规则完成三个分支的快照审计。"
            "在stage_result的result对象提交totals（分支到整数分的映射）、flagged（排序列表）、"
            "policy_version和request_revision；Answer引用草案事实和receipt_id。"
        ],
        "expected": audit_truth(branches, cutoff, threshold),
        "required_packets": ["case", "policy.audit", *[f"ledger.{b}" for b in branches]],
    }


def hard_tasks():
    return [
        plan_task("P0", 111),
        plan_task("P1", 210),
        plan_task("P2", 211),
        plan_task("P3", 212, four_groups=True),
        plan_task("P4", 213, four_groups=True),
        plan_task("R0", 310, revision=True),
        plan_task("R1", 311, revision=True),
        plan_task("R2", 312, revision=True),
        audit_task("A0", 410),
        audit_task("A1", 411),
        audit_task("A2", 412),
    ]
