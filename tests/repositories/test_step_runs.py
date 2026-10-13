"""Step-run repository contract: idempotent scheduling, due-claiming, open-run checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.entities.step_run import StepRun, StepRunStatus
from domain.value_objects.ids import StepRunId
from tests.repositories.conftest import make_attempt, seed
from tests.repositories.test_delivery_attempts import add

NOW = datetime.now(timezone.utc)


def run(g, step_id="a", index=0, run_at=NOW):
    return StepRun(
        id=StepRunId.new(), tenant_id=g.tenant, notification_id=g.notification.id,
        recipient_id=g.recipient.id, step_id=step_id, step_index=index, run_at=run_at,
    )


async def test_a_step_is_scheduled_once_per_recipient(env):
    g = await seed(env)
    async with env.uow() as uow:
        assert await uow.step_runs.add(run(g)) is True
        assert await uow.step_runs.add(run(g)) is False            # same notification, recipient and step
        assert await uow.step_runs.add(run(g, "b", 1)) is True
        await uow.commit()
        got = await uow.step_runs.list_for_notification(g.tenant, g.notification.id)
    assert [r.step_id for r in got] == ["a", "b"]


async def test_claim_due_returns_only_scheduled_runs_whose_time_has_come_oldest_first(env):
    g = await seed(env)
    async with env.uow() as uow:
        await uow.step_runs.add(run(g, "late", 2, NOW - timedelta(minutes=1)))
        await uow.step_runs.add(run(g, "early", 0, NOW - timedelta(minutes=9)))
        await uow.step_runs.add(run(g, "future", 3, NOW + timedelta(minutes=9)))
        done = run(g, "done", 1, NOW - timedelta(minutes=5))
        await uow.step_runs.add(done)
        done.mark_skipped("nope", NOW)
        await uow.step_runs.save(done)
        await uow.commit()
    async with env.uow() as uow:
        claimed = await uow.step_runs.claim_due(NOW, limit=10)
        limited = await uow.step_runs.claim_due(NOW, limit=1)
    assert [r.step_id for r in claimed] == ["early", "late"]
    assert [r.step_id for r in limited] == ["early"]


async def test_save_persists_the_outcome(env):
    g = await seed(env)
    attempt = make_attempt(g)
    await add(env, attempt)
    r = run(g)
    async with env.uow() as uow:
        await uow.step_runs.add(r)
        await uow.commit()
    r.mark_done(attempt.id, NOW)
    async with env.uow() as uow:
        await uow.step_runs.save(r)
        await uow.commit()
    async with env.uow() as uow:
        got = await uow.step_runs.get(g.notification.id, g.recipient.id, "a")
    assert got.status is StepRunStatus.DONE and got.attempt_id == attempt.id and got.executed_at is not None


async def test_has_open_is_true_only_while_something_is_scheduled(env):
    g = await seed(env)
    other = await seed(env)
    r = run(g)
    async with env.uow() as uow:
        assert not await uow.step_runs.has_open(g.notification.id)
        await uow.step_runs.add(r)
        await uow.commit()
        assert await uow.step_runs.has_open(g.notification.id)
        assert not await uow.step_runs.has_open(other.notification.id)
    r.mark_skipped("done", NOW)
    async with env.uow() as uow:
        await uow.step_runs.save(r)
        await uow.commit()
        assert not await uow.step_runs.has_open(g.notification.id)


async def test_list_is_scoped_to_the_tenant(env):
    g = await seed(env)
    stranger = await seed(env)
    async with env.uow() as uow:
        await uow.step_runs.add(run(g))
        await uow.commit()
        assert await uow.step_runs.list_for_notification(stranger.tenant, g.notification.id) == []
