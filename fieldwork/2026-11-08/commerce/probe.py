"""Acceptance probes over unmodified, pinned commerce-agents retail code.

Run from its git-archive directory with its venv, PYTHONPATH=examples. Synthetic data only.
"""

import argparse
import asyncio
import json
from pathlib import Path

from commerce_common.skills import SkillRegistry
from commerce_common.testing import FakeClient, text_message, tool_use_message
from demo_common import MerchantIdentity, build_merchant_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from merchant_agent import MerchantSessionContext, MerchantSessionState
from merchant_agent.executor import MerchantToolExecutor
from merchant_agent_runtime import MerchantAgent
from retail.api.agent_config import build_merchant_config, build_shopping_config
from retail.api.mock_merchant import MockRetailMerchant
from retail.api.mock_retail import MockRetail
from shopping_agent import ShoppingSessionContext, ShoppingSessionState
from shopping_agent.executor import ShoppingToolExecutor
from shopping_agent_runtime import ShoppingAgent

ADAPTER, DECALS = "AR-1805", "AR-2102"
ROOT = Path.cwd()


def public_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def staged_id(outcome):
    assert not outcome.refused, outcome.result_text
    return next(
        event.data["change"]["change_id"]
        for event in outcome.events
        if event.type == "change_update"
    )


