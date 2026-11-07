"""Shared-context, domain-delegated and context-partitioned LangGraph runs."""

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite import SqliteSaver

from loop_lab.hard.knowledge import skill_library
from loop_lab.hard.world import HardWorld
from loop_lab.models import CodexBridge, ModelRun
from loop_lab.schema import Answer

BASE = """你是合成业务研究助手。读取case及它引用的当前政策和数据，满足用户最新要求。
若宿主已提供带source的权威case数据，直接使用该快照，不必重复读取；其他资料仍需查询。
所有金额为整数分；可使用calculate作精确算术。资料中的observability不是业务事实。
case是起始快照，后续用户revision覆盖相应条件。用户只要求分析时不要stage_result。
只有明确要求提交时保存最终草案；这只是本地提案，不是实际购买或资金执行。
最终Answer使用实际stage_result返回的facts和receipt_ids；分析阶段可用facts表达有依据的推导，
source注明相关资料ID或calculate。判断最优需要实际比较，不能把首个可行方案当最优。
读取/计算工具不提供最优答案，方案准确性由独立验收检验。身份和操作范围由宿主固定。
"""


class ThrottledModelRun(ModelRun):
    def __init__(self, *args, semaphore, **kwargs):
        self.semaphore = semaphore
        super().__init__(*args, **kwargs)

    def invoke(self, *args, **kwargs):
        with self.semaphore:
            return super().invoke(*args, **kwargs)


