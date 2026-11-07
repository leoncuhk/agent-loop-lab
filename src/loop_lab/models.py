"""A real model bridge with explicit protocol and usage accounting.

The CLI is a transport, not the business executor. It sees only serialized messages
and tool schemas. Every business call is subsequently executed by LangGraph.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from loop_lab.schema import MAX_BATCH, PROTOCOL, Decision


class BudgetExceeded(RuntimeError):
    pass


class SharedBudgetExceeded(BudgetExceeded):
    pass


class TaskBudgetExceeded(BudgetExceeded):
    pass


class ModelServiceUnavailable(RuntimeError):
    """Authentication, exhausted account quota or a common runtime prerequisite."""


@dataclass
class Budget:
    max_calls: int = 400
    max_tokens: int = 4_000_000
    calls: int = 0
    tokens: int = 0
    reserve_per_call: int = 50_000
    in_flight: int = 0
    unknown_usage: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self) -> None:
        with self.lock:
            if (
                self.unknown_usage
                or self.calls >= self.max_calls
                or self.tokens + (self.in_flight + 1) * self.reserve_per_call > self.max_tokens
            ):
                raise SharedBudgetExceeded("Shared model budget exhausted")
            self.calls += 1
            self.in_flight += 1

    def charge(self, usage: dict) -> None:
        with self.lock:
            self.tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            self.in_flight = max(0, self.in_flight - 1)

    def mark_unknown(self):
        with self.lock:
            self.unknown_usage = True
            self.in_flight = max(0, self.in_flight - 1)

    def can_dispatch(self) -> bool:
        with self.lock:
            return (
                not self.unknown_usage
                and self.calls < self.max_calls
                and self.tokens + (self.in_flight + 1) * self.reserve_per_call <= self.max_tokens
            )

    def trial_slots(self) -> int:
        """Bound active trials even before their first worker reaches reserve()."""
        with self.lock:
            return max(0, (self.max_tokens - self.tokens) // self.reserve_per_call)


@dataclass
class ModelRun:
    directory: Path
    budget: Budget
    max_calls: int = 12
    model: str = "gpt-6.1-sol"
    effort: str = "low"
    timeout: float = 120
    records: list[dict] = field(default_factory=list)
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.usage_path = self.directory / "usage.jsonl"
        if self.usage_path.exists():
            self.records = [json.loads(s) for s in self.usage_path.read_text().splitlines()]
            self.calls = max((r["call"] for r in self.records), default=0)
            if any(not r.get("usage") for r in self.records):
                self.budget.mark_unknown()
        self.schema_path = self.directory / "decision.schema.json"
        self.schema_path.write_text(json.dumps(Decision.model_json_schema()))
        self.instructions_path = self.directory / "transport-instructions.txt"
        self.instructions_path.write_text(
            "You are a structured decision model in a synthetic business experiment. "
            "Follow the system message inside the supplied conversation. "
            "Select only from the supplied business tool schemas; another runtime executes them. "
            "Return tool_calls with JSON-string arguments OR a final structured answer, "
            "never both. Use actual observed facts and receipt IDs. "
            f"Each decision may request at most {MAX_BATCH} business tool calls. "
            "Do not inspect local files or use host tools."
        )

    def invoke(self, messages: list[BaseMessage], specs: list[dict], role: str) -> AIMessage:
        with self.lock:
            if self.calls >= self.max_calls:
                raise TaskBudgetExceeded("Per-task model budget exhausted, specialists included")
            self.budget.reserve()
            self.calls += 1
            number = self.calls
        prefix = self.directory / f"call-{number:03d}-{role}"
        conversation = []
        for message in messages:
            entry = {"role": message.type, "content": message.content}
            if getattr(message, "tool_calls", None):
                entry["tool_calls"] = message.tool_calls
            if getattr(message, "tool_call_id", None):
                entry["tool_call_id"] = message.tool_call_id
            conversation.append(entry)
        request = {"conversation": conversation, "tools": specs}
        prompt = json.dumps(request, ensure_ascii=False, default=str)
        prefix.with_suffix(".request.json").write_text(prompt)
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-C",
            str(self.directory.resolve()),
            "-m",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.effort}"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'web_search="disabled"',
            "-c",
            "skills.bundled.enabled=false",
            "-c",
            "skills.include_instructions=false",
            "-c",
            "plugins._default.enabled=false",
            "-c",
            "suppress_unstable_features_warning=true",
            "-c",
            "model_instructions_file=" + json.dumps(str(self.instructions_path.resolve())),
            "--enable",
            "skip_host_skill_discovery",
        ]
        for feature in (
            "shell_tool",
            "apps",
            "multi_agent",
            "memories",
            "skill_search",
            "hooks",
            "browser_use",
            "computer_use",
            "image_generation",
            "view_image",
            "code_mode_host",
        ):
            cmd.extend(["--disable", feature])
        cmd.extend(["--output-schema", str(self.schema_path.resolve()), "--json", "-"])
        start = time.monotonic()
        record = {
            "role": role,
            "call": number,
            "model": self.model,
            "effort": self.effort,
            "protocol": PROTOCOL,
            "usage": {},
            "status": "error",
        }
        try:
            process = subprocess.run(
                cmd, input=prompt, text=True, capture_output=True, timeout=self.timeout, check=False
            )
            prefix.with_suffix(".stdout.jsonl").write_text(process.stdout)
            prefix.with_suffix(".stderr.txt").write_text(process.stderr)
            events = [
                json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")
            ]
            text = None
            unexpected = []
            service_messages = []
            for event in events:
                item = event.get("item", {})
                if event.get("type") in {"error", "turn.failed"}:
                    service_messages.append(str(event.get("error", event.get("message", ""))))
                if event.get("type") == "turn.completed":
                    record["usage"] = event.get("usage", {})
                if item.get("type") == "agent_message":
                    text = item.get("text")
                if item.get("type") not in (None, "agent_message", "reasoning", "error"):
                    unexpected.append(item.get("type"))
            if unexpected:
                raise RuntimeError(f"Unexpected CLI host activity: {unexpected}")
            if process.returncode or text is None:
                signal = " ".join(service_messages).lower()
                if "usage limit" in signal or "insufficient_quota" in signal:
                    record["service_error"] = "usage_limit"
                    raise ModelServiceUnavailable("usage_limit: account cannot run model requests")
                if any(
                    term in signal
                    for term in ("not logged in", "access token", "sign in again", "unauthorized")
                ):
                    record["service_error"] = "authentication"
                    raise ModelServiceUnavailable("authentication: account login needs recovery")
                raise RuntimeError(
                    f"CLI failed, returncode={process.returncode}; see saved transcript"
                )
            decision = Decision.model_validate_json(text)
            if bool(decision.tool_calls) == (decision.final is not None):
                raise ValueError("Decision must contain tool calls OR a final answer")
            names = {spec["function"]["name"] for spec in specs}
            calls = []
            for call in decision.tool_calls:
                if call.name not in names:
                    raise ValueError(f"Undeclared business tool: {call.name}")
                arguments = json.loads(call.arguments_json)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                calls.append(
                    {
                        "name": call.name,
                        "args": arguments,
                        "id": uuid.uuid4().hex,
                        "type": "tool_call",
                    }
                )
            record["status"] = "ok"
            return AIMessage(
                content=decision.final.model_dump_json() if decision.final else "", tool_calls=calls
            )
        except subprocess.TimeoutExpired as exc:
            prefix.with_suffix(".timeout.txt").write_text(str(exc))
            for name, data in (("stdout.jsonl", exc.stdout), ("stderr.txt", exc.stderr)):
                if data:
                    prefix.with_suffix("." + name).write_bytes(
                        data if isinstance(data, bytes) else data.encode()
                    )
            record["error"] = "TimeoutExpired"
            raise
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if record["usage"]:
                self.budget.charge(record["usage"])
            else:
                self.budget.mark_unknown()
            record["latency_s"] = time.monotonic() - start
            with self.lock:
                self.records.append(record)
                with self.usage_path.open("a") as stream:
                    stream.write(json.dumps(record) + "\n")


class CodexBridge(BaseChatModel):
    client: Any = Field(exclude=True)
    role_name: str = "main"
    tool_specs: list[dict] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "codex-json-bridge"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.model_copy(update={"tool_specs": [convert_to_openai_tool(t) for t in tools]})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.client.invoke(messages, self.tool_specs, self.role_name)
        return ChatResult(generations=[ChatGeneration(message=message)])
