"""Check published observations, frozen inputs and cross-report arithmetic, without a model."""

import csv
import hashlib
import json
from collections import Counter
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELD = ROOT / "fieldwork/2026-11-08"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identifiers(rows, keys):
    ids = [tuple(r[k] for k in keys) for r in rows]
    check(len(ids) == len(set(ids)), f"Duplicate {keys}")
    return set(ids)


def earlier():
    with (ROOT / "data/observations.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    identifiers(rows, ["batch", "task", "method", "rep"])
    observed = {}
    support = [f"h{i:02}" for i in range(1, 17)]
    for batch, methods, cases, reps in (
        ("confirmation-v1", ["tools", "eager", "skills", "multi"], support, ["0"]),
        ("corrected-regression-v2", ["tools", "eager", "skills"], support, ["0"]),
        (
            "hard-core-v1",
            ["tools", "eager", "skills", "domain"],
            ["P1", "P2", "P3", "P4", "R1", "R2"],
            ["0", "1"],
        ),
    ):
        group = [r for r in rows if r["batch"] == batch]
        keys = identifiers(group, ["task", "method", "rep"])
        plan = set(product(cases, methods, reps))
        check(keys <= plan, f"{batch}: unexpected trial")
        stats = Counter(r["outcome"] for r in group)
        if batch == "hard-core-v1":
            check(
                stats
                == {
                    "pass": 25,
                    "business_fail": 2,
                    "task_budget": 2,
                    "request_timeout": 1,
                    "shared_stop": 3,
                }
                and len(plan - keys) == 15,
                "Interrupted study does not reconcile",
            )
        else:
            check(keys == plan and stats == {"pass": len(plan)}, f"{batch}: support comparison")
        if batch == "corrected-regression-v2":
            older = read(ROOT / "reports/v2-summary.json")
            check(sum(int(r["calls"]) for r in group) == older["total_calls"], "Regression calls")
            check(
                sum(int(r["input_tokens"]) + int(r["output_tokens"]) for r in group)
                == older["total_tokens"],
                "Regression usage",
            )
            check(
                sum(r["method"] == "skills" and int(r["skill_loads"]) > 0 for r in group)
                == older["variants"]["skills"]["skill_loaded_trials"],
                "Skill use mismatch",
            )
        observed[batch] = {
            "planned": len(plan),
            "observed": len(keys),
            "calls": sum(int(r["calls"]) for r in group),
            **stats,
        }
    check(len(rows) == sum(x["observed"] for x in observed.values()), "Unknown batch")
    return observed


def fair():
    audit = read(ROOT / "reports/fair-v2-final-independent-audit.json")
    freeze = read(ROOT / "reviews/fair-v2-freeze.json")
    for name, wanted in freeze["fingerprint"].items():
        check(sha(ROOT / name) == wanted, f"Frozen implementation changed: {name}")
    check(
        sha(ROOT / "reviews/fair-v2-holdout-tasks.sealed.json") == freeze["sealed_sha256"],
        "Sealed task file changed",
    )
    check(audit["final_acceptance"] and audit["full_comparison_eligible"], "Not final")
    trials = audit["trials"]
    keys = identifiers(trials, ["task", "method", "rep"])
    methods = freeze["methods"]
    sealed = read(ROOT / "reviews/fair-v2-holdout-tasks.sealed.json")["tasks"]
    tasks = {r["id"] for r in sealed}
    check(
        len(sealed) == len(tasks) == 12
        and keys == set(product(tasks, methods, range(freeze["reps"]))),
        "Unbalanced holdout",
    )
    result = {}
    for method in methods:
        attempts = [r["attempts"] for r in trials if r["method"] == method]
        check(all(len(a) == 1 for a in attempts), "Unexpected retry")
        counts = {
            k: sum(a[0][k] for a in attempts)
            for k in ("model_calls", "passed", "input_tokens", "output_tokens")
        }
        check(counts == {k: audit["first_attempt"][method][k] for k in counts}, method)
        result[method] = counts
    return result


def fieldwork():
    kit = read(FIELD / "kit/summary.json")["new_live"]
    haty = read(FIELD / "haty/results.json")
    commerce = read(FIELD / "commerce/results.json")
    aidless = read(FIELD / "aidless/results.json")["live_arms"]
    sql = read(FIELD / "langchain-reference/results.json")["cells"]
    ledger = read(FIELD / "EVIDENCE.json")
    expected_kit = {
        (case, arm, rep)
        for case in ("S+", "E+", "S-", "E-")
        for arm in "ABC"
        for rep in ([1, 2] if case.endswith("+") else [1])
    }
    check(identifiers(kit["cells"], ["case", "arm", "rep"]) == expected_kit, "Kit pairing")
    check(
        identifiers(haty["cells"], ["task", "arch", "rep"])
        == set(product(("T03", "T04", "T06"), ("A", "A1", "B"), (1, 2))),
        "Haty pairing",
    )
    check(
        identifiers(sql, ["case", "mode"])
        == set(product(("sales", "stock"), ("skill", "no_source", "dictionary"))),
        "SQL control missing",
    )
    scores = {}
    for label, rows, arms, key, mark, totals, score_key in [
        ("kit", kit["cells"], "ABC", "arm", "pass", kit["groups_all"], "passes"),
        (
            "haty",
            haty["cells"],
            ["A", "A1", "B"],
            "arch",
            "local_pass",
            haty["groups"],
            "local_strict_pass",
        ),
    ]:
        scores[label] = {}
        for arm in arms:
            selected = [r for r in rows if r[key] == arm]
            passes, calls = sum(r[mark] for r in selected), sum(r["calls"] for r in selected)
            check(len(selected) == 6 and passes == totals[arm][score_key], f"{label}/{arm}: score")
            check(
                calls == totals[arm].get("model_calls", totals[arm].get("calls")),
                f"{label}/{arm}: calls",
            )
            scores[label][arm] = {"passes": passes, "calls": calls}
    check(
        set(aidless) == {"bare", "skill"}
        and all(a["cases"] == 6 and 0 <= a["passed"] <= a["cases"] for a in aidless.values()),
        "Aidless planned denominators",
    )
    check(set(commerce["live"]) == {"shopping", "merchant"}, "Commerce planned roles")
    check(set(kit["new_native_t11"]) == {"app", "single_loop"}, "Kit original applications")
    native = list(kit["new_native_t11"].values())
    counts = {
        "kit": sum(r["calls"] for r in kit["cells"] + native),
        "haty": sum(r["calls"] for r in haty["cells"]),
        "commerce": sum(r["calls"] for r in commerce["live"].values()),
        "aidless": sum(r["calls"] for r in aidless.values()),
        "langchain-reference": sum(r["model_calls"] for r in sql),
    }
    for name, count in counts.items():
        check(count == ledger["request_counts"][name]["calls"], f"{name}: CLI ledger disagreement")
    check(
        sum(counts[k] for k in ("kit", "haty", "commerce", "aidless"))
        == ledger["four_project_model_calls"],
        "Four-project total",
    )
    runs = len(kit["cells"] + native) + len(haty["cells"]) + len(commerce["live"])
    runs += sum(r["cases"] for r in aidless.values())
    return {
        "per_arm": scores,
        "four_project_runs": runs,
        "four_project_calls": ledger["four_project_model_calls"],
        "calls": counts,
        "sql": {
            mode: {
                "passed": sum(r["passed"] for r in sql if r["mode"] == mode),
                "tasks": sum(r["mode"] == mode for r in sql),
            }
            for mode in ("skill", "dictionary", "no_source")
        },
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {"earlier": earlier(), "formal_holdout": fair(), "upstreams": fieldwork()},
            indent=2,
            ensure_ascii=False,
        )
    )
