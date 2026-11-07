"""Evaluator-only truth. These functions are never registered as agent tools."""

from collections import defaultdict
from itertools import product


def evaluate_plan(case, policy, products, selected_ids):
    by_id = {p["id"]: p for p in products}
    errors = []
    if len(set(selected_ids)) != len(selected_ids) or any(i not in by_id for i in selected_ids):
        return {"feasible": False, "errors": ["unknown_or_duplicate_product"]}
    chosen = [by_id[i] for i in selected_ids]
    if sorted(p["group"] for p in chosen) != sorted(case["groups"]):
        errors.append("group_coverage")
    req = case["requirements"]
    platforms = {p["platform"] for p in chosen} - {"universal"}
    if len(platforms) > 1 or (req["platform"] != "any" and platforms != {req["platform"]}):
        errors.append("platform")
    if any(p["stock"] < 1 for p in chosen):
        errors.append("stock")
    if any(p["eta"] > req["deadline"] for p in chosen):
        errors.append("deadline")
    if any(p["warehouse"] in req["excluded"] for p in chosen):
        errors.append("excluded_warehouse")
    subtotals = defaultdict(int)
    for p in chosen:
        subtotals[p["warehouse"]] += p["price"]
    merchandise = shipping = 0
    for warehouse, subtotal in subtotals.items():
        discount = (
            subtotal * policy["discount_bps"] // 10000
            if subtotal >= policy["discount_threshold"]
            else 0
        )
        net = subtotal - discount
        merchandise += net
        if net < policy["free_shipping_threshold"]:
            shipping += policy["shipping"][warehouse]
    credit = min(case["funds"]["posted_credit"], merchandise * policy["credit_bps"] // 10000)
    cash = merchandise + shipping - credit
    if cash > req["budget"]:
        errors.append("cash_budget")
    return {
        "selected_ids": sorted(selected_ids),
        "cash_cents": cash,
        "utility": sum(p["utility"] for p in chosen),
        "feasible": not errors,
        "errors": errors,
        "merchandise": merchandise,
        "shipping": shipping,
        "credit": credit,
    }


def optimal_plan(case, policy, products):
    choices = [[p["id"] for p in products if p["group"] == group] for group in case["groups"]]
    feasible = []
    for selected in product(*choices):
        value = evaluate_plan(case, policy, products, list(selected))
        if value["feasible"]:
            feasible.append(value)
    if not feasible:
        return None
    best = min(feasible, key=lambda p: (-p["utility"], p["cash_cents"], p["selected_ids"]))
    return {**best, "feasible_combinations": len(feasible)}


def audit_truth(branches, cutoff, flag_threshold):
    totals = {}
    flagged = []
    for branch, rows in branches.items():
        latest = {}
        for row in rows:
            if row["kind"] != "posting" or row["at"] > cutoff:
                continue
            old = latest.get(row["ref"])
            if old is None or row["seq"] > old["seq"]:
                latest[row["ref"]] = row
        totals[branch] = sum(r["amount"] for r in latest.values() if r["state"] == "approved")
        flagged.extend(
            f"{branch}/{ref}"
            for ref, row in latest.items()
            if row["state"] == "pending" and row["amount"] > flag_threshold
        )
    return {"totals": totals, "flagged": sorted(flagged)}
