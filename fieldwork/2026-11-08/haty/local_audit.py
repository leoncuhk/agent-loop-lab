"""Offline audit. Read archived raw runs, emit only lightweight scores / routes / counts."""

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


def norm(s):
    return unicodedata.normalize("NFKC", s)


def pat(s, reg):
    return bool(re.search(reg, s, re.I))


def hasdate(s, target):
    y, m, d = target.year, target.month, target.day
    return pat(s, rf"(?<!\d)(?:(?:{y}(?:年|/|-))?0?{m}(?:月|/|-)\s*0?{d}(?:日|(?!\d)))")


def assess(r, data):
    if not r.get("calls"):
        return {"cell": r["cell"], "error": r.get("error"), "local_pass": False}
    s = norm(r["answer"])
    ts = sorted(r["tools"], key=lambda x: x["t"])
    tid = r["task"]

    def hits(n):
        return [t for t in ts if t["name"] == n]

    claims = {}
    proof = {}
    supplemental = {}
    if tid in ("T03", "T04"):
        oid = "A1003" if tid == "T03" else "A1004"
        o = data["orders"][oid]
        cid = o["customer_id"]
        tracking = o["tracking_number"]
        cat = o["items"][0]["category"]
        delivered = date.fromisoformat(data["shipments"][tracking]["delivered_at"])
        cutoff = delivered + timedelta(days=30 if tid == "T03" else 14)
        ver = [
            x
            for x in hits("verify_customer")
            if x["args"].get("order_id") == oid
            and x["args"].get("email") == data["customers"][cid]["email"]
            and x["result"].get("verified") is True
            and x["result"].get("customer_id") == cid
        ]
        orders = hits("get_order")
        ok_orders = [
            x
            for x in orders
            if x["args"] == {"customer_id": cid, "order_id": oid}
            and x["result"].get("order_id") == oid
            and x["result"].get("tracking_number") == tracking
            and x["result"].get("items") == o["items"]
        ]
        supplemental["orders_without_earlier_same_actor_verification"] = [
            x["agent"]
            for x in orders
            if not any(v["agent"] == x["agent"] and v["t"] < x["t"] for v in ver)
        ]
        proof["verified_order_before_access"] = bool(
            ver and ok_orders and min(x["t"] for x in ver) < min(x["t"] for x in orders)
        )
        proof["category_policy"] = any(
            x["args"].get("category") == cat
            and x["result"].get("policy") == data["return_policies"][cat]
            for x in hits("get_return_policy")
        )
        ship = any(
            x["args"].get("tracking_number") == tracking
            and x["result"].get("delivered_at") == delivered.isoformat()
            for x in hits("get_shipment")
        )
        if tid == "T03":
            claims["eligible"] = pat(
                s,
                r"可能|お?受け(?:でき|いたし|し)|返品.{0,5}(?:を?承|できます|いただけ|いただける)|承[れり]|間に合|期限内|お手続き.{0,8}(?:可能|いただけ)",
            )
            claims["unworn"] = pat(
                s, r"未着用|未使用|着用.{0,4}(?:していない|されていない)|着ていない"
            )
            claims["tags"] = pat(s, r"タグ|下げ札")
            claims["delivered_date"] = hasdate(s, delivered)
            claims["deadline"] = hasdate(s, cutoff)
            proof["shipment_delivered_date"] = ship
        else:
            claims["opened"] = pat(s, r"開封|開梱|使用済|ご使用")
            claims["refusal"] = pat(
                s,
                r"不可|できません|できかね|対象外|いたしかね|いたし[か兼]ね|承[れり].{0,4}(?:ない|ません|かね)|お断り|お受け.{0,5}(?:かね|ません)|応じ.{0,4}(?:かね|ません)",
            )
            claims["unopened_condition"] = pat(s, r"未開封|開封.{0,3}(?:されていない|していない)")
            claims["14_day_rule"] = pat(s, r"14\s*日|二週間|2週間")
            supplemental["specific_deadline_correct"] = hasdate(s, cutoff)
            # Any novel calendar date is separately reviewed by a Japanese-reading assessor.
            supplemental["shipping_returned"] = ship
            secondary_defect_deadline = delivered + timedelta(days=30)
            if any(hasdate(s, d) for d in [cutoff, delivered, secondary_defect_deadline]):
                proof["specific_date_grounded"] = ship
    else:
        b = data["inventory"]["X200-BLK"]
        w = data["inventory"]["X200-WHT"]
        stock = [x["result"].get("results", {}) for x in hits("check_inventory")]
        proof["black_record"] = any(v.get("X200-BLK") == b for v in stock)
        proof["white_record"] = any(v.get("X200-WHT") == w for v in stock)
        claims["black_zero"] = pat(s, r"ブラック|黒") and pat(
            s,
            r"在庫(?:切れ|が?(?:ございません|ありません|なく|ない|0)|数?.{0,3}0)|欠品|品切れ|売り切れ|完売",
        )
        claims["restock_date"] = hasdate(s, date.fromisoformat(b["restock_date"]))
        claims["white_alternative_12"] = pat(s, r"ホワイト|白") and pat(
            s, r"12\s*(?:個|点|台|つ|[)）])|在庫.{0,3}12"
        )
    calls = r["calls"]
    return {
        "cell": r["cell"],
        "task": tid,
        "rep": r["rep"],
        "arch": r["arch"],
        "upstream_det": r["source_det"]["det_pass"],
        "upstream_missing": r["source_det"]["missing_patterns"],
        "verify_order_violation": r["source_det"]["verify_order_violation"],
        "claims": claims,
        "evidence": proof,
        "supplemental": supplemental,
        "local_pass": all(claims.values()) and all(proof.values()) and not r.get("bridge_errors"),
        "routes": r["routed"],
        "skills": r["skills"],
        "calls": len(calls),
        "attempts": r["attempted_model_requests"],
        "business_tool_counts": dict(Counter(t["name"] for t in ts)),
        "shipping_actors": [t["agent"] for t in hits("get_shipment")],
        "usage_input_excluding_cache": sum(c["input"] for c in calls),
        "usage_cache_read": sum(c["cache_read"] for c in calls),
        "usage_input": sum(c["input"] + c["cache_read"] + c["cache_write"] for c in calls),
        "usage_output": sum(c["output"] for c in calls),
        "wall_sec": r["wall_sec"],
    }


