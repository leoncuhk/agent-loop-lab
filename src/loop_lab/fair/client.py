"""Role-aware task budget. Model attempts remain separate from deterministic completion."""

import json
import threading
import time
import uuid
from contextlib import contextmanager

from langchain_core.messages import AIMessage, SystemMessage

from loop_lab.models import TaskBudgetExceeded


class WorkerBudgetExceeded(RuntimeError):
    pass


class SessionClient:
    def __init__(
        self, adapter, world, *, max_calls=24, reserve_main=3, worker_allowance=6, semaphore=None
    ):
        self.adapter, self.world = adapter, world
        self.max_calls = max_calls
        self.reserve_main, self.worker_allowance = reserve_main, worker_allowance
        self.semaphore = semaphore or threading.BoundedSemaphore(4)
        self.used = 0
        self.worker_counts = {}
        self.lock = threading.RLock()
        self.started = []

    @contextmanager
    def worker_assignment(self, scope):
        with self.lock:
            if scope in self.worker_counts:
                raise ValueError("This persistent worker is already active")
            self.worker_counts[scope] = 0
        try:
            yield
        finally:
            with self.lock:
                self.worker_counts.pop(scope, None)

    def invoke(self, messages, specs, role):
        if role == "main" and self.world.turn in self.world.completed_answers:
            answer = self.world.completed_answers[self.world.turn]
            self.world.record("host_finalization", {}, {"receipt_ids": answer["receipt_ids"]})
            return AIMessage(content=json.dumps(answer, ensure_ascii=False))
        with self.lock:
            worker = role.startswith("worker-")
            if worker:
                if role not in self.worker_counts:
                    raise RuntimeError("Worker invocation has no active assignment")
                if (
                    self.worker_counts[role] >= self.worker_allowance
                    or self.used >= self.max_calls - self.reserve_main
                ):
                    raise WorkerBudgetExceeded(
                        "Worker allowance exhausted; return registered partial artifacts"
                    )
                self.worker_counts[role] += 1
            if self.used >= self.max_calls:
                raise TaskBudgetExceeded("Whole task request quota exhausted")
            self.used += 1
            remaining = self.max_calls - self.used
            assignment_remaining = (
                (self.worker_allowance - self.worker_counts[role]) if worker else None
            )
            request_id = uuid.uuid4().hex
            intent = {
                "request_id": request_id,
                "role": role,
                "sequence": self.used,
                "status": "started",
                "remaining_after_request": remaining,
            }
            self.started.append(intent)
            with (self.adapter.directory / "attempt-starts.jsonl").open("a") as stream:
                stream.write(json.dumps(intent) + "\n")
        notice = SystemMessage(
            content=f"Resource status: {remaining} model requests remain after this request, "
            "shared by coordinator and workers; "
            f"current worker allowance remaining={assignment_remaining}. "
            "Finish a justified result without redundant checks."
        )
        waiting = time.monotonic()
        with self.semaphore:
            waited = time.monotonic() - waiting
            try:
                answer = self.adapter.invoke([*messages, notice], specs, role)
                status = "completed"
                return answer
            except BaseException:
                status = "failed"
                raise
            finally:
                finish = {
                    "request_id": request_id,
                    "role": role,
                    "status": status,
                    "queue_wait_s": waited,
                }
                with self.lock:
                    with (self.adapter.directory / "attempt-finishes.jsonl").open("a") as stream:
                        stream.write(json.dumps(finish) + "\n")
