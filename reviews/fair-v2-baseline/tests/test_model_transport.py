import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from loop_lab.models import Budget, ModelRun


def test_five_declared_tool_calls_survive_the_transport(monkeypatch, tmp_path):
    names = ["load_skill", "load_skill", "get_shipping", "get_order", "get_policy"]
    decision = {
        "tool_calls": [{"name": name, "arguments_json": "{}"} for name in names],
        "final": None,
    }
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(decision)}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}},
    ]
    monkeypatch.setattr(
        "loop_lab.models.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="\n".join(json.dumps(e) for e in events), stderr=""
        ),
    )
    client = ModelRun(tmp_path, Budget())
    specs = [{"type": "function", "function": {"name": name}} for name in set(names)]
    answer = client.invoke(
        [HumanMessage(content="Read relevant procedures and facts")], specs, "main"
    )
    assert [call["name"] for call in answer.tool_calls] == names
