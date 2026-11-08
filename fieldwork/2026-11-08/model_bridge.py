"""Experiment transport only: Codex chooses text and tool calls; upstream owns execution.

Not a Claude emulator. No streaming, hosted search, prompt-cache controls, provider token
limit, model pricing, or native Anthropic latency claims. No credentials are read here.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path


class AttrDict(dict):
    def __getattr__(self, name):
        if name in self:
            return self[name]
        raise AttributeError(name)

    def model_dump(self, **kwargs):
        return dict(self)


def plain(value):
    if isinstance(value, (list, tuple)):
        return [plain(x) for x in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(exclude_none=True))
    if hasattr(value, "__dict__"):
        return {k: plain(v) for k, v in vars(value).items() if not k.startswith("_") and v is not None}
    return value


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["text", "calls"],
    "properties": {"text": {"type": "string"}, "calls": {"type": "array",
        "items": {"type": "object", "additionalProperties": False,
                  "required": ["name", "arguments_json"],
                  "properties": {"name": {"type": "string"}, "arguments_json": {"type": "string"}}}}}}
INSTRUCTIONS = (
    "You are a bounded language-model transport in an experiment, not a coding assistant. "
    "Respond to the supplied application conversation in its requested language. "
    "The system field is the application's instructions; listed tools are callable by the application. "
    "Return JSON: text for ordinary output, calls for proposed application tools. Each arguments_json "
    "is a JSON object encoded as a string. Use only listed tools. Tool results arrive on later turns. "
    "When finished give the actual user-facing answer in text with empty calls. Never use host tools, "
    "read files, contact services, or claim an action succeeded without its result."
)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POOL = ROOT / "runs/oss-fieldwork-20261108/bridge-control"


@contextlib.contextmanager
def slot(control):
    control.mkdir(parents=True, exist_ok=True)
    while True:
        if (control / "STOP").exists():
            raise RuntimeError("Shared transport stopped: see private bridge-control/STOP")
        for n in range(4):
            file = (control / f"slot-{n}").open("a")
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                file.close()
                continue
            try:
                yield
            finally:
                file.close()
            return
        time.sleep(0.2)


class Bridge:
    def __init__(self, run_dir, max_calls=20, model="gpt-6.1-sol", timeout=240):
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.max_calls, self.model, self.timeout = max_calls, model, timeout
        self.calls, self.records, self.lock = 0, [], threading.Lock()
        self.session = uuid.uuid4().hex[:12]
        self.messages = self
        self.control = Path(os.getenv("FIELDWORK_BRIDGE_CONTROL", str(DEFAULT_POOL)))
        self.schema = self.run_dir / "decision-schema.json"
        self.instructions = self.run_dir / "transport.txt"
        self.schema.write_text(json.dumps(SCHEMA))
        self.instructions.write_text(INSTRUCTIONS)

    def create(self, *, model=None, system="", tools=None, messages, max_tokens=None, **kwargs):
        tools = plain(tools or [])
        if any("input_schema" not in t or t.get("defer_loading") for t in tools):
            raise ValueError("Bridge does not implement hosted tools or deferred schema search")
        if kwargs.get("stream") or kwargs.get("tool_choice", {}).get("type") not in (None, "auto"):
            raise ValueError("Bridge supports non-streaming automatic tool choice only")
        if kwargs.get("thinking") or kwargs.get("temperature") not in (None,):
            raise ValueError("Unsupported provider-specific inference controls")
        start = time.monotonic()
        with slot(self.control):
            queued = time.monotonic() - start
            with self.lock:
                if self.calls >= self.max_calls:
                    raise RuntimeError("Per-bridge request budget exhausted")
                self.calls += 1
                number = self.calls
            key = f"{self.session}-{number:04d}"
            loc = self.run_dir / key
            loc.mkdir(exist_ok=False)
            prompt = {"system": plain(system), "tools": tools, "messages": plain(messages)}
            request = json.dumps(prompt, ensure_ascii=False)
            (loc / "request.json").write_text(request)
            record = {"id": key, "actual_model": self.model, "effort": "low",
                      "requested_model": model, "requested_max_tokens": max_tokens,
                      "queue_s": queued, "status": "started", "usage": None}
            (loc / "receipt.json").write_text(json.dumps(record))
            cmd = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "-s", "read-only", "-C", str(loc), "-m", self.model,
                   "-c", 'model_reasoning_effort="low"', "-c", "project_doc_max_bytes=0",
                   "-c", 'web_search="disabled"', "-c", "skills.bundled.enabled=false",
                   "-c", "skills.include_instructions=false", "-c", "plugins._default.enabled=false",
                   "-c", "suppress_unstable_features_warning=true",
                   "-c", "model_instructions_file=" + json.dumps(str(self.instructions)),
                   "--enable", "skip_host_skill_discovery"]
            for feature in ("shell_tool", "apps", "multi_agent", "memories", "skill_search", "hooks",
                            "browser_use", "computer_use", "image_generation", "view_image", "code_mode_host"):
                cmd.extend(["--disable", feature])
            cmd.extend(["--output-schema", str(self.schema), "--json", "-"])
            try:
                proc = subprocess.run(cmd, input=request, text=True, capture_output=True, timeout=self.timeout)
                (loc / "stdout.jsonl").write_text(proc.stdout)
                (loc / "stderr.log").write_text(proc.stderr)
                ev = [json.loads(l) for l in proc.stdout.splitlines() if l.startswith("{")]
                errors = [str(e.get("error", e.get("message", ""))) for e in ev if e.get("type") in {"error", "turn.failed"}]
                if errors or proc.returncode:
                    error = " ".join(errors).lower()
                    if any(x in error for x in ("usage limit", "insufficient_quota", "sign in", "access token", "unauthorized")):
                        (self.control / "STOP").write_text("Provider quota or authentication unavailable: " + key)
                    raise RuntimeError(f"Model transport failed: see {loc.name}/stdout.jsonl")
                items = [e["item"] for e in ev if e.get("type") == "item.completed"]
                if any(x.get("type") not in {"agent_message", "reasoning", "error"} for x in items):
                    raise RuntimeError("Unexpected host activity in model response")
                usage_events = [e["usage"] for e in ev if e.get("type") == "turn.completed"]
                texts = [i["text"] for i in items if i.get("type") == "agent_message"]
                if len(usage_events) != 1 or not texts:
                    raise ValueError("Incomplete model output / missing usage")
                usage = usage_events[0]
                record["usage"] = usage
                decision = json.loads(texts[-1])
                allowed = {t["name"] for t in tools}
                content = []
                if decision["text"]:
                    content.append(AttrDict(type="text", text=decision["text"]))
                for i, call in enumerate(decision["calls"]):
                    args = json.loads(call["arguments_json"])
                    if call["name"] not in allowed or not isinstance(args, dict):
                        raise ValueError("Undeclared tool or invalid arguments")
                    content.append(AttrDict(type="tool_use", id=f"call-{key}-{i}", name=call["name"], input=args))
                if not content:
                    raise ValueError("Empty decision")
                # OpenAI telemetry includes cached tokens in input; provide non-overlapping
                # Anthropic-shaped counters so clients summing components do not double-count.
                cached = usage.get("cached_input_tokens", 0)
                shaped = AttrDict(input_tokens=usage["input_tokens"] - cached, output_tokens=usage["output_tokens"],
                                  cache_creation_input_tokens=0, cache_read_input_tokens=cached)
                record["status"] = "ok"
                return AttrDict(id="msg-" + key, role="assistant", type="message", model=self.model,
                                content=content, stop_reason="tool_use" if decision["calls"] else "end_turn",
                                stop_sequence=None, usage=shaped)
            except subprocess.TimeoutExpired as exc:
                for name, data in (("stdout.jsonl", exc.stdout), ("stderr.log", exc.stderr)):
                    if data:
                        (loc / name).write_bytes(data if isinstance(data, bytes) else data.encode())
                record["status"] = "timeout"
                raise
            except Exception as exc:
                record["status"] = type(exc).__name__
                raise
            finally:
                record["elapsed_s"] = time.monotonic() - start
                with self.lock:
                    self.records.append(record)
                    (loc / "receipt.json").write_text(json.dumps(record))