def verify_exchange(folder, rec, src, data, final_claims):
    """Audit raw model decisions vs menus, subsequently consumed results, and source trace."""
    proposed = {}
    returned = {}
    actors_for_result = {}
    host_items = set()
    declared_ok = True
    b_data_without_yet_read_skills = []
    bodies = {p.read_text() for p in (src / "skills").glob("*.md")}
    for request in sorted(folder.glob("model/*/request.json")):
        q = json.loads(request.read_text())
        menu = {d["name"] for d in q["tools"]}
        if "load_skill" in menu:
            actor = "main"
        elif any(name.startswith("ask_") for name in menu):
            actor = "orchestrator"
        else:
            mt = re.search("「([a-z]+)」担当のスペシャリスト", q["system"])
            actor = "sub:" + mt.group(1) if mt else "unknown"
        stdout = request.with_name("stdout.jsonl")
        if not stdout.exists():
            declared_ok = False
            continue
        events = [
            json.loads(line) for line in stdout.read_text().splitlines() if line.startswith("{")
        ]
        items = [e["item"] for e in events if e.get("type") == "item.completed"]
        for item in items:
            if item["type"] == "error" and item.get("message", "").startswith(
                "Code Mode is unavailable because code-mode host is disabled."
            ):
                continue  # capability-disabled startup notice, not host tool activity
            if item["type"] not in {"reasoning", "agent_message"}:
                host_items.add(item["type"])
        messages = [i["text"] for i in items if i["type"] == "agent_message"]
        if not messages:
            declared_ok = False
            continue
        previous_raw = [
            b.get("content")
            for m in q["messages"]
            if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_result"
        ]
        seen_body = any(isinstance(v, str) and v in bodies for v in previous_raw)
        for n, call in enumerate(json.loads(messages[-1])["calls"]):
            if actor == "main" and call["name"] != "load_skill" and not seen_body:
                b_data_without_yet_read_skills.append(call["name"])

            cid = f"call-{request.parent.name}-{n}"
            declared_ok &= call["name"] in menu
            proposed[cid] = (actor, call["name"], json.loads(call["arguments_json"]))
        for msg in q["messages"]:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for bl in msg["content"]:
                    if bl["type"] != "tool_result":
                        continue
                    cid = bl["tool_use_id"]
                    if cid in returned and returned[cid] != bl["content"]:
                        declared_ok = False
                    returned[cid] = bl["content"]
                    actors_for_result[cid] = actor

    def signature(agent, name, args, res):
        return json.dumps([agent, name, args, res], ensure_ascii=False, sort_keys=True)

    raw_tools = Counter()
    skill_reads = Counter()
    delegated = Counter()
    matched_actor = all(actors_for_result.get(k) == a for k, (a, n, args) in proposed.items())
    for cid, (actor, name, args) in proposed.items():
        if cid not in returned:
            continue
        if name.startswith("ask_"):
            delegated[name[4:]] += 1
        elif name == "load_skill":
            sk = args.get("name", "")
            if (
                sk in {"orders", "shipping", "returns", "products", "account"}
                and (src / "skills" / (sk + ".md")).read_text() == returned[cid]
            ):
                skill_reads[sk] += 1
        else:
            raw_tools[signature(actor, name, args, json.loads(returned[cid]))] += 1
    source = Counter(
        signature(t["agent"], t["name"], t["args"], t["result"]) for t in rec.get("tools", [])
    )
    checks = {
        "all_proposals_declared": bool(declared_ok),
        "host_tool_items_absent": not host_items,
        "result_consumed_by_calling_actor": set(proposed) == set(returned) and matched_actor,
        "data_trace_matches_raw_received_results": raw_tools == source,
        "skill_trace_matches_actual_text_delivered": skill_reads == Counter(rec.get("skills", [])),
        "delegate_trace_matches_completed_handoffs": delegated == Counter(rec.get("routed", [])),
    }
    direct = sum(
        1
        for k, (a, n, args) in proposed.items()
        if n != "load_skill"
        and not n.startswith("ask_")
        and actors_for_result.get(k) in {"main", "orchestrator"}
    )
    kept_in_handoff = {}
    for cid, (_actor, name, _args) in proposed.items():
        if not name.startswith("ask_") or cid not in returned or not final_claims:
            continue
        handoff = assess(rec | {"answer": returned[cid]}, data)["claims"]
        for tag, present in handoff.items():
            if present and not final_claims[tag]:
                kept_in_handoff.setdefault(tag, []).append(name[4:])
    diagnostic = {
        "absent_from_final_but_present_in_handoff": kept_in_handoff,
        "direct_data_results_in_final_actor_context": direct,
        "handoff_results_in_orchestrator": sum(delegated.values()),
        "B_data_calls_before_any_skill_body_arrived": b_data_without_yet_read_skills,
    }
    return checks, diagnostic


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("src", type=Path)
    p.add_argument("run", type=Path)
    args = p.parse_args()
    data = json.loads((args.src / "data.json").read_text())
    plan = json.loads((args.run / "plan.json").read_text())
    cells = []
    for rep, tid, arch in plan["schedule"]:
        f = args.run / f"r{rep}-{tid}-{arch}" / "result.json"
        if f.exists():
            rec = json.loads(f.read_text())
            cell = assess(rec, data)
            receipts = [
                json.loads(path.read_text())
                for path in sorted(f.parent.glob("model/*/receipt.json"))
            ]
            usage = [q["usage"] for q in receipts if q.get("usage")]
            cell["receipt_count"] = len(receipts)
            cell["receipt_status_counts"] = dict(Counter(q["status"] for q in receipts))
            cell["receipt_usage_matches_trace"] = (
                len(receipts) == cell.get("attempts") == cell.get("calls") == len(usage)
                and all(
                    q["status"] == "ok" and q["actual_model"] == plan["actual_model"]
                    for q in receipts
                )
                and sum(u["input_tokens"] for u in usage) == cell.get("usage_input")
                and sum(u["output_tokens"] for u in usage) == cell.get("usage_output")
            )
            cell["raw_exchange_audit"], cell["context_path"] = verify_exchange(
                f.parent, rec, args.src, data, cell.get("claims", {})
            )
            if not cell["receipt_usage_matches_trace"] or not all(
                cell["raw_exchange_audit"].values()
            ):
                cell["local_pass"] = False
            cells.append(cell)
    src_ok = all(
        hashlib.sha256((args.src / name).read_bytes()).hexdigest() == digest
        for name, digest in plan["source_sha256"].items()
    )
    groups = {}
    for arch in ["A", "A1", "B"]:
        sel = [c for c in cells if c.get("arch") == arch]
        groups[arch] = {
            "n": len(sel),
            "upstream_det_pass": sum(c.get("upstream_det", False) for c in sel),
            "local_strict_pass": sum(c["local_pass"] for c in sel),
            "all_required_evidence": sum(
                all(c.get("evidence", {"missing": False}).values()) for c in sel
            ),
            "model_calls": sum(c.get("calls", 0) for c in sel),
            "cli_input_incl_cache": sum(c.get("usage_input", 0) for c in sel),
            "cli_output": sum(c.get("usage_output", 0) for c in sel),
            "cli_cache_read": sum(c.get("usage_cache_read", 0) for c in sel),
            "summed_wall_sec": round(sum(c.get("wall_sec", 0) for c in sel), 3),
            "calls_by_task": {
                t: [c.get("calls") for c in sorted(sel, key=lambda r: r["rep"]) if c["task"] == t]
                for t in ["T03", "T04", "T06"]
            },
        }
    print(
        json.dumps(
            {
                "n": len(cells),
                "n_planned": len(plan["schedule"]),
                "source_matches_launch": src_ok,
                "source_sha": plan["source_pin"],
                "groups": groups,
                "cells": cells,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