async def run(raw):
    trace = []
    results = {}
    shop = MockRetail()
    sh_config = build_shopping_config()
    sh_skills = SkillRegistry.from_dir(ROOT / "shopping-agent/skills")
    operator = MerchantIdentity(merchant_id="acme-retail", operator="Avery")
    m_config = build_merchant_config(shop.store_name)
    assert m_config.require_host_approval
    store = MockRetailMerchant(shop, m_config)
    m_skills = SkillRegistry.from_dir(ROOT / "merchant-agent/skills")
    shop_a = ShoppingSessionContext(user_id="demo-user", session_id="shop-a")
    shop_b = ShoppingSessionContext(user_id="demo-user-2", session_id="shop-b")

    def shopping(session):
        return ShoppingToolExecutor(
            backend=shop,
            config=sh_config,
            skills=sh_skills,
            session=session,
            state=ShoppingSessionState(),
        )

    a, b = shopping(shop_a), shopping(shop_b)
    owner = MerchantSessionContext(
        session_id="operator-e", merchant_id=operator.merchant_id, operator=operator.operator
    )
    ex = MerchantToolExecutor(
        backend=store, config=m_config, skills=m_skills, session=owner, state=MerchantSessionState()
    )

    async def execute(executor, name, args=None):
        outcome = await executor.execute(name, args or {})
        trace.append(
            dict(
                tool=name,
                args=args or {},
                text=outcome.result_text,
                is_error=outcome.is_error,
                blocked=outcome.blocked,
                events=[e.model_dump(mode="json") for e in outcome.events],
            )
        )
        return outcome

    # Only the HTTP session's call to list pending proposals needs a scripted decision;
    # neither it nor the decision to click constitutes evidence of real model behavior.
    shim = FakeClient([tool_use_message("get_pending_changes", {}), text_message("Listed.")])
    merchant = MerchantAgent(backend=store, config=m_config, skills=m_skills, client=shim)
    shopper = ShoppingAgent(backend=shop, config=sh_config, skills=sh_skills, client=FakeClient([]))
    for name, agent, executor, named_skill in [
        ("shopping", shopper, a, "search-discovery"),
        ("merchant", merchant, ex, "inventory-operations"),
    ]:
        index = agent.skills.index_block()
        assert len(agent.skills.names) == 5 and index in agent._static_system
        assert not any(
            agent.skills.get_instructions(n) in agent._static_system for n in agent.skills.names
        )
        ok = await execute(executor, "load_skill", {"skill_name": named_skill})
        assert not ok.refused and ok.result_text == agent.skills.get_instructions(named_skill)
        unknown = await execute(executor, "load_skill", {"skill_name": "nonexistent"})
        assert unknown.is_error
        results[f"{name}_skill"] = dict(
            index_names=agent.skills.names,
            initial_full_bodies=0,
            loaded=named_skill,
            chars=len(ok.result_text),
            unknown_refused=True,
        )

    search = await execute(a, "search_products", {"query": "universal travel adapter"})
    assert ADAPTER in search.result_text
    detail = await execute(a, "get_product_details", {"product_id": ADAPTER})
    assert all(value in detail.result_text for value in [ADAPTER, "65 W GaN", "49.0"])
    unseen = await execute(b, "add_to_cart", {"product_id": ADAPTER, "quantity": 1})
    assert unseen.blocked and (await shop.get_cart(shop_b)).item_count == 0
    add = await execute(a, "add_to_cart", {"product_id": ADAPTER, "quantity": 1})
    assert not add.refused and (await shop.get_cart(shop_a)).subtotal == 49.0
    assert (await shop.get_cart(shop_b)).item_count == 0
    denied = await execute(b, "get_order_status", {"order_id": "AR-78214"})
    allowed = await execute(a, "get_order_status", {"order_id": "AR-78214"})
    assert denied.is_error and "Mechanical Keyboard" not in denied.result_text
    assert "Mechanical Keyboard" in allowed.result_text
    results["shopping"] = dict(
        product=ADAPTER,
        catalog_price=49,
        output="65 W GaN",
        unseen_cart_gate=unseen.blocked,
        owner_cart_subtotal=49,
        other_cart_count=0,
        foreign_order_read_refused=True,
    )

    # Produce changes via the real agent executor; independently read the real mock backend.
    args = {"items": [{"listing_id": DECALS, "action": "restock", "quantity": 7}]}

    def get_listing_args(sku):
        return {"listing_id": sku}

    async def stock():
        return (await store.get_listing(owner, DECALS)).stock

    initial = await stock()
    assert initial == 3
    no_evidence = await execute(ex, "stage_inventory_action", args)
    assert no_evidence.blocked and not store.ledger.pending()
    assert not (await execute(ex, "get_listing", get_listing_args(DECALS))).refused
    too_much = await execute(
        ex, "stage_inventory_action", {"items": [dict(args["items"][0], quantity=501)]}
    )
    assert too_much.blocked and not store.ledger.pending()
    discard_id = staged_id(await execute(ex, "stage_inventory_action", args))
    apply_id = staged_id(await execute(ex, "stage_inventory_action", args))
    unapproved = await execute(ex, "apply_change", {"change_id": apply_id})
    assert unapproved.blocked and await stock() == initial
    # Two prices staged against the SAME live value, inside the 20% guardrail each.
    assert not (await execute(ex, "get_listing", get_listing_args(ADAPTER))).refused

    async def price():
        return (await store.get_listing(owner, ADAPTER)).price

    old = await price()

    def price_items(n):
        return {"items": [{"listing_id": ADAPTER, "new_price": n}]}

    cheaper, dearer = 40, 58
    assert old == 49
    decrease_id = staged_id(await execute(ex, "stage_price_update", price_items(cheaper)))
    increase_id = staged_id(await execute(ex, "stage_price_update", price_items(dearer)))
    assert await price() == old

    app = FastAPI()
    app.include_router(
        build_merchant_router(
            storefront=shop, backend=store, agent=merchant, identity=operator, example_dir="retail"
        ),
        prefix="/api/merchant",
    )
    with TestClient(app, base_url="http://localhost") as http:

        def post(path, **kw):
            return http.post("/api/merchant/" + path, **kw)

        h = {"X-Session-Id": post("session").json()["session_id"]}

        def decision(cid, action="apply", headers=h):
            res = post(f"changes/{cid}/{action}", headers=headers)
            trace.append(
                dict(route=f"changes/{cid}/{action}", status=res.status_code, response=res.json())
            )
            return res

        assert decision(apply_id, headers={}).status_code == 401
        assert decision(apply_id, headers={"X-Session-Id": "invented-session"}).status_code == 401
        unseen_change = decision(apply_id)
        assert (
            not unseen_change.json()["ok"]
            and "not staged or listed" in unseen_change.json()["reason"]
        )
        chat = post("chat", headers=h, json={"message": "List my pending changes."})
        assert (
            chat.status_code == 200
            and "event: turn_complete" in chat.text
            and "event: error" not in chat.text
        )
        assert "get_pending_changes" in chat.text and len(shim.calls) == 2
        trace.append(dict(route="chat", sse=chat.text))
        discard = decision(discard_id, "discard").json()["change"]
        assert discard["status"] == "discarded" and discard["discarded_by_kind"] == "operator"
        assert decision(discard_id).status_code == 400 and await stock() == initial
        apply = decision(apply_id).json()["change"]
        assert apply["status"] == "applied" and apply["applied_by"] == operator.operator
        after = await stock()
        assert (
            after == initial + 7
            and decision(apply_id).status_code == 400
            and await stock() == after
        )
        results["merchant"] = dict(
            target=DECALS,
            initial_stock=initial,
            stock_before_approval=initial,
            unstaged_evidence_gate=no_evidence.blocked,
            cap_gate=too_much.blocked,
            unapproved_apply_gate=unapproved.blocked,
            stock_after_discard=initial,
            stock_after_host_approval=after,
            repeat_apply_http=400,
            post_discard_apply_http=400,
            missing_or_invented_session_http=401,
            unseen_change_in_valid_session_refused=True,
            pending_visible_to_another_session_for_same_demo_merchant=True,
        )
        # Hypothesis under test: application enforces the configured cap against current price.
        dec = decision(decrease_id).json()
        earlier = await price()
        inc = decision(increase_id).json()
        later = await price()
        assert dec["ok"] and earlier == cheaper
        pct = round((later / earlier - 1) * 100, 2)
        second_applied = inc.get("ok") is True
        check = pct <= m_config.max_price_delta_pct if second_applied else later == earlier
        results["stale_price_probe"] = dict(
            property="applied move vs current price stays within configured cap",
            property_passed=check,
            snapshot_price=old,
            first_applied_price=earlier,
            second_staged_before=store.ledger.get(increase_id).items[0].before,
            second_approved=second_applied,
            final_price=later,
            actual_move_pct=pct,
            cap_pct=m_config.max_price_delta_pct,
        )
    public_json(raw / "trace.json", trace)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    p.add_argument(
        "--require-current-price-cap",
        action="store_true",
        help="exit 1 if approving an old price preview bypasses the current-price cap",
    )
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(run(args.out))
    public_json(args.out / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_current_price_cap and not result["stale_price_probe"]["property_passed"]:
        raise SystemExit(1)
