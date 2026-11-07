"""Common quote/selection seam. It sees public packets, never evaluator truth."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from loop_lab.hard.world import HardWorld


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget: int = Field(ge=0, le=10**9, strict=True)
    deadline: date
    excluded: list[str]
    platform: Literal["any", "A", "B"]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class DecisionWorld(HardWorld):
    def __init__(self, task, directory, representation="compact"):
        public = {
            "id": task["id"],
            "family": task["family"],
            "packets": copy.deepcopy(task["packets"]),
        }
        super().__init__(public, directory, representation)
        self.records = {}
        self.stage_allowed = True
        self.completed_answers = {}

    def _goal(self, requirements):
        value = Requirements.model_validate(requirements).model_dump(mode="json")
        value["excluded"] = sorted(set(value["excluded"]))
        return value

    def update_source(self, packet_id, data):
        """Host-owned source update; this operation is not exposed as an agent tool."""
        with self.lock:
            if packet_id not in self.task["packets"]:
                raise ValueError("Unknown source")
            self.task["packets"][packet_id] = copy.deepcopy(data)
            self.record("source_updated", {"packet_id": packet_id}, {"source_digest": digest(data)})

    def public_bundle_data(self):
        case = self.read_packet("case", "quote-service")["data"]
        policy_id = case["policy_ref"]
        policy = self.read_packet(policy_id, "quote-service")["data"]
        products = []
        for packet in case["catalog_refs"]:
            products.extend(self.read_packet(packet, "quote-service")["data"]["products"])
        return case, policy, products, ["case", policy_id, *case["catalog_refs"]]

    @staticmethod
    def _price(case, policy, products, selected, goal):
        """Independent Decimal implementation; no import of the grading oracle."""
        index = {item["id"]: item for item in products}
        if len(set(selected)) != len(selected) or any(i not in index for i in selected):
            return {"feasible": False, "errors": ["unknown_or_duplicate_sku"]}
        chosen = [index[i] for i in selected]
        errors = []
        if sorted(p["group"] for p in chosen) != sorted(case["groups"]):
            errors.append("one_item_per_required_group")
        concrete_platforms = {p["platform"] for p in chosen if p["platform"] != "universal"}
        if len(concrete_platforms) > 1:
            errors.append("incompatible_platforms")
        if goal["platform"] != "any" and any(p != goal["platform"] for p in concrete_platforms):
            errors.append("requested_platform")
        if any(p["stock"] <= 0 for p in chosen):
            errors.append("out_of_stock")
        if any(date.fromisoformat(p["eta"]) > date.fromisoformat(goal["deadline"]) for p in chosen):
            errors.append("arrival_deadline")
        if any(p["warehouse"] in goal["excluded"] for p in chosen):
            errors.append("excluded_warehouse")
        warehouse_prices = defaultdict(list)
        for p in chosen:
            warehouse_prices[p["warehouse"]].append(Decimal(p["price"]))
        net_total = Decimal(0)
        delivery_total = Decimal(0)
        breakdown = {}
        for warehouse, amounts in warehouse_prices.items():
            subtotal = sum(amounts, Decimal(0))
            rebate = Decimal(0)
            if subtotal >= Decimal(policy["discount_threshold"]):
                rebate = (
                    subtotal * Decimal(policy["discount_bps"]) / Decimal(10000)
                ).to_integral_value(rounding=ROUND_FLOOR)
            net = subtotal - rebate
            delivery = (
                Decimal(0)
                if net >= Decimal(policy["free_shipping_threshold"])
                else Decimal(policy["shipping"][warehouse])
            )
            net_total += net
            delivery_total += delivery
            breakdown[warehouse] = {
                "subtotal": int(subtotal),
                "discount": int(rebate),
                "shipping": int(delivery),
            }
        cap = (net_total * Decimal(policy["credit_bps"]) / Decimal(10000)).to_integral_value(
            rounding=ROUND_FLOOR
        )
        used_credit = min(Decimal(case["funds"]["posted_credit"]), cap)
        cash = int(net_total + delivery_total - used_credit)
        if cash > goal["budget"]:
            errors.append("cash_budget")
        return {
            "selected_ids": sorted(selected),
            "cash_cents": cash,
            "utility": sum(p["utility"] for p in chosen),
            "feasible": not errors,
            "errors": errors,
            "breakdown": breakdown,
            "used_credit": int(used_credit),
            "platforms": sorted(concrete_platforms),
        }

    def assess_candidates(self, candidates, requirements, revision, scope=None):
        try:
            if revision != self.turn:
                return self.record(
                    "assess_candidates",
                    {},
                    {"error": "stale_revision", "current_revision": self.turn},
                )
            if not isinstance(candidates, list) or len(candidates) > 16:
                raise ValueError(
                    "At most 16 candidate SKU lists per call; [] compares existing quotes"
                )
            goal = self._goal(requirements)
            case, policy, products, sources = self.public_bundle_data()
            source_digest = digest({"case": case, "policy": policy, "products": products})
            goal_key = digest(
                {"goal": goal, "revision": revision, "source_digest": source_digest}
            )
            produced = []
            with self.lock:
                for selected in candidates:
                    if not isinstance(selected, list) or not all(
                        isinstance(i, str) for i in selected
                    ):
                        raise ValueError("Each candidate must be a list of SKU IDs")
                    priced = self._price(case, policy, products, selected, goal)
                    if "cash_cents" not in priced:
                        produced.append(priced)
                        continue
                    if scope in {"A", "B"}:
                        owner = priced["platforms"][0] if len(priced["platforms"]) == 1 else "A"
                        if len(priced["platforms"]) > 1 or owner != scope:
                            produced.append(
                                {"selected_ids": sorted(selected), "error": "outside_context"}
                            )
                            continue
                    key = "Q-" + digest({"ids": sorted(selected), "goal_key": goal_key})[:20]
                    quote = {
                        **priced,
                        "quote_id": key,
                        "kind": "bundle",
                        "goal": goal,
                        "goal_key": goal_key,
                        "request_revision": revision,
                        "source_digest": source_digest,
                        "policy_version": policy["version"],
                        "evidence_ids": sources,
                    }
                    self.records[key] = quote
                    produced.append(quote)
                matching = [
                    q
                    for q in self.records.values()
                    if q["kind"] == "bundle"
                    and q["goal_key"] == goal_key
                    and (scope is None or (q["platforms"] or ["A"]) == [scope])
                ]
                valid = sorted(
                    (q for q in matching if q["feasible"]),
                    key=lambda q: (-q["utility"], q["cash_cents"], q["selected_ids"]),
                )
                result = {
                    "best": self.quote_view(valid[0]) if valid else None,
                    "ranked": [self.quote_view(q) for q in valid[:5]],
                    "new_quotes": [self.quote_view(q) for q in produced],
                    "evaluated_count": len(matching),
                    "global_optimality": "not_established",
                    "evidence_ids": sources,
                    "goal_key": goal_key,
                }
                self._save_records()
        except (ValueError, TypeError, KeyError) as exc:
            result = {
                "error": "invalid_assessment",
                "message": str(exc),
                "requirements_schema": Requirements.model_json_schema(),
            }
        return self.record(
            "assess_candidates",
            {"candidates": candidates, "revision": revision, "scope": scope},
            result,
        )

    def audit_selection(self, selections, flagged, revision, scope=None):
        if revision != self.turn:
            return self.record("audit_selection", {}, {"error": "stale_revision"})
        try:
            case = self.read_packet("case", "audit-service")["data"]
            policy = self.read_packet(case["policy_ref"], "audit-service")["data"]
            if (
                not isinstance(selections, dict)
                or not selections
                or not set(selections) <= set(case["branches"])
                or (scope is not None and set(selections) != {scope})
            ):
                raise ValueError("Select declared branches within this worker's scope")
            totals, normalized, known_refs = {}, {}, {}
            sources = ["case", case["policy_ref"]]
            for branch, ids in selections.items():
                packet_id = case["ledger_refs"][branch]
                rows = self.read_packet(packet_id, "audit-service")["data"]["rows"]
                index = {row["id"]: row for row in rows}
                if not isinstance(ids, list) or any(i not in index for i in ids):
                    raise ValueError("Use actual event IDs, not ref names")
                normalized[branch] = sorted(set(ids))
                totals[branch] = sum(index[i]["amount"] for i in normalized[branch])
                known_refs[branch] = {r["ref"] for r in rows if r["kind"] == "posting"}
                sources.append(packet_id)
            if not isinstance(flagged, list) or not all(isinstance(i, str) for i in flagged):
                raise ValueError("flagged must be branch/ref strings")
            for item in flagged:
                branch, ref = item.split("/", 1)
                if branch not in known_refs or ref not in known_refs[branch]:
                    raise ValueError("Flagged refs must exist inside the selected branch scope")
            payload = {
                "totals": totals,
                "flagged": sorted(flagged),
                "policy_version": policy["version"],
                "request_revision": revision,
            }
            key = "A-" + digest({"payload": payload, "selected_events": normalized})[:20]
            result = {
                "record_id": key,
                "kind": "audit",
                "payload": payload,
                "selected_events": normalized,
                "evidence_ids": sources,
                "request_revision": revision,
            }
            result["source_digest"] = self._audit_digest(selections)
            with self.lock:
                self.records[key] = result
                self._save_records()
        except (ValueError, TypeError, KeyError) as exc:
            result = {"error": "invalid_audit_selection", "message": str(exc)}
        return self.record(
            "audit_selection", {"selections": selections, "revision": revision}, result
        )

    def stage_quote(self, quote_id, seen_ids=None):
        with self.lock:
            quote = self.records.get(quote_id)
            if quote is None or quote.get("kind") != "bundle":
                return self.record("stage_result", {}, {"error": "unknown_quote"})
            if quote["request_revision"] != self.turn:
                return self.record("stage_result", {}, {"error": "stale_revision"})
            if seen_ids is not None and quote_id not in seen_ids:
                return self.record("stage_result", {}, {"error": "quote_not_observed"})
            if not quote["feasible"]:
                return self.record(
                    "stage_result", {}, {"error": "infeasible_quote", "reasons": quote["errors"]}
                )
            ranked = sorted(
                (
                    q
                    for q in self.records.values()
                    if q["kind"] == "bundle"
                    and q["goal_key"] == quote["goal_key"]
                    and q["feasible"]
                ),
                key=lambda q: (-q["utility"], q["cash_cents"], q["selected_ids"]),
            )
            if ranked and ranked[0]["quote_id"] != quote_id:
                return self.record(
                    "stage_result",
                    {},
                    {"error": "better_evaluated_quote", "best": self.quote_view(ranked[0])},
                )
            case, policy, products, _ = self.public_bundle_data()
            if (
                digest({"case": case, "policy": policy, "products": products})
                != quote["source_digest"]
            ):
                return self.record("stage_result", {}, {"error": "stale_business_data"})
            payload = {
                k: quote[k]
                for k in (
                    "selected_ids",
                    "cash_cents",
                    "utility",
                    "policy_version",
                    "request_revision",
                )
            }
            return self._stage(
                payload,
                quote["evidence_ids"],
                {"quote_id": quote_id, "goal_snapshot": quote["goal"]},
            )

    def stage_audit(self, record_ids, seen_ids=None):
        try:
            reports = [self.records[i] for i in record_ids]
            if not reports or any(
                r["kind"] != "audit" or r["request_revision"] != self.turn for r in reports
            ):
                raise ValueError("Use current audit record IDs")
            if seen_ids is not None and not set(record_ids) <= set(seen_ids):
                raise ValueError("All audit references must have been observed")
            case = self.task["packets"]["case"]
            totals, flags, sources = {}, [], set()
            for report in reports:
                payload = report["payload"]
                if report["source_digest"] != self._audit_digest(payload["totals"]):
                    raise ValueError("Audit source changed; recompute the selected records")
                if set(totals) & set(payload["totals"]):
                    raise ValueError("Duplicate branch coverage; select one report per branch")
                totals.update(payload["totals"])
                flags.extend(payload["flagged"])
                sources.update(report["evidence_ids"])
            if set(totals) != set(case["branches"]):
                raise ValueError("Cover every branch before staging")
            payload = {
                "totals": totals,
                "flagged": sorted(flags),
                "request_revision": self.turn,
                "policy_version": self.task["packets"][case["policy_ref"]]["version"],
            }
            return self._stage(payload, sorted(sources), {"audit_record_ids": list(record_ids)})
        except (KeyError, ValueError, TypeError) as exc:
            return self.record(
                "stage_result", {}, {"error": "invalid_audit_reference", "message": str(exc)}
            )

    def _stage(self, payload, sources, metadata):
        if not self.stage_allowed:
            return self.record(
                "stage_result", {}, {"error": "analysis_turn_not_authorized_to_stage"}
            )
        out = super().stage_result(payload, seen_packets=sources)
        if "error" in out:
            return out
        for proposal in self.proposals:
            if proposal["receipt_id"] == out["receipt_id"]:
                proposal.update(metadata)
        answer = {"status": "completed", "facts": out["facts"], "receipt_ids": [out["receipt_id"]]}
        self.completed_answers[self.turn] = answer
        (self.directory / "drafts.json").write_text(
            json.dumps(self.proposals, ensure_ascii=False, indent=2)
        )
        return answer

    def _save_records(self):
        target = self.directory / "decision-records.json"
        pending = target.with_suffix(".pending")
        pending.write_text(json.dumps(self.records, ensure_ascii=False, indent=2))
        pending.replace(target)

    @staticmethod
    def quote_view(quote):
        keys = {
            "quote_id",
            "selected_ids",
            "cash_cents",
            "utility",
            "feasible",
            "errors",
            "error",
            "policy_version",
        }
        return {k: v for k, v in quote.items() if k in keys}

    def _audit_digest(self, branches):
        case = self.task["packets"]["case"]
        data = {"policy": self.task["packets"][case["policy_ref"]], "case": case}
        for branch in sorted(branches):
            packet = self.task["packets"][case["ledger_refs"][branch]]
            data[branch] = [
                {k: v for k, v in row.items() if k != "observability"} for row in packet["rows"]
            ]
        return digest(data)

    def export(self):
        with self.lock:
            return {**super().export(), "records": copy.deepcopy(self.records)}
