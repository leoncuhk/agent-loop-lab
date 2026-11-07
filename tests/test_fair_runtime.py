import json

from langchain_core.messages import AIMessage

from loop_lab.fair.runtime import FairExecution
from loop_lab.hard.tasks import plan_task


class QuoteAdapter:
    def __init__(self, directory, goal):
        self.directory = directory
        directory.mkdir(parents=True)
        self.goal = goal
        self.calls = 0

    def invoke(self, messages, specs, role):
        self.calls += 1
        if not any(m.type == "tool" for m in messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "assess_candidates",
                        "args": {
                            "candidates": [["B2", "C6", "T2"]],
                            "requirements": self.goal,
                            "request_revision": 0,
                        },
                        "id": "quote",
                    }
                ],
            )
        packet = json.loads(next(m.content for m in reversed(messages) if m.type == "tool"))
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "stage_quote",
                    "args": {"quote_id": packet["best"]["quote_id"]},
                    "id": "stage",
                }
            ],
        )


def test_real_graph_submits_quote_and_host_finishes_without_extra_model_call(tmp_path):
    task = plan_task("dev", 211)
    adapter = QuoteAdapter(tmp_path / "model", task["packets"]["case"]["requirements"])
    execution = FairExecution(task, "tools", adapter, tmp_path)
    outcome = execution.run()
    assert outcome["answer"]["status"] == "completed"
    assert outcome["world"]["proposals"][0]["payload"]["cash_cents"] == 9958
    assert adapter.calls == 2
    assert outcome["actual_topology"] == "single"
    execution.close()


def test_future_messages_and_private_gold_never_enter_current_model_request(tmp_path):
    task = plan_task("dev", 211)
    task["expected"] = {"sentinel": "PRIVATE_GOLD_9283"}
    task["messages"].append("UNRECEIVED_FUTURE_7922")

    class Probe:
        directory = tmp_path / "model"

        def invoke(self, messages, specs, role):
            text = json.dumps([m.content for m in messages])
            assert "PRIVATE_GOLD_9283" not in text
            assert "UNRECEIVED_FUTURE_7922" not in text
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    Probe.directory.mkdir()
    execution = FairExecution(task, "skills", Probe(), tmp_path)
    assert execution.advance(task["messages"][0])["status"] == "answered"
    execution.close()


def test_persistent_worker_keeps_evidence_between_revisions(tmp_path):
    task = plan_task("dev", 211)
    goal = task["packets"]["case"]["requirements"]

    class Probe:
        directory = tmp_path / "model"
        main_calls = 0
        worker_calls = 0

        def invoke(self, messages, specs, role):
            if role == "main":
                self.main_calls += 1
                if self.main_calls in (1, 3):
                    return AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "investigate_bundle",
                                "args": {
                                    "platform": "B",
                                    "requirements": goal,
                                    "request_revision": 0 if self.main_calls == 1 else 1,
                                },
                                "id": f"d{self.main_calls}",
                            }
                        ],
                    )
                return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')
            self.worker_calls += 1
            if self.worker_calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_packet",
                            "args": {"packet_id": "policy.current"},
                            "id": "read",
                        }
                    ],
                )
            assert any(m.type == "tool" and m.name == "read_packet" for m in messages)
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    Probe.directory.mkdir()
    adapter = Probe()
    execution = FairExecution(task, "multi", adapter, tmp_path)
    execution.advance("[revision 0] 分析B平台，不提交")
    execution.advance("[revision 1] 继续分析B平台，不提交")
    assert adapter.worker_calls == 3
    assert len(execution.world.export()["events"]) > 0
    execution.close()
