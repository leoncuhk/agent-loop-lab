import json
from types import SimpleNamespace

import pytest
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


def test_usage_limit_is_classified_as_a_systemic_stop(monkeypatch, tmp_path):
    from loop_lab.models import ModelServiceUnavailable

    events = [{"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]
    monkeypatch.setattr(
        "loop_lab.models.subprocess.run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stdout="\n".join(json.dumps(e) for e in events), stderr=""
        ),
    )
    client = ModelRun(tmp_path, Budget())
    with pytest.raises(ModelServiceUnavailable, match="usage_limit"):
        client.invoke([HumanMessage(content="test")], [], "main")
    assert client.records[0]["service_error"] == "usage_limit"
