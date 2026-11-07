from concurrent.futures import ThreadPoolExecutor

from loop_lab.models import Budget, BudgetExceeded


def test_shared_budget_cannot_be_oversubscribed_by_parallel_specialists():
    budget = Budget(max_calls=3)

    def attempt(_):
        try:
            budget.reserve()
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        allowed = list(pool.map(attempt, range(12)))
    assert sum(allowed) == 3


def test_token_budget_stops_new_requests_after_accounted_usage():
    budget = Budget(max_tokens=100)
    budget.charge({"input_tokens": 90, "output_tokens": 10})
    try:
        budget.reserve()
    except BudgetExceeded:
        return
    raise AssertionError("Model call was allowed after token budget was spent")


def test_unknown_usage_halts_new_spending():
    budget = Budget()
    budget.mark_unknown()
    try:
        budget.reserve()
    except BudgetExceeded:
        return
    raise AssertionError("Unknown usage did not halt spending")


def test_dispatch_waits_for_in_flight_reservation_to_settle():
    budget = Budget(max_tokens=60000)
    budget.reserve()
    assert budget.can_dispatch() is False
    budget.charge({"input_tokens": 1000, "output_tokens": 100})
    assert budget.can_dispatch() is True
