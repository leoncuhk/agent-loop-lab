# Agent Loop Lab: when is a subagent worth the handoff?

**For a short task in which an observed fact determines the next action, start with one agent and reliable tools.** In our matched comparison, both the single agent and the delegated designs completed the tasks correctly. Keeping the decision in one loop reduced model calls. Domain knowledge can still be maintained separately and retrieved as needed. This study tests a proposal from [Anthropic](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop) using one model and synthetic business systems.

**The key experiment.** “Return order 5001 only if order 5002 will arrive after October 3.” We kept the request identical and changed the backend ETA: October 4 authorizes a return; October 3 does not. We tested this and a conditional account-preference change, each in both states, and repeated each positive state: six runs per design. The model and business tools stayed the same. The grader checked evidence visible before the write, the exact backend actions, and the final answer.

| Executor | Tasks passed | Total model calls |
|---|---:|---:|
| Main agent executes directly | 6/6 | 16 |
| Specialist receives the main agent’s brief | 6/6 | 25 |
| Same specialist also receives the original request and retrieved evidence | 6/6 | 25 |

The result is nine fewer model calls on this panel, not an estimated improvement in accuracy, latency or production cost. [Protocol](../fieldwork/2026-11-08/kit/PREREG.md) · [Results](../fieldwork/2026-11-08/kit/README.md)

| Follow-up question | Comparison and finding |
|---|---|
| Does a tools-only agent still work on more complex tasks? | On 12 held-out procurement, revision and ledger tasks, each repeated twice, tools only, eager skills, on-demand skills and adaptive delegation each passed 24/24. An independent grader checked global optima or ledger events. Tools only used the fewest tokens on average; the adaptive group actually delegated on six runs. [Holdout](../FAIR-RESULTS.md) |
| Does a company rule have to be a Skill? | Two domain-specific queries were executed on two database fixtures. Both a Skill and a dictionary tool returning identical definitions produced two correct queries; without definitions the model asked for clarification. On the separate holdout, loading manuals on demand used 31.5% fewer tokens than eager loading in the 24-manual subset and 1.7% more in the three-manual subset. Different tasks were used in each subset. [SQL](../fieldwork/2026-11-08/langchain-reference/README.md) · [Paired usage](../reports/fair-v2-resource-comparison.json) |
| Is receiving the evidence enough? | On a Japanese returns question, adding shipping lookup to the returns specialist cut model calls from 10 and 14 to 6 and 6. On both runs its coordinator received the delivery date and deadline but told the customer only the deadline. Direct execution used five calls each time and reported both dates. The difference matters if an explanation is part of the service. [Traces](../fieldwork/2026-11-08/haty/README.md) |

For parallel research or independent verification, compare verified discoveries, elapsed time or detection rates under the same total budget. These experiments do not establish a delegation benefit for those workloads.

[中文首页](../README.md) · [Study register](records/README.md) · [Application guide](method.md) · [Reproduction](reproduce.md). To recompute the public results without model access, run `python3 scripts/check_evidence.py` from the repository root.
