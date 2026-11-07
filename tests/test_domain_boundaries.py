from loop_lab.domain import BusinessWorld


def test_other_users_data_and_expired_returns_are_rejected(tmp_path):
    world = BusinessWorld(
        tmp_path / "state.sqlite",
        user_id="alice",
        orders={
            "OTHER": {
                "owner": "bob",
                "status": "delivered",
                "age_days": 1,
                "amount_cents": 9999,
                "tracking_id": "secret",
            },
            "OLD": {
                "owner": "alice",
                "status": "delivered",
                "age_days": 31,
                "amount_cents": 1200,
                "tracking_id": "S2",
            },
        },
        shipments={},
    )
    assert world.get_order("OTHER")["error"] == "access_denied"
    assert "9999" not in str(world.get_order("OTHER"))
    assert world.create_return("OTHER", approved=True)["error"] == "access_denied"
    assert world.create_return("OLD", approved=True)["error"] == "ineligible"
    assert world.receipts() == []
