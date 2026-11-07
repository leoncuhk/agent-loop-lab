"""Read-only evidence, restricted arithmetic, and local drafts; no oracle tool."""

import ast
import copy
import json
import operator
import threading
from decimal import Decimal, localcontext


def calculate(expression):
    if len(expression) > 16000:
        raise ValueError("Expression too long")
    tree = ast.parse(expression, mode="eval")
    stack = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 64:
            raise ValueError("Expression too deep")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    if sum(1 for _ in ast.walk(tree)) > 2500:
        raise ValueError("Expression too complex")
    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
    }

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 10**12:
                raise ValueError("Numeric literal magnitude must be <= 1e12")
            return Decimal(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -visit(node.operand) if isinstance(node.op, ast.USub) else visit(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, (ast.List, ast.Tuple)):
            return [visit(n) for n in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [visit(n) for n in node.args]
            if node.func.id == "sum" and len(args) == 1 and isinstance(args[0], list):
                return sum(args[0], Decimal(0))
            if node.func.id in ("min", "max") and args:
                return (min if node.func.id == "min" else max)(*args)
        raise ValueError("Only numeric arithmetic, sum, min and max are supported")

    with localcontext() as context:
        context.prec = 40
        result = visit(tree.body)
        if not isinstance(result, Decimal) or not result.is_finite():
            raise ValueError("Result must be a finite number")
        return format(result, "f")


class HardWorld:
    def __init__(self, task, directory, representation):
        self.task = task
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.representation = representation
        self.turn = 0
        self.events = []
        self.proposals = []
        self.lock = threading.RLock()

    def record(self, name, args, result):
        with self.lock:
            event = {"tool": name, "args": args, "result": result, "turn": self.turn}
            self.events.append(event)
            with (self.directory / "events.jsonl").open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return result

    def read_packet(self, packet_id, reader="main"):
        packet = self.task["packets"].get(packet_id)
        if packet is None:
            result = {"error": "not_found", "available_ids": sorted(self.task["packets"])}
        else:
            data = copy.deepcopy(packet)
            if self.representation == "compact" and "rows" in data:
                data["rows"] = [
                    {k: v for k, v in row.items() if k != "observability"} for row in data["rows"]
                ]
            result = {"source": packet_id, "data": data}
        return self.record("read_packet", {"packet_id": packet_id, "reader": reader}, result)

    def search_packets(self, query):
        words = query.lower().split()
        matches = [k for k in self.task["packets"] if any(w in k.lower() for w in words)]
        return self.record("search_packets", {"query": query}, {"packet_ids": matches})

    def calculate(self, expression):
        try:
            out = {"value": calculate(expression), "expression": expression}
        except (ValueError, SyntaxError, ArithmeticError, TypeError, RecursionError) as exc:
            out = {"error": type(exc).__name__, "message": str(exc)}
        return self.record("calculate", {"expression": expression}, out)

    def stage_result(self, result_json, seen_packets=None):
        try:
            payload = (
                json.loads(result_json)
                if isinstance(result_json, str)
                else copy.deepcopy(result_json)
            )
            required = (
                {"selected_ids", "cash_cents", "utility", "policy_version", "request_revision"}
                if self.task["family"] != "audit"
                else {"totals", "flagged", "policy_version", "request_revision"}
            )
            if not isinstance(payload, dict) or set(payload) != required:
                raise ValueError("Fields must exactly match the requested output contract")
            if (
                type(payload["request_revision"]) is not int
                or payload["request_revision"] != self.turn
            ):
                return self.record(
                    "stage_result", {"payload": payload}, {"error": "stale_revision"}
                )
            if self.task["family"] != "audit":
                if (
                    not isinstance(payload["selected_ids"], list)
                    or not all(isinstance(i, str) for i in payload["selected_ids"])
                    or any(type(payload[k]) is not int for k in ("cash_cents", "utility"))
                ):
                    raise ValueError("Use selected_ids list and integer cash_cents/utility")
            else:
                if (
                    not isinstance(payload["totals"], dict)
                    or not all(type(v) is int for v in payload["totals"].values())
                    or not isinstance(payload["flagged"], list)
                    or not all(isinstance(v, str) for v in payload["flagged"])
                ):
                    raise ValueError("Use totals mapping to integers and flagged list")
            receipt = f"DRAFT-{self.task['id']}-{self.turn}"
            with self.lock:
                existing = next((p for p in self.proposals if p["receipt_id"] == receipt), None)
                if existing and existing["payload"] != payload:
                    return self.record(
                        "stage_result", {"payload": payload}, {"error": "draft_conflict"}
                    )
                if not existing:
                    seen = (
                        list(seen_packets)
                        if seen_packets is not None
                        else [
                            e["args"]["packet_id"]
                            for e in self.events
                            if e["tool"] == "read_packet" and "data" in e["result"]
                        ]
                    )
                    self.proposals.append(
                        {
                            "receipt_id": receipt,
                            "turn": self.turn,
                            "payload": payload,
                            "seen_packets": sorted(set(seen)),
                        }
                    )
                (self.directory / "drafts.json").write_text(
                    json.dumps(self.proposals, ensure_ascii=False, indent=2)
                )
            out = {
                "receipt_id": receipt,
                "facts": [
                    {
                        "key": k,
                        "value": render_fact_value(v),
                        "source": receipt,
                    }
                    for k, v in payload.items()
                ],
            }
        except (ValueError, TypeError) as exc:
            out = {"error": "invalid_payload", "message": str(exc)}
        return self.record("stage_result", {"result_json": result_json}, out)

    def export(self):
        with self.lock:
            return copy.deepcopy({"events": self.events, "proposals": self.proposals})


def render_fact_value(value):
    return (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
