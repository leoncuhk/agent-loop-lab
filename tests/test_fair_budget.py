import pytest
from langchain_core.messages import AIMessage

from loop_lab.fair.client import SessionClient, WorkerBudgetExceeded
from loop_lab.fair.world import DecisionWorld
from loop_lab.hard.tasks import plan_task
from loop_lab.models import TaskBudgetExceeded


def test_workers_cannot_consume_reserved_main_calls(tmp_path):
    class Adapter:
        directory = tmp_path / "model"

        def invoke(self, *args):
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    Adapter.directory.mkdir()
    world = DecisionWorld(plan_task("dev", 211), tmp_path)
    client = SessionClient(Adapter(), world, max_calls=6, reserve_main=2)
    with client.worker_assignment("worker-A"):
        for _ in range(4):
            client.invoke([], [], "worker-A")
        with pytest.raises(WorkerBudgetExceeded):
            client.invoke([], [], "worker-A")
    for _ in range(2):
        client.invoke([], [], "main")
    with pytest.raises(TaskBudgetExceeded):
        client.invoke([], [], "main")
