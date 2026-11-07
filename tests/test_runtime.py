import pytest
from langchain_core.messages import AIMessage

from loop_lab.runtime import Execution
from loop_lab.tasks import tasks


class ScriptedModel:
    """External model boundary double; real graph, tools and SQLite are used."""

    records = []

    def invoke(self, messages, specs, role):
        tool_results = [m for m in messages if m.type == "tool"]
        if not tool_results:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "create_return", "args": {"order_id": "OLD"}, "id": "call-return"}
                ],
            )
        return AIMessage(content='{"status":"completed","facts":[],"receipt_ids":["R-OLD"]}')


def test_approval_can_resume_in_a_new_execution_without_duplicate_write(tmp_path):
    task = next(t for t in tasks() if t["id"] == "d07")
    first = Execution(task, "tools", ScriptedModel(), tmp_path)
    state = first.advance()
    assert state["pending"][0]["order_id"] == "OLD"
    assert first.world.receipts() == []
    first.close()

    resumed = Execution(task, "tools", ScriptedModel(), tmp_path)
    result = resumed.advance(approval="approve")
    assert result["answer"]["receipt_ids"] == ["R-OLD"]
    assert len(resumed.world.receipts()) == 1
    resumed.close()


def test_resume_rejects_different_task_identity(tmp_path):
    task = next(t for t in tasks() if t["id"] == "d07")
    execution = Execution(task, "tools", ScriptedModel(), tmp_path)
    execution.advance()
    execution.close()
    changed = {**task, "user_id": "bob"}
    with pytest.raises(ValueError, match="identity"):
        Execution(changed, "tools", ScriptedModel(), tmp_path)


def test_two_pending_writes_resume_by_interrupt_id(tmp_path):
    class TwoWrites(ScriptedModel):
        def invoke(self, messages, specs, role):
            if not any(m.type == "tool" for m in messages):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "create_return", "args": {"order_id": oid}, "id": f"write-{oid}"}
                        for oid in ["NEW", "OLD"]
                    ],
                )
            return AIMessage(
                content='{"status":"completed","facts":[],"receipt_ids":["R-NEW","R-OLD"]}'
            )

    task = next(t for t in tasks() if t["id"] == "d07")
    task["orders"]["NEW"]["status"] = "delivered"
    e = Execution(task, "tools", TwoWrites(), tmp_path)
    assert len(e.advance()["pending"]) == 2
    assert len(e.advance(approval="approve")["world"]["receipts"]) == 2
    e.close()
