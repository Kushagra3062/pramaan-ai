"""Daily model-call budget: atomicity, reservation lifecycle and UTC-day semantics.

PostgreSQL-backed on a disposable database. Budget rows used here live far in the future
(2090-01-01 onwards) and are purged before and after every test, so no real day is touched.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.db.engine import SessionLocal, engine
from app.services.model_budget import (
    DailyReservation,
    ModelBudgetExceededError,
    NoopModelBudget,
    PostgresBudgetStore,
    PostgresDailyModelBudget,
    seconds_until_next_utc_day,
    utc_budget_date,
)

TEST_DAY = date(2090, 1, 1)
TEST_DAY_2 = date(2090, 1, 2)
IST = ZoneInfo("Asia/Kolkata")


def clock(day: date = TEST_DAY, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _purge_budget_rows():
    def purge() -> None:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM model_call_budget WHERE budget_date >= DATE '2090-01-01'")
            )

    purge()
    yield
    purge()


@pytest.fixture
def store() -> PostgresBudgetStore:
    return PostgresBudgetStore(SessionLocal)


def make_budget(store: PostgresBudgetStore, limit: int = 5, day: date = TEST_DAY):
    return PostgresDailyModelBudget(limit=limit, store=store, clock=lambda: clock(day))


# ── UTC clock ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 2, 23, 59, 59, tzinfo=UTC), date(2026, 11, 2)),
        (datetime(2026, 11, 3, 0, 0, 0, tzinfo=UTC), date(2026, 11, 3)),
        # 2026-11-02 23:00 UTC is already 2026-11-03 locally in IST; the budget stays on UTC.
        (datetime(2026, 11, 2, 23, 0, tzinfo=UTC).astimezone(IST), date(2026, 11, 2)),
        (datetime(2026, 11, 3, 20, 0, tzinfo=UTC).astimezone(IST), date(2026, 11, 3)),
        (datetime(2026, 11, 3, 12, 0), date(2026, 11, 3)),  # naive is treated as UTC
    ],
)
def test_utc_budget_date(moment, expected):
    assert utc_budget_date(moment) == expected


def test_seconds_until_next_utc_day():
    assert seconds_until_next_utc_day(datetime(2026, 11, 2, 23, 59, tzinfo=UTC)) == 60
    assert seconds_until_next_utc_day(datetime(2026, 11, 2, 0, 0, tzinfo=UTC)) == 86400


# ── Reservation lifecycle ────────────────────────────────────────────────────


def test_reserves_within_budget(store):
    reservation = make_budget(store, limit=5).reserve(2)
    assert isinstance(reservation, DailyReservation)
    assert store.current(budget_date=TEST_DAY) == 2
    reservation.consume()
    reservation.consume()
    assert reservation.release_unused() == 0
    assert store.current(budget_date=TEST_DAY) == 2


def test_exactly_reaching_the_limit_is_admitted(store):
    budget = make_budget(store, limit=3)
    first = budget.reserve(2)
    second = budget.reserve(1)
    assert store.current(budget_date=TEST_DAY) == 3
    first.release_unused()
    second.release_unused()
    assert store.current(budget_date=TEST_DAY) == 0  # nothing was consumed


def test_one_over_remaining_is_rejected(store):
    budget = make_budget(store, limit=2)
    budget.reserve(2).consume()
    with pytest.raises(ModelBudgetExceededError) as info:
        budget.reserve(1)
    assert (info.value.limit, info.value.requested, info.value.remaining) == (2, 1, 0)
    assert info.value.budget_date == TEST_DAY
    assert "postgresql" not in str(info.value).lower()


def test_request_larger_than_the_whole_limit_has_no_side_effects(store):
    with pytest.raises(ModelBudgetExceededError):
        make_budget(store, limit=2).reserve(3)
    assert store.current(budget_date=TEST_DAY) == 0


def test_unconsumed_slots_are_released_once(store):
    reservation = make_budget(store, limit=4).reserve(3)
    reservation.consume()
    assert reservation.release_unused() == 2
    assert store.current(budget_date=TEST_DAY) == 1
    assert reservation.release_unused() == 0  # idempotent
    assert store.current(budget_date=TEST_DAY) == 1


def test_consumed_slots_are_never_released(store):
    reservation = make_budget(store, limit=4).reserve(2)
    reservation.consume()
    reservation.consume()
    assert reservation.release_unused() == 0
    assert store.current(budget_date=TEST_DAY) == 2


def test_refund_returns_a_slot_whose_request_never_left_the_process(store):
    reservation = make_budget(store, limit=4).reserve(2)
    reservation.consume()
    reservation.refund()
    assert reservation.release_unused() == 2
    assert store.current(budget_date=TEST_DAY) == 0


def test_abandoned_reservation_keeps_counting(store):
    budget = make_budget(store, limit=2)
    abandoned = budget.reserve(2)
    abandoned.consume()
    # Simulated crash: release_unused() is never called.
    assert store.current(budget_date=TEST_DAY) == 2
    with pytest.raises(ModelBudgetExceededError):
        budget.reserve(1)


def test_release_failure_is_contained_and_capacity_stays_held(store, monkeypatch):
    reservation = make_budget(store, limit=3).reserve(2)

    def boom(**_kwargs):
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(store, "release", boom)
    assert reservation.release_unused() == 0
    assert store.current(budget_date=TEST_DAY) == 2


def test_next_utc_day_gets_a_fresh_budget(store):
    day_one = make_budget(store, limit=2, day=TEST_DAY)
    day_one.reserve(2).consume()
    with pytest.raises(ModelBudgetExceededError):
        day_one.reserve(1)

    day_two = make_budget(store, limit=2, day=TEST_DAY_2)
    day_two.reserve(2)
    assert store.current(budget_date=TEST_DAY_2) == 2
    assert store.current(budget_date=TEST_DAY) == 2  # yesterday is untouched


def test_snapshot_reports_remaining_capacity(store):
    budget = make_budget(store, limit=3)
    first = budget.snapshot()
    assert (first.budget_date, first.limit, first.remaining) == (TEST_DAY, 3, 3)
    budget.reserve(2).consume()
    assert budget.snapshot().remaining == 1


def test_noop_budget_never_touches_the_database(store):
    budget = NoopModelBudget()
    reservation = budget.reserve(5)
    reservation.consume()
    reservation.consume()
    reservation.refund()
    assert reservation.release_unused() == 0
    assert store.current(budget_date=TEST_DAY) == 0
    assert budget.snapshot().reserved_calls == 0


# ── Competing transactions ───────────────────────────────────────────────────


def _run_threads(workers: list[threading.Thread], timeout: float = 30.0) -> None:
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout)
    assert all(not worker.is_alive() for worker in workers), "worker thread hung"


def test_competing_reservations_cannot_exceed_the_limit(store):
    budget = make_budget(store, limit=3)
    barrier = threading.Barrier(5)
    admitted: list[int] = []
    rejected: list[int] = []
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            budget.reserve(1).consume()
            admitted.append(index)
        except ModelBudgetExceededError:
            rejected.append(index)
        except BaseException as exc:  # surfaced by the assertion below
            errors.append(exc)

    _run_threads([threading.Thread(target=worker, args=(i,)) for i in range(5)])

    assert errors == []
    assert len(admitted) == 3
    assert len(rejected) == 2
    assert store.current(budget_date=TEST_DAY) == 3  # no lost updates


def test_two_large_reservations_never_both_win(store):
    budget = make_budget(store, limit=3)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(name: str) -> None:
        barrier.wait(timeout=10)
        try:
            budget.reserve(2).consume()
            outcomes.append(f"{name}:admitted")
        except ModelBudgetExceededError:
            outcomes.append(f"{name}:rejected")

    _run_threads([threading.Thread(target=worker, args=(n,)) for n in ("a", "b")])

    assert sorted(o.split(":")[1] for o in outcomes) == ["admitted", "rejected"]
    assert store.current(budget_date=TEST_DAY) == 2


def test_repeated_competition_never_over_books(store):
    """Three rounds of five racers against a limit of two: exactly two winners each round."""
    budget = make_budget(store, limit=2)
    for _round in range(3):
        barrier = threading.Barrier(5)
        winners: list[int] = []

        def worker(index: int) -> None:
            barrier.wait(timeout=10)
            try:
                budget.reserve(1).consume()
                winners.append(index)
            except ModelBudgetExceededError:
                return

        _run_threads([threading.Thread(target=worker, args=(i,)) for i in range(5)])
        assert len(winners) == 2
        assert store.current(budget_date=TEST_DAY) == 2
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM model_call_budget WHERE budget_date >= DATE '2090-01-01'"))
