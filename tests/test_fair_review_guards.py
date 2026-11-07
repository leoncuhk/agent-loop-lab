import json

import pytest
from langchain_core.messages import AIMessage

from loop_lab.fair.runtime import FairExecution
from loop_lab.hard.tasks import plan_task


def test_partial_worker_returns_registered_quote_and_main_can_finish(tmp_path):
    task = plan_task("dev", 211, delivery="quote")
    goal = task["packets"]["case"]["requirements"]

    class Probe:
        directory = tmp_path / "model"
        main_calls = 0
        worker_calls = 0

        def invoke(self, messages, specs, role):
            if role == "main":
                self.main_calls += 1
                if self.main_calls == 1:
                    return AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "investigate_bundle",
                                "id": "delegate",
                                "args": {
                                    "platform": "B",
                                    "requirements": goal,
                                    "request_revision": 0,
                                },
                            }
                        ],
                    )
                report = json.loads(next(m.content for m in reversed(messages) if m.type == "tool"))
                assert report["state"] == "partial_budget"
                quote = report["registered_records"][0]["quote_id"]
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "stage_quote", "id": "stage", "args": {"quote_id": quote}}
                    ],
                )
            self.worker_calls += 1
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "assess_candidates",
                        "id": "assess",
                        "args": {"candidates": [["B2", "C6", "T2"]]},
                    }
                ],
            )

    Probe.directory.mkdir()
    adapter = Probe()
    execution = FairExecution(task, "multi", adapter, tmp_path, worker_allowance=1)
    result = execution.run()
    assert result["answer"]["status"] == "completed"
    assert adapter.worker_calls == 1 and adapter.main_calls == 2
    assert result["delegation_spans"][0]["state"] == "partial_budget"
    execution.close()


def test_reopening_existing_checkpoint_requires_an_explicit_recovery_path(tmp_path):
    class Probe:
        directory = tmp_path / "model"

        def invoke(self, *args):
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    Probe.directory.mkdir()
    task = plan_task("dev", 211, delivery="quote")
    execution = FairExecution(task, "tools", Probe(), tmp_path)
    execution.advance("仅分析")
    execution.close()
    with pytest.raises(ValueError, match="fresh"):
        FairExecution(task, "tools", Probe(), tmp_path)
