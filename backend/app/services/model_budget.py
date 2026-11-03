"""Installation-wide daily model-call budget.

Semantics
    ``DAILY_MODEL_CALL_LIMIT`` bounds the *total* number of outbound Meta requests for the
    whole installation per UTC calendar day, across all workspaces and users. It is
    independent of the per-request cap (`MAX_MODEL_CALLS_PER_REQUEST`), which limits how many
    calls one document may use.

Concurrency
    Capacity is taken with a single atomic statement executed in its own short transaction::

        INSERT INTO model_call_budget (budget_date, reserved_calls) VALUES (:day, :n)
        ON CONFLICT (budget_date) DO UPDATE
            SET reserved_calls = model_call_budget.reserved_calls + :n
            WHERE model_call_budget.reserved_calls + :n <= :limit
        RETURNING reserved_calls

    PostgreSQL re-evaluates the ``WHERE`` clause against the committed row when a competing
    statement wins the conflict, so at most one of two racers can take the last slots. There
    is no check-then-increment anywhere, and no in-process lock or counter is involved, so the
    limit holds across Cloud Run instances as well.

Lifecycle
    1. ``reserve(n)`` holds ``n`` slots before the first model call. Insufficient capacity
       raises ``ModelBudgetExceededError`` and the whole run is rejected: a document is never
       partially extracted because only some of its calls fit.
    2. ``consume()`` runs immediately before each real outbound request, so a slot spent on a
       request that may be in flight during a crash can never be released afterwards.
    3. ``refund()`` is used only when the extractor reports that the request never left the
       process (for example the provider client could not be constructed).
    4. ``release_unused()`` returns reserved but unconsumed slots once the model phase ends,
       on success or failure. It is idempotent.

Interrupted runs and the UTC reset
    A crashed process never reaches ``release_unused``, so its slots stay counted for that
    day: capacity can be under-used but never over-used. The next UTC day is a new row, so the
    budget resets naturally at 00:00 UTC without a scheduled job.

Budget versus usage
    This counter is not token accounting. ``ModelUsage`` remains the record of actual provider
    usage (tokens, latency, status); a failed request still consumes a slot but may have no
    token numbers at all.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.audit_eval import ModelCallBudget

logger = logging.getLogger(__name__)

_TABLE = ModelCallBudget.__table__


# ── Clock helpers ─────────────────────────────────────────────────────────────


def utc_budget_date(now: datetime | None = None) -> date:
    """UTC calendar day that keys the budget. Naive datetimes are treated as UTC."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def seconds_until_next_utc_day(now: datetime | None = None) -> int:
    """Seconds until the budget resets, used for the ``Retry-After`` header."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    next_day = datetime.combine(moment.date() + timedelta(days=1), time.min, tzinfo=UTC)
    return max(int((next_day - moment).total_seconds()), 1)


# ── Errors and values ─────────────────────────────────────────────────────────


class ModelBudgetExceededError(Exception):
    """The remaining daily capacity cannot cover the request; nothing was sent."""

    def __init__(
        self, *, requested: int, limit: int, reserved_calls: int, budget_date: date
    ) -> None:
        self.requested = requested
        self.limit = limit
        self.reserved_calls = reserved_calls
        self.budget_date = budget_date
        self.remaining = max(limit - reserved_calls, 0)
        super().__init__(
            f"daily model call budget exhausted for {budget_date.isoformat()}: "
            f"requested {requested}, remaining {self.remaining} of {limit}"
        )


@dataclass(frozen=True)
class BudgetSnapshot:
    budget_date: date
    limit: int
    reserved_calls: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.reserved_calls, 0)


# ── Reservations ──────────────────────────────────────────────────────────────


class Reservation(Protocol):
    """Capacity held for one run. Consumed slots can never be released again."""

    def consume(self) -> None: ...

    def refund(self) -> None: ...

    def release_unused(self) -> int: ...


class NoopReservation:
    """Reservation for callers that spend no provider calls (fakes, local OCR)."""

    reserved = 0
    consumed = 0

    def consume(self) -> None:
        return None

    def refund(self) -> None:
        return None

    def release_unused(self) -> int:
        return 0


@dataclass
class DailyReservation:
    """Slots held against one UTC day; local consumption decides what is released."""

    store: PostgresBudgetStore
    budget_date: date
    limit: int
    reserved: int
    consumed: int = 0
    released: bool = False

    def consume(self) -> None:
        if not self.released:
            self.consumed += 1

    def refund(self) -> None:
        if not self.released and self.consumed > 0:
            self.consumed -= 1

    @property
    def unused(self) -> int:
        return max(self.reserved - self.consumed, 0)

    def release_unused(self) -> int:
        """Return slots whose requests never happened. Idempotent, never raises."""
        if self.released:
            return 0
        self.released = True
        unused = self.unused
        if not unused:
            return 0
        try:
            self.store.release(budget_date=self.budget_date, count=unused)
        except Exception as exc:  # capacity stays held: conservative, never over-spend
            logger.warning(
                "model_budget_release_failed",
                extra={
                    "event": "model_budget.release_failed",
                    "budget_date": self.budget_date.isoformat(),
                    "unused_calls": unused,
                    "error_type": type(exc).__name__,
                },
            )
            return 0
        return unused


# ── PostgreSQL store ──────────────────────────────────────────────────────────


class PostgresBudgetStore:
    """Atomic counter operations. Every call is its own short transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def try_reserve(self, *, budget_date: date, count: int, limit: int) -> int | None:
        """Atomically take ``count`` slots. Returns the new total, or None if insufficient."""
        if count <= 0:
            return self.current(budget_date=budget_date)
        if count > limit:
            return None
        statement = (
            pg_insert(_TABLE)
            .values(budget_date=budget_date, reserved_calls=count)
            .on_conflict_do_update(
                index_elements=[_TABLE.c.budget_date],
                set_={
                    "reserved_calls": _TABLE.c.reserved_calls + count,
                    "updated_at": func.now(),
                },
                where=(_TABLE.c.reserved_calls + count) <= limit,
            )
            .returning(_TABLE.c.reserved_calls)
        )
        with self._session_factory() as session:
            reserved = session.scalar(statement)
            session.commit()
        return None if reserved is None else int(reserved)

    def current(self, *, budget_date: date) -> int:
        with self._session_factory() as session:
            value = session.scalar(
                select(_TABLE.c.reserved_calls).where(_TABLE.c.budget_date == budget_date)
            )
        return int(value or 0)

    def release(self, *, budget_date: date, count: int) -> None:
        if count <= 0:
            return
        with self._session_factory() as session:
            session.execute(
                update(_TABLE)
                .where(_TABLE.c.budget_date == budget_date)
                .values(
                    reserved_calls=func.greatest(_TABLE.c.reserved_calls - count, 0),
                    updated_at=func.now(),
                )
            )
            session.commit()


