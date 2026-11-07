import json

from langchain_core.messages import AIMessage

from loop_lab.hard.runtime import HardExecution
from loop_lab.hard.tasks import plan_task


def test_skill_body_enters_same_loop_only_after_loading(tmp_path):
    class Probe:
        calls = 0

        def invoke(self, messages, specs, role):
            self.calls += 1
            content = json.dumps([m.content for m in messages], ensure_ascii=False)
            if self.calls == 1:
                assert "高单品utility/price不保证组合最优" not in content
                assert "bundle-planning" in content
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "load_skill", "args": {"name": "bundle-planning"}, "id": "load"}
                    ],
                )
            assert "高单品utility/price不保证组合最优" in content
            return AIMessage(content='{"status":"answered","facts":[],"receipt_ids":[]}')

    execution = HardExecution(plan_task("P-test", 111), "skills", Probe(), tmp_path)
    assert execution.advance("先读取组合方法，不提交")["status"] == "answered"
    assert [e["tool"] for e in execution.world.events] == ["load_skill"]
    execution.close()
