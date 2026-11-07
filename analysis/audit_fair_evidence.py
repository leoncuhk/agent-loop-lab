"""Independently audit a frozen fair study. Standard library only; read-only, JSON to stdout."""

import argparse
import hashlib
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ["tools", "eager", "skills", "multi"]
LIMITS = [
    "Known tokens are CLI telemetry, not a service-side bill; unknown usage remains unknown.",
    "Worker calls prove separate model contexts ran, not that delegation caused the outcome.",
    "Grades concern final synthetic delivery, not interim reasoning or natural-language quality.",
    "Per-request start IDs are not stored in usage.jsonl: match intent/finish IDs and role totals, "
    "then match numbered requests, stdout and usage independently.",
]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def jsonl(path, *, live=False):
    with path.open("rb") as stream:
        # A running append may have a partial final line; never grade it before flush.
        return [
            json.loads(line)
            for line in stream
            if line.strip() and (not live or line.endswith(b"\n"))
        ]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def goal_key(goal):
    return canonical({**goal, "excluded": sorted(set(goal["excluded"]))})


def final_goal(task):
    goal = task["packets"]["case"]["requirements"].copy()
    if task["family"] == "revision":
        messages = task["messages"]
        update = re.search(
            r"平台限定为([AB])，仓库排除清单改为([^；]+)；截止日期改为(\d{4}-\d{2}-\d{2})",
            messages[1],
        )
        budget = re.search(r"现金上限最终改为(\d+)分", messages[2])
        need(update and budget, "unsupported revision wording; independent goal not verified")
        platform, excluded, deadline = update.groups()
        goal.update(
            platform=platform,
            excluded=[] if excluded == "无" else excluded.split(","),
            deadline=deadline,
            budget=int(budget[1]),
        )
    need(
        goal_key(goal) == goal_key(task["latest_case"]["requirements"]),
        "evaluator goal differs from public messages",
    )
    return goal


