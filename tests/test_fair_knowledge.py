import json

from langchain_core.messages import AIMessage

from loop_lab.fair.knowledge import knowledge_pack
from loop_lab.fair.runtime import FairExecution
from loop_lab.hard.tasks import plan_task


def test_same_bodies_are_eager_or_progressively_loaded_and_common_tools_match(tmp_path):
    pack = knowledge_pack("small")
    schemas = {}

    class Probe:
        def __init__(self, method):
            self.method = method
            self.directory = tmp_path / method / "model"
            self.directory.mkdir(parents=True)
            self.calls = 0

        def invoke(self, messages, specs, role):
            self.calls += 1
            schemas[self.method] = {s["function"]["name"]: s for s in specs}
            system = messages[0].content
            if self.method == "eager":
                assert all(v["body"] in system for v in pack.values())
            else:
                assert all(v["body"] not in system for v in pack.values())
            if self.method == "skills" and self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "load_skills", "args": {"names": list(pack)}, "id": "load"}
                    ],
                )
            if self.method == "skills":
                data = json.loads(next(m.content for m in messages if m.type == "tool"))
                assert {s["name"]: s["instructions"] for s in data["loaded"]} == {
                    k: v["body"] for k, v in pack.items()
                }
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    for method in ["tools", "eager", "skills"]:
        execution = FairExecution(plan_task("dev", 211), method, Probe(method), tmp_path / method)
        execution.advance("先分析，不提交")
        execution.close()
    common = {"read_packet", "assess_candidates", "stage_quote"}
    assert all(
        {n: schemas[m][n] for n in common} == {n: schemas["tools"][n] for n in common}
        for m in ["eager", "skills"]
    )
