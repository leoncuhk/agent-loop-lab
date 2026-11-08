"""Two independent synthetic turns with real model decisions on the upstream retail loops."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from commerce_common.testing import FakeStream
from merchant_agent import MerchantSessionContext, MerchantSessionState
from merchant_agent_runtime import MerchantAgent
from retail.api.agent_config import build_merchant_config, build_shopping_config
from retail.api.mock_merchant import MockRetailMerchant
from retail.api.mock_retail import MockRetail
from shopping_agent import ShoppingSessionContext, ShoppingSessionState
from shopping_agent_runtime import ShoppingAgent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model_bridge import Bridge, plain  # noqa: E402

PROMPTS = {
    "shopping": (
        "I'm buying a universal travel adapter for a laptop. My limit is $50. "
        "What can you offer? Show the item and price, but don't add anything to my cart."
    ),
    "merchant": (
        "I'm reviewing stock for our ocean wall decals. Please check the current count "
        "and stage exactly seven additional units for my review. Show me what would "
        "change; do not apply it."
    ),
}


def save(path, data):
    path.write_text(json.dumps(plain(data), ensure_ascii=False, indent=2) + "\n")


class ReplayClient:
    def __init__(self, bridge, out):
        self.bridge, self.out, self.messages, self.calls = bridge, out, self, 0

    def call(self, **request):
        self.calls += 1
        save(self.out / f"upstream-request-{self.calls}.json", request)
        d = dict(request)
        # Provider reasoning/caching controls are not emulated. Preserve the original
        # payload, reject unknown controls and record how the known ones are mapped.
        thinking = d.pop("thinking", None)
        effort = d.pop("output_config", {})
        assert thinking is None or thinking.get("type") in ("disabled", "adaptive"), thinking
        if "temperature" in d:
            raise ValueError("No native temperature mapping.")
        choice = d.pop("tool_choice", {"type": "auto"})
        assert choice["type"] in ("auto", "none", "tool")
        if choice["type"] == "none":
            d["tools"] = []
        if choice["type"] == "tool":
            name = choice["name"]
            d["tools"] = [t for t in d["tools"] if t["name"] == name]
            assert len(d["tools"]) == 1
            d["system"] = [
                *d["system"],
                {"type": "text", "text": f"Transport constraint: this round must call {name}."},
            ]
        data = dict(
            upstream_thinking=thinking,
            upstream_output_config=effort,
            actual_effort="low",
            source_tool_choice=choice,
            replayed_after_full_generation=True,
        )
        save(self.out / f"transport-map-{self.calls}.json", data)
        msg = self.bridge.messages.create(**d)
        if choice["type"] == "tool":
            picks = [b for b in msg.content if b.type == "tool_use"]
            if len(picks) != 1 or picks[0].name != choice["name"]:
                raise ValueError("Actual model did not honor forced tool choice.")
        return msg

    def stream(self, **request):
        # Only the event frames are synthetic. Every block is the current model's
        # decision, including tool name, arguments, text and stop reason.
        return FakeStream(self.call(**request))

    async def create(self, **request):
        return self.call(**request)


async def run(out):
    bridge = Bridge(out / "bridge", max_calls=30, model="gpt-6.1-sol")
    summary = {}
    for role, prompt in PROMPTS.items():
        base = out / role
        base.mkdir()
        client = ReplayClient(bridge, base)
        shop = MockRetail()
        if role == "shopping":
            cfg = build_shopping_config()
            agent = ShoppingAgent(
                backend=shop, config=cfg, skills_dir=Path("shopping-agent/skills"), client=client
            )
            ctx = ShoppingSessionContext(session_id="live-s", user_id="demo-user")
            state = ShoppingSessionState()
        else:
            cfg = build_merchant_config(shop.store_name)
            assert cfg.require_host_approval
            back = MockRetailMerchant(shop, cfg)
            agent = MerchantAgent(
                backend=back, config=cfg, skills_dir=Path("merchant-agent/skills"), client=client
            )
            ctx = MerchantSessionContext(
                session_id="live-m", merchant_id="acme-retail", operator="Avery"
            )
            state = MerchantSessionState()
        events, messages, error = [], [{"role": "user", "content": prompt}], None
        try:
            async for event in agent.stream_turn(messages, ctx, state):
                events.append(event.model_dump(mode="json"))
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        finally:
            save(base / "events.json", events)
            save(base / "messages.json", messages)
            save(base / "state.json", state.model_dump(mode="json"))
        calls = [e["data"]["tool"] for e in events if e["type"] == "tool_call"]
        loaded = [
            e["data"]["input"].get("skill_name")
            for e in events
            if e["type"] == "tool_call" and e["data"]["tool"] == "load_skill"
        ]
        cards = [e["data"] for e in events if e["type"] == "ui"]
        readings = [e for e in events if e["type"] == "tool_result" and e["data"]["status"] == "ok"]
        finished = any(e["type"] == "turn_complete" for e in events) and not error
        if role == "shopping":
            subtotal = (await shop.get_cart(ctx)).subtotal
            card_data = json.dumps(cards)
            surfaced = any(t in card_data for t in ("49.0", '"price": 49,'))
            correct_product = "AR-1805" in card_data and "65W" in card_data
            passing = (
                finished
                and "AR-1805" in state.seen_products
                and surfaced
                and correct_product
                and subtotal == 0
            )
            extra = dict(
                cart_subtotal=subtotal,
                correct_product_in_card=correct_product,
                requested_price_in_card=surfaced,
                source_id_seen="AR-1805" in state.seen_products,
            )
        else:
            pending = back.ledger.pending()
            stock = (await back.get_listing(ctx, "AR-2102")).stock
            delta = None
            if len(pending) == 1 and len(pending[0].items) == 1:
                item = pending[0].items[0]
                if item.target == "AR-2102":
                    delta = int(item.after) - int(item.before)
            preview = bool(pending and any(pending[0].change_id in json.dumps(c) for c in cards))
            passing = (
                finished and stock == 3 and delta == 7 and not back.ledger.applied() and preview
            )
            extra = dict(
                staged=len(pending),
                applied=len(back.ledger.applied()),
                stock=stock,
                target_stock_delta=delta,
                matching_preview=preview,
            )
        summary[role] = dict(
            passed=bool(passing),
            transport_error=error,
            turn_complete=finished,
            calls=client.calls,
            tools=calls,
            skills_loaded=loaded,
            successful_tools=[e["data"]["tool"] for e in readings],
            ui=[c["component"] for c in cards],
            **extra,
        )
        save(out / "result.json", summary)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    opts = p.parse_args()
    opts.out.mkdir(parents=True, exist_ok=False)
    results = asyncio.run(run(opts.out))
    print(json.dumps(results, indent=2))
    sys.exit(0 if all(v["passed"] for v in results.values()) else 1)