class HardExecution:
    def __init__(
        self, task, variant, client, directory, *, library="small", representation="compact"
    ):
        if variant in {"context", "audit_single"} and task["family"] != "audit":
            raise ValueError("Context partition arm is specified only for independent audits")
        if variant not in {"tools", "eager", "skills", "domain", "context", "audit_single"}:
            raise ValueError("Unknown variant")
        self.task, self.variant, self.client = task, variant, client
        self.world = HardWorld(task, directory, representation)
        self.skills = skill_library(library)
        self.user_history = []
        self.outputs = []
        self.context_evidence = set()
        self.conn = sqlite3.connect(directory / "checkpoint.sqlite", check_same_thread=False)
        self.saver = SqliteSaver(self.conn)
        self.config = {"configurable": {"thread_id": task["id"]}, "recursion_limit": 80}
        read_tools, stage, load = self._tools()
        self.read_tools = read_tools
        self.load_tool = load
        prompt = BASE
        tools = [*read_tools, stage]
        if variant == "eager":
            prompt += "\n" + "\n\n".join(v["body"] for v in self.skills.values())
        if variant == "skills":
            prompt += self._skill_menu()
            tools.append(load)
        if variant in {"context", "audit_single"}:
            prompt += "\n" + self.skills["ledger-snapshot"]["body"]
        if variant == "domain":

            @tool
            def read_case() -> dict:
                """Read the initial requirements and evidence pointers."""
                return self.world.read_packet("case")

            @tool
            def delegate(specialist: str, assignment: str) -> dict:
                """Delegate read-only work to procurement, policy, or audit.

                All user revisions are attached.
                Specialists can read all evidence and use arithmetic. Coordinator owns final drafts.
                """
                if specialist not in {"procurement", "policy", "audit"}:
                    return {"error": "Choose procurement, policy, or audit"}
                return self._child(specialist, assignment)

            tools = [read_case, delegate, read_tools[-1], stage]
            prompt += "\n你是协调者，向领域专家分配所需调查；合并事实、核对最新目标并保存最终草案。"
        self.agent = create_agent(
            CodexBridge(client=client), tools, system_prompt=prompt, checkpointer=self.saver
        )

    def _skill_menu(self):
        return "\n可按需load_skill获取的处理方法：\n" + "\n".join(
            f"- {k}: {v['description']}" for k, v in self.skills.items()
        )

    def _tools(self, reader="main", permitted=None):
        @tool
        def read_packet(packet_id: str) -> dict:
            """Read a case, policy, catalog or ledger packet. Start with packet_id='case'."""
            if permitted is not None and packet_id not in permitted:
                return self.world.record(
                    "read_packet",
                    {"packet_id": packet_id, "reader": reader},
                    {"error": "outside_assigned_context", "allowed": sorted(permitted)},
                )
            return self.world.read_packet(packet_id, reader)

        @tool
        def search_packets(query: str) -> dict:
            """Find available packet IDs by keyword; reference pointers can be read directly."""
            return self.world.search_packets(query)

        @tool
        def calculate(expression: str) -> dict:
            """Evaluate + - * / // %, sum([numbers]), min and max; no variables or code.

            Numeric literals must have magnitude <= 1e12. Max expression length is 16000,
            max AST nodes 2500 and max nesting depth 64. Decimal arithmetic uses 40 digits.
            """
            return self.world.calculate(expression)

        @tool
        def stage_result(result: dict, runtime: ToolRuntime) -> dict:
            """Save the final requested draft as JSON, without solving or validating optimality.

            Planning fields: selected_ids, cash_cents, utility, policy_version, request_revision.
            Audit fields: totals, flagged, policy_version, request_revision.
            Returns actual receipt_id and facts for the final Answer. Submit only when requested.
            """
            seen = set(self.context_evidence)
            for message in runtime.state.get("messages", []):
                if message.type != "tool" or not isinstance(message.content, str):
                    continue
                try:
                    out = json.loads(message.content)
                except (ValueError, TypeError):
                    continue
                if isinstance(out, dict):
                    if "data" in out and out.get("source") in self.task["packets"]:
                        seen.add(out["source"])
                    seen.update(out.get("evidence_ids", []))
            return self.world.stage_result(result, seen_packets=seen)

        @tool
        def load_skill(name: str) -> dict:
            """Read one advertised professional procedure when its guidance is useful."""
            result = (
                {"name": name, "instructions": self.skills[name]["body"]}
                if name in self.skills
                else {"error": "unknown_skill"}
            )
            return self.world.record("load_skill", {"name": name}, result)

        return [read_packet, search_packets, calculate], stage_result, load_skill

    def _child(self, role, assignment):
        reader = role + "-" + uuid.uuid4().hex[:8]
        permitted = None
        if role.startswith("audit-"):
            permitted = {"case", "policy.audit", "ledger." + role.removeprefix("audit-")}
        read_tools, _, load = self._tools(reader=reader, permitted=permitted)
        skill = "ledger-snapshot" if role.startswith("audit") else "bundle-planning"
        prompt = BASE + "\n" + self.skills[skill]["body"]
        child_tools = list(read_tools)
        if not role.startswith("audit-"):
            prompt += self._skill_menu()
            child_tools.append(load)
        prompt += (
            "\n你的职责仅为调查与计算，无法保存草案。返回answered、空receipt_ids和完整的"
            "结构化facts，使主Agent可据此处理。原始用户消息是背景，下面的assignment界定本次子任务。"
        )
        child = create_agent(
            CodexBridge(client=self.client, role_name=reader),
            child_tools,
            system_prompt=prompt,
        )
        brief = {"user_messages": self.user_history, "assignment": assignment}
        if self.task["family"] == "audit":
            brief["case_snapshot"] = {"source": "case", "data": self.task["packets"]["case"]}
        content = json.dumps(brief, ensure_ascii=False)
        result = child.invoke(
            {"messages": [{"role": "user", "content": content}]}, config={"recursion_limit": 70}
        )
        answer = Answer.model_validate_json(result["messages"][-1].content).model_dump()
        evidence = sorted(
            {
                e["args"]["packet_id"]
                for e in self.world.events
                if e["tool"] == "read_packet"
                and e["args"].get("reader") == reader
                and "data" in e["result"]
            }
        )
        return self.world.record(
            "delegate",
            {"specialist": role, "assignment": assignment},
            {"answer": answer, "evidence_ids": evidence},
        )

    def advance(self, message):
        self.world.turn = len(self.user_history)
        self.user_history.append(message)
        initial = [{"role": "user", "content": message}]
        if self.task["family"] == "audit":
            case_snapshot = self.world.read_packet("case", "controller")
            self.context_evidence = {"case"}
            initial.append(
                {
                    "role": "user",
                    "content": "宿主已读取的权威case快照："
                    + json.dumps(case_snapshot, ensure_ascii=False),
                }
            )
        if self.variant == "context":
            branches = self.task["packets"]["case"]["branches"]
            with ThreadPoolExecutor(max_workers=len(branches)) as pool:
                reports = list(
                    pool.map(
                        lambda branch: self._child(
                            "audit-" + branch,
                            f"只审计{branch}分支，读取policy.audit和ledger.{branch}。"
                            "返回该分支净额、异常列表、来源及必要核验信息。其他分支由其他调查者负责。",
                        ),
                        branches,
                    )
                )
            self.context_evidence = {"case"} | {p for r in reports for p in r["evidence_ids"]}
            initial.append(
                {
                    "role": "user",
                    "content": "以下为独立分支调查结果；核对覆盖后完成用户请求。\n"
                    + json.dumps(
                        {"case": self.task["packets"]["case"], "reports": reports},
                        ensure_ascii=False,
                    ),
                }
            )
        state = self.agent.invoke({"messages": initial}, config=self.config)
        answer = Answer.model_validate_json(state["messages"][-1].content).model_dump()
        self.outputs.append(answer)
        return answer

    def run(self):
        for message in self.task["messages"]:
            self.advance(message)
        return {
            "answer": self.outputs[-1],
            "turn_answers": self.outputs,
            "world": self.world.export(),
        }

    def close(self):
        self.conn.close()