class PublicTruth:
    """Rebuild truth from public policies, catalogs, ledgers and sequential user messages."""

    def __init__(self, task):
        self.task = task
        packets = task["packets"]
        self.case = packets["case"]
        self.policy = packets[self.case["policy_ref"]]
        self.audit = task["family"] == "audit"
        need(len(task["messages"]) == task["final_revision"] + 1, "revision count mismatch")
        if self.audit:
            self.ledgers, self.events, self.totals, self.flags = {}, {}, {}, []
            for branch, ref in self.case["ledger_refs"].items():
                ledger, latest = {}, {}
                for row in packets[ref]["rows"]:
                    rid = row["id"]
                    fields = {k: v for k, v in row.items() if k != "observability"}
                    need(rid not in ledger or ledger[rid] == fields, f"conflicting event ID {rid}")
                    ledger[rid] = fields
                    if row["kind"] == "posting" and row["at"] <= self.policy["cutoff"]:
                        prev = latest.get(row["ref"])
                        if prev is None or row["seq"] > prev["seq"]:
                            latest[row["ref"]] = row
                self.ledgers[branch] = ledger
                selected = sorted(r["id"] for r in latest.values() if r["state"] == "approved")
                self.events[branch] = selected
                self.totals[branch] = sum(ledger[i]["amount"] for i in selected)
                self.flags.extend(
                    f"{branch}/{r['ref']}"
                    for r in latest.values()
                    if r["state"] == "pending" and r["amount"] > self.policy["flag_threshold"]
                )
            self.flags.sort()
            need(
                task["expected"] == {"totals": self.totals, "flagged": self.flags},
                "audit gold differs from public truth",
            )
        else:
            self.goal = final_goal(task)
            catalogs = [packets[ref]["products"] for ref in self.case["catalog_refs"]]
            self.skus = {p["id"]: p for catalog in catalogs for p in catalog}
            self.source_digest = digest(
                {"case": self.case, "policy": self.policy, "products": sum(catalogs, [])}
            )
            choices = itertools.product(*[[p["id"] for p in catalog] for catalog in catalogs])
            priced = (self.price(ids, self.goal) for ids in choices)
            self.best = min(
                (p for p in priced if p["feasible"]),
                key=lambda p: (-p["utility"], p["cash_cents"], p["selected_ids"]),
                default=None,
            )
            need(self.best is not None, "generator defect: no feasible purchase; keep seed")
            need(
                all(
                    task["expected"].get(k) == self.best[k]
                    for k in ("selected_ids", "cash_cents", "utility")
                ),
                "purchase gold differs from exhaustive optimum",
            )

    def price(self, ids, goal):
        selected = [self.skus[i] for i in ids]
        platforms = {p["platform"] for p in selected} - {"universal"}
        ok = (
            len(set(ids)) == len(ids)
            and sorted(p["group"] for p in selected) == sorted(self.case["groups"])
            and len(platforms) <= 1
            and (goal["platform"] == "any" or platforms <= {goal["platform"]})
            and all(
                p["stock"] > 0
                and p["eta"] <= goal["deadline"]
                and p["warehouse"] not in goal["excluded"]
                for p in selected
            )
        )
        warehouses = defaultdict(int)
        for p in selected:
            warehouses[p["warehouse"]] += p["price"]
        net = shipping = 0
        for warehouse, subtotal in warehouses.items():
            discount = (
                subtotal * self.policy["discount_bps"] // 10000
                if subtotal >= self.policy["discount_threshold"]
                else 0
            )
            after = subtotal - discount
            net += after
            if after < self.policy["free_shipping_threshold"]:
                shipping += self.policy["shipping"][warehouse]
        credit = min(self.case["funds"]["posted_credit"], net * self.policy["credit_bps"] // 10000)
        cash = net + shipping - credit
        return dict(
            selected_ids=sorted(ids),
            cash_cents=cash,
            utility=sum(p["utility"] for p in selected),
            feasible=ok and cash <= goal["budget"],
        )


def assignment(request):
    for message in reversed(request["conversation"]):
        if message["role"] in ("user", "human"):
            try:
                item = json.loads(message["content"])
                if isinstance(item, dict) and "scope" in item and "revision" in item:
                    return item
            except (ValueError, TypeError):
                pass
    raise ValueError("worker request without coordinator assignment")


def scan_transport(model, outcome, settings, warnings):
    usage = jsonl(model / "usage.jsonl") if (model / "usage.jsonl").exists() else []
    starts = (
        jsonl(model / "attempt-starts.jsonl") if (model / "attempt-starts.jsonl").exists() else []
    )
    ends = (
        jsonl(model / "attempt-finishes.jsonl")
        if (model / "attempt-finishes.jsonl").exists()
        else []
    )
    need(usage == outcome["model_records"], "raw usage differs from result records")

    def ids(entries):
        return Counter((r["request_id"], r["role"]) for r in entries)

    need(
        ids(starts) == ids(ends) and all(v == 1 for v in ids(starts).values()),
        "intent/finish ID or role mismatch",
    )
    need(
        sorted(r["sequence"] for r in starts) == list(range(1, len(starts) + 1)),
        "invalid intent sequence",
    )
    need(
        Counter(r["role"] for r in ends if r["status"] == "completed")
        == Counter(r["role"] for r in usage if r["status"] == "ok"),
        "successful intents/transport roles differ",
    )
    need(
        not (Counter(r["role"] for r in usage) - Counter(r["role"] for r in starts)),
        "transport without matching role intent",
    )
    if len(starts) != len(usage):
        need(
            outcome["error"] and "budget" in outcome["error"].lower(),
            "unexplained pre-dispatch attempts",
        )
        warnings["pre_dispatch_failed_intents"] += len(starts) - len(usage)
    need(
        sorted(r["call"] for r in usage) == list(range(1, len(usage) + 1)),
        "missing/duplicate transport call",
    )
    expected_prefix = {f"call-{r['call']:03d}-{r['role']}" for r in usage}
    need(
        {p.name.removesuffix(".request.json") for p in model.glob("call-*.request.json")}
        == expected_prefix,
        "request files differ from usage",
    )
    need(
        not (
            {p.name.removesuffix(".stdout.jsonl") for p in model.glob("call-*.stdout.jsonl")}
            - expected_prefix
        ),
        "stdout without corresponding call",
    )
    candidates, audits, delegation, last_main, stages = set(), set(), set(), None, []
    for r in sorted(usage, key=lambda x: x["call"]):
        role = r["role"]
        prefix = model / f"call-{r['call']:03d}-{role}"
        need(
            r["model"] == settings["model"] and r["effort"] == settings["effort"],
            "model/effort drift",
        )
        request = read(prefix.with_suffix(".request.json"))
        worker = assignment(request) if role.startswith("worker-") else None
        if worker:
            need(role == "worker-" + worker["scope"].replace(":", "-"), "assignment/role mismatch")
        stdout = prefix.with_suffix(".stdout.jsonl")
        events = []
        if stdout.exists():
            for line in stdout.read_bytes().splitlines():
                if line.startswith(b"{"):
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        need(r.get("error") == "TimeoutExpired", "invalid JSON stdout")
                        warnings["truncated_timeout_stdout_lines"] += 1
        else:
            need(
                not r["usage"] and r["status"] != "ok",
                "missing raw stdout for completed/known call",
            )
            warnings["failed_calls_without_stdout"] += 1
        if r.get("error") == "TimeoutExpired":
            need(prefix.with_suffix(".timeout.txt").exists(), "timeout lacks timeout artifact")
        completed = [e.get("usage", {}) for e in events if e["type"] == "turn.completed"]
        need(
            (completed[-1] if completed else {}) == r["usage"],
            "stdout token usage differs from ledger",
        )
        if r.get("service_error"):
            raw_errors = " ".join(
                str(e.get("error", e.get("message", "")))
                for e in events
                if e["type"] in ("error", "turn.failed")
            ).lower()
            signals = {
                "usage_limit": ("usage limit", "insufficient_quota"),
                "authentication": (
                    "not logged in",
                    "access token",
                    "sign in again",
                    "unauthorized",
                ),
            }
            need(
                any(s in raw_errors for s in signals.get(r["service_error"], ())),
                "service error not supported by raw CLI",
            )
        if r["status"] != "ok":
            continue
        need(
            not any(
                e.get("item", {}).get("type") not in (None, "agent_message", "reasoning", "error")
                for e in events
            ),
            "unexpected host action",
        )
        messages = [
            e["item"]["text"] for e in events if e.get("item", {}).get("type") == "agent_message"
        ]
        decision = json.loads(messages[-1])
        if role == "main":
            last_main = decision
        for call in decision["tool_calls"]:
            args = json.loads(call["arguments_json"])
            name = call["name"]
            if name == "assess_candidates":
                goal = worker["requirements"] if worker else args.get("requirements")
                rev = worker["revision"] if worker else args.get("request_revision")
                # Invalid tool requests may legitimately have no registered record.
                if (
                    isinstance(goal, dict)
                    and isinstance(goal.get("excluded"), list)
                    and isinstance(args.get("candidates"), list)
                ):
                    for ids_ in args["candidates"]:
                        if isinstance(ids_, list) and all(isinstance(i, str) for i in ids_):
                            candidates.add((tuple(sorted(ids_)), goal_key(goal), rev))
            elif name == "audit_selection":
                sels = (
                    {worker["scope"].split(":", 1)[1]: args.get("event_ids")}
                    if worker
                    else args.get("selections")
                )
                if (
                    isinstance(sels, dict)
                    and all(
                        isinstance(v, list) and all(isinstance(i, str) for i in v)
                        for v in sels.values()
                    )
                    and isinstance(args.get("flagged"), list)
                    and all(isinstance(i, str) for i in args["flagged"])
                ):
                    audits.add(
                        (
                            canonical({k: sorted(set(v)) for k, v in sels.items()}),
                            tuple(sorted(args["flagged"])),
                            worker["revision"] if worker else args.get("request_revision"),
                        )
                    )
            elif name in ("investigate_branch", "investigate_bundle"):
                delegation.add(
                    "worker-branch-" + str(args.get("branch"))
                    if name == "investigate_branch"
                    else "worker-" + str(args.get("platform"))
                )
            elif (
                name in ("stage_quote", "stage_audit")
                and role == "main"
                and len(decision["tool_calls"]) == 1
            ):
                refs = (
                    [args.get("quote_id")] if name == "stage_quote" else args.get("record_ids", [])
                )
                observed = [m["content"] for m in request["conversation"] if m["role"] == "tool"]
                if isinstance(refs, list) and all(
                    isinstance(i, str) and any(i in c for c in observed) for i in refs
                ):
                    stages.append((r["call"], refs))
    roles = {r["role"] for r in usage if r["role"].startswith("worker-")}
    spans = {"worker-" + s["scope"].replace(":", "-") for s in outcome["delegation_spans"]}
    need(
        roles <= delegation and roles <= spans, "worker role not evidenced by main delegation/spans"
    )
    counts = dict(
        model_calls=len(usage),
        main_calls=sum(r["role"] == "main" for r in usage),
        worker_calls=sum(r["role"].startswith("worker-") for r in usage),
        worker_count=len(roles),
        input_tokens=sum(r["usage"].get("input_tokens", 0) for r in usage),
        output_tokens=sum(r["usage"].get("output_tokens", 0) for r in usage),
        unknown_usage_requests=sum(not r["usage"] for r in usage),
        actual_topology="delegated" if roles else "single",
        transport_timeout=any(r.get("error") == "TimeoutExpired" for r in usage),
        service_error=next((r["service_error"] for r in usage if r.get("service_error")), None),
    )
    need(all(outcome[k] == v for k, v in counts.items()), "transport rollup differs from attempt")
    need(counts["main_calls"] + counts["worker_calls"] == len(usage), "unrecognized model role")
    counts["worker_role_calls"] = dict(Counter(r["role"] for r in usage if r["role"] in roles))
    return (
        counts,
        candidates,
        audits,
        last_main,
        stages,
        max((r["call"] for r in usage if r["role"] == "main"), default=None),
    )


def audit_attempt(path, truth, settings, identifier, index, warnings):
    r = read(path / "result.json")
    task_id, method, rep = identifier
    need(
        (r["task_id"], r["method"], r["rep"], r["attempt"]) == (*identifier, index),
        "attempt identity mismatch",
    )
    ledger = read(path / "attempt.json")
    need(
        ledger["status"] == "finished"
        and ledger["index"] == index
        and ledger["error"] == r["error"],
        "attempt ledger incomplete",
    )
    counts, candidates, audits, last, stages, last_n = scan_transport(
        path / "model", r, settings, warnings
    )
    need(method == "multi" or not counts["worker_calls"], "single method ran worker")
    world = r["world"]
    events = world["events"]
    need(
        (jsonl(path / "events.jsonl") if (path / "events.jsonl").exists() else []) == events,
        "raw world events differ from result",
    )
    records = world["records"]
    need(
        (read(path / "decision-records.json") if (path / "decision-records.json").exists() else {})
        == records,
        "registered records file differs",
    )
    need(
        (read(path / "drafts.json") if (path / "drafts.json").exists() else [])
        == world["proposals"],
        "draft store differs",
    )
    for ref, record in records.items():
        if truth.audit:
            sels = record["selected_events"]
            payload = record["payload"]
            need(
                (canonical(sels), tuple(payload["flagged"]), record["request_revision"]) in audits,
                "audit record not traceable to model-selected events and flags",
            )
            sums = {b: sum(truth.ledgers[b][i]["amount"] for i in ids) for b, ids in sels.items()}
            need(
                ref == record["record_id"] and sums == payload["totals"],
                "audit service arithmetic or reference mismatch",
            )
        else:
            ids = record["selected_ids"]
            need(
                (tuple(ids), goal_key(record["goal"]), record["request_revision"]) in candidates,
                "quote not traceable to model candidate/goal/revision",
            )
            priced = truth.price(ids, record["goal"])
            goal_digest = digest(
                {
                    "goal": record["goal"],
                    "revision": record["request_revision"],
                    "source_digest": truth.source_digest,
                }
            )
            expected_id = "Q-" + digest({"ids": sorted(ids), "goal_key": goal_digest})[:20]
            need(
                (ref, record["source_digest"], record["goal_key"])
                == (expected_id, truth.source_digest, goal_digest),
                "quote identity does not bind its public data/goal/revision",
            )
            need(
                ref == record["quote_id"] and all(record[k] == v for k, v in priced.items()),
                f"independent quote mismatch {ref}",
            )
    # A business failure is valid evidence if the independent grade agrees.
    last_rev = truth.task["final_revision"]
    proposals = [p for p in world["proposals"] if p["turn"] == last_rev]
    answer = r["answer"] or {}
    proposal = proposals[0] if len(proposals) == 1 else None
    receipt = proposal["receipt_id"] if proposal else None
    delivered = bool(
        proposal and answer.get("status") == "completed" and answer.get("receipt_ids") == [receipt]
    )
    hosts = [e for e in events if e["tool"] == "host_finalization"]
    if delivered:
        refs = proposal["audit_record_ids"] if truth.audit else [proposal["quote_id"]]
        need(
            last and last["final"] is None and (last_n, refs) in stages,
            "delivery not traced to final observed stage without extra model call",
        )
        need(
            len(hosts) == 1 and hosts[0]["result"]["receipt_ids"] == [receipt],
            "host finalization/receipt mismatch",
        )
    feasible = optimal = False
    correct = delivered and not r["error"]
    if proposal:
        payload = proposal["payload"]
        facts = answer.get("facts", [])
        expected = {k: v if isinstance(v, str) else canonical(v) for k, v in payload.items()}
        correct = (
            correct
            and {f["key"]: f["value"] for f in facts} == expected
            and all(f["source"] == receipt and expected.get(f["key"]) == f["value"] for f in facts)
        )
        correct = (
            correct
            and payload["policy_version"] == truth.policy["version"]
            and payload["request_revision"] == last_rev
        )
        if truth.audit:
            selected, amounts, flags = {}, {}, []
            for ref in proposal["audit_record_ids"]:
                record = records[ref]
                need(not (selected.keys() & record["selected_events"].keys()), "duplicate branch")
                selected.update(record["selected_events"])
                amounts.update(record["payload"]["totals"])
                flags.extend(record["payload"]["flagged"])
            need(
                amounts == payload["totals"] and sorted(flags) == payload["flagged"],
                "staged audit payload detached from registered selections",
            )
            feasible = payload["totals"] == truth.totals and payload["flagged"] == truth.flags
            optimal = feasible  # Published audit metric; event correctness is checked separately.
            correct = correct and feasible and selected == truth.events
            need(
                r["grade"].get("event_selection_correct") == (selected == truth.events),
                "audit event-selection grade mismatch",
            )
        else:
            q = records[proposal["quote_id"]]
            need(
                all(
                    payload[k] == q[k]
                    for k in (
                        "cash_cents",
                        "utility",
                        "selected_ids",
                        "policy_version",
                        "request_revision",
                    )
                ),
                "staged payload detached from quote",
            )
            priced = truth.price(payload["selected_ids"], truth.goal)
            feasible = priced["feasible"]
            optimal = feasible and priced["selected_ids"] == truth.best["selected_ids"]
            same_goal = goal_key(proposal["goal_snapshot"]) == goal_key(truth.goal)
            correct = (
                correct
                and optimal
                and same_goal
                and all(payload[k] == priced[k] for k in ("cash_cents", "utility"))
            )
            need(r["grade"].get("goal_consistent") == same_goal, "goal-consistency grade mismatch")
        required = set(truth.task["required_packets"])
        reads = {
            e["args"]["packet_id"]
            for e in events
            if e["tool"] == "read_packet" and "data" in e["result"]
        }
        correct = correct and required <= reads and required <= set(proposal["seen_packets"])
    correct = bool(
        correct
        and not any(
            e["turn"] < last_rev or e["result"].get("error") == "stale_revision"
            for e in events
            if e["tool"] == "stage_result"
        )
    )
    need(
        (correct, feasible, optimal)
        == tuple(r["grade"][k] for k in ("passed", "feasible", "optimal")),
        "independent delivery/feasibility/optimum grade mismatch",
    )
    return {
        **counts,
        "delivered": delivered,
        "passed": correct,
        "feasible": feasible,
        "optimal": optimal,
        "quotes_repriced": 0 if truth.audit else len(records),
        "audit_records_checked": len(records) if truth.audit else 0,
        "host_finalizations": len(hosts),
        "failures": r["grade"]["failures"],
        "error": r["error"],
        "grade": r["grade"],
    }


def trial_key(row):
    return row["task_id"], row["method"], row["rep"]


def audit_trial(run, row, truth, settings, warnings):
    key = trial_key(row)
    task_id, method, rep = key
    path = run / f"{task_id}-{method}-r{rep}"
    need(read(path / "trial.json") == row, "trial file differs from batch log")
    attempts = sorted(path.glob("attempt-*"))
    need(
        1 <= len(attempts) == row["attempts"] <= 1 + settings["technical_recoveries"],
        "attempt count",
    )
    need(
        [p.name for p in attempts] == [f"attempt-{i}" for i in range(len(attempts))],
        "attempt sequence",
    )
    history = [audit_attempt(p, truth, settings, key, i, warnings) for i, p in enumerate(attempts)]
    left, tokens = settings["task_calls"], settings["known_token_threshold"]
    for i, a in enumerate(history):
        need(
            left > 0 and tokens >= settings["unknown_reservation"],
            "attempt dispatched without resources",
        )
        left -= a["model_calls"]
        tokens -= (
            a["input_tokens"]
            + a["output_tokens"]
            + a["unknown_usage_requests"] * settings["unknown_reservation"]
        )
        need(left >= 0, "shared task call cap violated")
        should_retry = (
            i < settings["technical_recoveries"]
            and a["transport_timeout"]
            and left > 0
            and tokens >= settings["unknown_reservation"]
        )
        need(
            should_retry == (i + 1 < len(history)),
            "retry outside frozen timeout/remaining-budget policy",
        )
    for label, a in (("first", history[0]), ("final", history[-1])):
        need(
            row[f"{label}_grade"] == a["grade"]
            and row[f"{label}_passed"] == a["passed"]
            and row[f"{label}_error"] == a["error"],
            f"{label} trial/attempt mismatch",
        )
    for k in (
        "model_calls",
        "input_tokens",
        "output_tokens",
        "unknown_usage_requests",
        "worker_calls",
    ):
        need(row[k] == sum(a[k] for a in history), f"{k} not cumulative over attempts")
    need(
        row["worker_count"] == history[-1]["worker_count"]
        and row["actual_topology"] == history[-1]["actual_topology"],
        "trial topology differs from final attempt",
    )
    need(
        row["transport_fault"] == any(a["transport_timeout"] for a in history),
        "trial transport flag differs",
    )
    need(
        row["service_error"]
        == next((a["service_error"] for a in history if a["service_error"]), None),
        "trial service flag differs",
    )
    return [{k: v for k, v in a.items() if k != "grade"} for a in history]


def phase_table(histories, methods, assigned, recover):
    table = {}
    for method in methods:
        observed = [h for (_, m, _), h in histories.items() if m == method]
        selected = [h[-1] if recover else h[0] for h in observed]
        charged = [a for h in observed for a in (h if recover else h[:1])]
        table[method] = {
            "assigned": assigned,
            "verified_trials": len(observed),
            **{
                k: sum(a[k] for a in selected)
                for k in ("delivered", "passed", "feasible", "optimal")
            },
            "pass_rate_all_assigned": sum(a["passed"] for a in selected) / assigned,
            "delivery_rate_all_assigned": sum(a["delivered"] for a in selected) / assigned,
            "transport_timeouts_in_charged_attempts": sum(a["transport_timeout"] for a in charged),
            "service_faults_in_charged_attempts": sum(bool(a["service_error"]) for a in charged),
            "delegated_outcome_trials": sum(a["actual_topology"] == "delegated" for a in selected),
            **{
                k: sum(a[k] for a in charged)
                for k in (
                    "model_calls",
                    "worker_calls",
                    "input_tokens",
                    "output_tokens",
                    "unknown_usage_requests",
                    "quotes_repriced",
                    "audit_records_checked",
                    "host_finalizations",
                )
            },
        }
    return table


def audit_study(args):
    run = args.run.resolve()
    manifest = read(run / "manifest.json")
    finished = bool(manifest.get("finished_at"))
    if not args.partial and (not finished or not (run / "summary.json").exists()):
        return {
            "state": "not_ready",
            "final_acceptance": False,
            "reason": "Finalized manifest and summary required; --partial is diagnostic only.",
        }, 2
    receipt = read(args.freeze)
    holdout = manifest["phase"] == "sealed-holdout"
    need(holdout or manifest["phase"] == "development", "unsupported study phase")
    tasks_list = read(run / "tasks.json")
    tasks = {t["id"]: t for t in tasks_list}
    methods, reps = manifest["methods"], manifest["reps"]
    need(methods == METHODS == receipt["methods"], "not the frozen four methods")
    need(
        len(tasks) == len(tasks_list) == manifest["task_count"] == (12 if holdout else 3),
        "task count differs from protocol",
    )
    need(
        reps == (receipt["reps"] if holdout else 1) == (2 if holdout else 1),
        "repetitions differ from protocol",
    )
    for field in ("settings", "knowledge_hashes", "fingerprint"):
        need(manifest[field] == receipt[field], f"freeze mismatch: {field}")
    for rel, checksum in manifest["fingerprint"].items():
        need(sha(run / "source-snapshot" / rel) == checksum, f"snapshot checksum mismatch: {rel}")
    sealed_checked = False
    if holdout and not args.partial:
        sealed = args.sealed or Path(receipt["sealed_tasks_path"])
        raw = sealed.read_bytes()
        need(
            hashlib.sha256(raw).hexdigest() == receipt["sealed_sha256"],
            "sealed checksum differs from freeze",
        )
        expected_tasks = [
            {**t, "library": "large" if i % 2 else "small"}
            for i, t in enumerate(json.loads(raw)["tasks"])
        ]
        need(
            tasks_list == expected_tasks,
            "runtime task material/library differs from frozen dataset",
        )
        sealed_checked = True
    if holdout:
        sizes = Counter(
            (
                t["family"],
                len(t["packets"]["case"]["branches" if t["family"] == "audit" else "groups"]),
            )
            for t in tasks_list
        )
        expected_sizes = (
            {("planning", n): 2 for n in (3, 4, 5)}
            | {("revision", n): 1 for n in (3, 4, 5)}
            | {("audit", 3): 1, ("audit", 4): 2}
        )
        need(sizes == expected_sizes, "preassigned family/size panel differs")
    else:
        need(set(tasks) == {"DEV-P2", "DEV-R2", "DEV-A0"}, "incorrect development panel")
        need(
            sha(run / "manifest.json") == receipt["development_manifest_sha256"]
            and sha(run / "trials.jsonl") == receipt["development_trials_sha256"],
            "not the development run pinned by the freeze",
        )
    blocks = [(b["task_id"], b["rep"]) for b in manifest["blocks"]]
    planned = {(t, m, r) for t in tasks for m in methods for r in range(reps)}
    need(
        Counter(blocks) == Counter(itertools.product(tasks, range(reps))),
        "manifest block list is incomplete or duplicate",
    )
    need(
        len(planned) == manifest["planned_trials"] == (96 if holdout else 12),
        "planned trial allocation differs",
    )
    rows = jsonl(run / "trials.jsonl", live=args.partial and not finished)
    counts = Counter(trial_key(row) for row in rows)
    issues, warnings, histories, truths = [], Counter(), {}, {}
    missing = planned - set(counts)
    extras = set(counts) - planned
    duplicates = [k for k, count in counts.items() if count > 1]
    if extras or duplicates:
        issues.append(f"unexpected keys: {sorted(extras)}; duplicates: {sorted(duplicates)}")
    # A stopped final report still checks the planned generators, without inventing outcomes.
    inspect_tasks = set(tasks) if not args.partial else {k[0] for k in counts if k in planned}
    for task_id in sorted(inspect_tasks):
        try:
            truths[task_id] = PublicTruth(tasks[task_id])
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            issues.append(f"{task_id}: independent task oracle: {exc}")
    for row in rows:
        key = trial_key(row)
        if key not in planned or counts[key] != 1 or key[0] not in truths:
            continue
        try:
            histories[key] = audit_trial(run, row, truths[key[0]], manifest["settings"], warnings)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            issues.append(f"{key}: {type(exc).__name__}: {exc}")
    first = phase_table(histories, methods, len(tasks) * reps, False)
    after = phase_table(histories, methods, len(tasks) * reps, True)
    stop = manifest.get("stop_reason")
    valid_stop = False
    comparison = False
    if finished:
        disk_dirs = {p.parent.name for p in run.glob("*/attempt-*")}
        logged_dirs = {f"{t}-{m}-r{r}" for t, m, r in counts}
        if disk_dirs != logged_dirs:
            issues.append(f"orphan/missing trial directories: {sorted(disk_dirs ^ logged_dirs)}")
        if manifest.get("completed_trials") != len(rows) or Counter(
            trial_key(x) for x in manifest.get("not_run", [])
        ) != Counter(missing):
            issues.append("closed manifest completed/not_run do not reconcile with allocations")
        for field, derived in {
            "actual_calls": sum(a["model_calls"] for a in after.values()),
            "known_tokens": sum(a["input_tokens"] + a["output_tokens"] for a in after.values()),
            "unknown_usage_requests": sum(a["unknown_usage_requests"] for a in after.values()),
        }.items():
            if manifest.get(field) != derived:
                issues.append(f"{field}: manifest {manifest.get(field)} vs verified {derived}")
        if stop and stop.startswith("systemic_model_service:"):
            tag = stop.partition(":")[2]
            completed_n = len(rows) // len(methods)
            prefix = {(t, m, r) for t, r in blocks[:completed_n] for m in methods}
            stopping = (
                set(k for k in counts if (k[0], k[2]) == blocks[completed_n - 1])
                if completed_n
                else set()
            )
            valid_stop = (
                tag in ("usage_limit", "authentication")
                and prefix == set(counts)
                and any(
                    a["service_error"] == tag for key in stopping for a in histories.get(key, [])
                )
            )
        elif stop == "study_error:RuntimeError:Frozen source changed; study stopped":
            valid_stop = manifest.get("source_unchanged") is False
        if stop and not valid_stop:
            issues.append(f"stop requires manual evidence review: {stop}")
        if missing and not valid_stop:
            issues.append("missing allocations without verified protocol stop")
        if not manifest.get("source_unchanged") and not valid_stop:
            issues.append("source integrity was not confirmed")
        valid_file = run / "VALIDITY.json"
        validity = read(valid_file) if valid_file.exists() else {}
        comparison = (
            not stop
            and not missing
            and not extras
            and not duplicates
            and manifest.get("source_unchanged") is True
            and not any(r.get("service_error") for r in rows)
            and validity.get("eligible_for_architecture_comparison", True)
        )
        if (run / "summary.json").exists():
            summary = read(run / "summary.json")
            if summary.get("complete_balanced") != comparison:
                issues.append("published balance/validity disagrees with independent check")
            for m in methods:
                s = summary["methods"].get(m, {})
                if not s and not first[m]["verified_trials"]:
                    continue
                expected = {
                    "trials": first[m]["verified_trials"],
                    "assigned_trials": first[m]["assigned"],
                    "first_passed": first[m]["passed"],
                    "recovery_passed": after[m]["passed"],
                    "first_delivery": first[m]["delivered"],
                    "recovery_delivery": after[m]["delivered"],
                    "first_feasible": first[m]["feasible"],
                    "first_optimal": first[m]["optimal"],
                    "unknown_usage_requests": after[m]["unknown_usage_requests"],
                    "input_tokens_known": after[m]["input_tokens"],
                    "output_tokens_known": after[m]["output_tokens"],
                    "delegated_trials": after[m]["delegated_outcome_trials"],
                }
                for k, v in expected.items():
                    if s.get(k) != v:
                        issues.append(f"published {m}/{k}: {s.get(k)} vs independent {v}")
        elif not args.partial:
            issues.append("closed run lacks derived summary")
    terminal = finished and (not missing or valid_stop) and not args.partial
    state = (
        "partial_diagnostic"
        if args.partial
        else ("stopped" if valid_stop else "complete" if terminal else "unreconciled")
    )
    if issues:
        state += "_with_mismatches"
    return {
        "state": state,
        "phase": manifest["phase"],
        "final_acceptance": terminal and not issues,
        "full_comparison_eligible": bool(terminal and comparison and not issues),
        "planned": len(planned),
        "logged": len(rows),
        "verified_trials": len(histories),
        "independently_verified_task_oracles": len(truths),
        "stop_reason": stop,
        "missing": sorted(missing),
        "freeze_and_snapshot_match": True,
        "sealed_material_verified": sealed_checked,
        "evidence_sha256": {
            "auditor": sha(Path(__file__)),
            "freeze_receipt": sha(args.freeze),
            **(
                {
                    "final_manifest": sha(run / "manifest.json"),
                    "final_trials": sha(run / "trials.jsonl"),
                }
                if finished and not args.partial
                else {}
            ),
        },
        "first_attempt": first,
        "after_timeout_policy_cumulative_cost": after,
        "trials": [
            {"task": k[0], "method": k[1], "rep": k[2], "attempts": h}
            for k, h in sorted(histories.items())
        ],
        "issues": issues,
        "diagnostic_counts": dict(warnings),
        "limits": LIMITS,
    }, (1 if issues else 0 if terminal or args.partial else 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed fair-dev-v3 or fair-holdout-v2 directory")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Audit completed trial rows only; always marks output non-final",
    )
    parser.add_argument("--freeze", type=Path, default=ROOT / "reviews/fair-v2-freeze.json")
    parser.add_argument(
        "--sealed",
        type=Path,
        help="Optional relocated original sealed material; final holdout only",
    )
    args = parser.parse_args()
    try:
        result, code = audit_study(args)
    except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
        result = {
            "state": "evidence_error",
            "final_acceptance": False,
            "issues": [f"{type(error).__name__}: {error}"],
        }
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
