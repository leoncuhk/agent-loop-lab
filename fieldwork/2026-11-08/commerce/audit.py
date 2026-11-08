"""Check fixture data, actual model requests, UI cards, and CLI receipts."""

import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            yield from leaves(item)


def audit(run):
    src, live = run / "src", run / "live-1"
    deterministic = read(run / "strict-1/result.json")
    strict = int((run / "strict-1.exit").read_text())
    assert strict == int(not deterministic["stale_price_probe"]["property_passed"])
    summary = read(live / "result.json")
    events = {r: read(live / r / "events.json") for r in summary}
    cards = {r: [e["data"] for e in es if e["type"] == "ui"] for r, es in events.items()}
    fixture = next(
        x
        for x in read(src / "examples/retail/data/catalog.json")["products"]
        if x["product_id"] == "AR-1805"
    )
    product = next(
        row["product"]
        for c in cards["shopping"]
        if c["component"] == "products"
        for row in c["payload"]["items"]
        if row["product"]["product_id"] == "AR-1805"
    )
    assert (product["price"], product["attributes"]["output"]) == (fixture["price"], "65 W GaN")
    assert summary["shopping"]["cart_subtotal"] == 0
    stock = next(
        x["stock"]
        for x in read(src / "examples/retail/data/merchant_inventory.json")["inventory"]
        if x["product_id"] == "AR-2102"
    )
    preview = next(
        c["payload"]["change"] for c in cards["merchant"] if c["component"] == "change_preview"
    )
    assert preview["items"] == [
        dict(target="AR-2102", field="stock", before=stock, after=stock + 7)
    ]
    assert (preview["status"], summary["merchant"]["applied"], summary["merchant"]["stock"]) == (
        "staged",
        0,
        stock,
    )
    # Inspect the EXACT transported prompt rather than inferring disclosure from the UI.
    receipts = sorted(live.glob("bridge/*/receipt.json"))
    live_requests = [read(f.parent / "request.json") for f in receipts]
    pos = 0
    for role, skill in (("shopping", "search-discovery"), ("merchant", "inventory-operations")):
        count = summary[role]["calls"]
        body = (
            (src / f"{role}-agent/skills/{skill}/SKILL.md").read_text().split("---", 2)[2].strip()
        )
        role_requests = live_requests[pos : pos + count]
        assert all(
            read(live / role / f"transport-map-{i + 1}.json")["source_tool_choice"]
            == {"type": "auto"}
            for i in range(count)
        )
        assert skill in str(role_requests[0]["system"]) and not any(
            body in value for value in leaves(role_requests[0])
        )
        assert any(
            isinstance(m["content"], list)
            and any(
                b.get("type") == "tool_result" and b.get("content") == body for b in m["content"]
            )
            for d in role_requests[1:]
            for m in d["messages"]
        )
        pos += count
    ledgers = [read(f) for f in receipts]
    assert pos == len(ledgers) and all(
        x["status"] == "ok" and x["actual_model"] == "gpt-6.1-sol" for x in ledgers
    )
    return {
        "commit": "fd4d59224ab96b43c6dc6888207c67b3bd5a24cf",
        "upstream": {
            "pytest_summary": next(
                line
                for line in reversed((run / "upstream-pytest.log").read_text().splitlines())
                if " passed" in line
            ),
            "pytest_exit": int((run / "upstream-pytest.exit").read_text()),
            "check_exit": int((run / "upstream-check.exit").read_text()),
        },
        "local_probe": deterministic,
        "strict_current_price_exit": strict,
        "live": summary,
        "live_raw_crosschecks": {
            "product_price_matches_fixture": True,
            "preview_matches_fixture_and_requested_delta": True,
            "skill_body_absent_from_first_request_and_returned_as_tool_result": True,
            "source_tool_choice_always_auto": True,
        },
        "transport": {
            "completed": len(ledgers),
            "model": "gpt-6.1-sol",
            "usage": {k: sum(x["usage"][k] for x in ledgers) for k in ledgers[0]["usage"]},
        },
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    print(json.dumps(audit(p.parse_args().run), ensure_ascii=False, indent=2))