# ── Budgets ───────────────────────────────────────────────────────────────────


class ModelBudget(Protocol):
    limit: int

    def reserve(self, count: int = 1) -> Reservation: ...

    def snapshot(self) -> BudgetSnapshot: ...


class NoopModelBudget:
    """Budget for paths that spend nothing (test fakes, diagnostics, local OCR)."""

    limit = 0

    def reserve(self, count: int = 1) -> Reservation:
        return NoopReservation()

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(budget_date=utc_budget_date(), limit=0, reserved_calls=0)


class PostgresDailyModelBudget:
    """The real, installation-wide daily budget backed by ``model_call_budget``."""

    def __init__(
        self,
        limit: int,
        store: PostgresBudgetStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.limit = limit
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def snapshot(self) -> BudgetSnapshot:
        today = utc_budget_date(self._clock())
        return BudgetSnapshot(
            budget_date=today,
            limit=self.limit,
            reserved_calls=self._store.current(budget_date=today),
        )

    def reserve(self, count: int = 1) -> Reservation:
        if count <= 0:
            return NoopReservation()
        today = utc_budget_date(self._clock())
        # A UTC midnight crossed mid-reservation simply retries once on the new day.
        for _ in range(2):
            reserved = self._store.try_reserve(budget_date=today, count=count, limit=self.limit)
            if reserved is not None:
                return DailyReservation(
                    store=self._store,
                    budget_date=today,
                    limit=self.limit,
                    reserved=count,
                )
            day_now = utc_budget_date(self._clock())
            if day_now == today:
                raise ModelBudgetExceededError(
                    requested=count,
                    limit=self.limit,
                    reserved_calls=self._store.current(budget_date=today),
                    budget_date=today,
                )
            today = day_now
        raise ModelBudgetExceededError(  # pragma: no cover - defensive
            requested=count,
            limit=self.limit,
            reserved_calls=self._store.current(budget_date=today),
            budget_date=today,
        )


# ── Process-level budget ──────────────────────────────────────────────────────

_default_budget: ModelBudget | None = None
_budget_lock = threading.Lock()


def build_default_budget() -> ModelBudget:
    """Real budget from settings; imported lazily so module import stays cheap."""
    from app.db.engine import SessionLocal

    return PostgresDailyModelBudget(
        limit=get_settings().daily_model_call_limit,
        store=PostgresBudgetStore(SessionLocal),
    )


def get_model_budget() -> ModelBudget:
    global _default_budget
    if _default_budget is None:
        with _budget_lock:
            if _default_budget is None:
                _default_budget = build_default_budget()
    return _default_budget


def override_model_budget(budget: ModelBudget) -> ModelBudget | None:
    """Install a specific budget (tests); returns the previous one."""
    global _default_budget
    with _budget_lock:
        previous = _default_budget
        _default_budget = budget
    return previous


def reset_model_budget() -> None:
    global _default_budget
    with _budget_lock:
        _default_budget = None
