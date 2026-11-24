from datetime import date

from sqlalchemy import select

from src.constants.enums import BatchState, ReplayStatus
from src.domain.replay import Version, ReplayError
from src.domain.replay_flow import (
    decide_batch,
    normalize_replay_params,
    parse_cutoff,
    parse_target_day,
    seal_replay,
)
from src.models.replay import ArrivalBatch, ArrivalRecord, Replay
from src.services.audit import record


def save_batch(db, name, filename, rows, actor):
    """校验并入库补报批次；同刻人数冲突 -> 记录拒收批次并抛出 ValueError。"""
    existing = {
        (item.service_day.toordinal(), item.route, item.posted_at): item.passengers
        for item in db.scalars(select(ArrivalRecord)).all()
    }
    decision, reason, versions = decide_batch(rows, existing)
    if decision == 'reject':
        rejected = ArrivalBatch(name=name, filename=filename, state=BatchState.REJECTED,
                                row_count=len(versions), reject_reason=reason[:500], author=actor)
        db.add(rejected)
        record(db, actor, 'arrival.rejected', 0, reason=reason[:500], filename=filename)
        db.commit()
        raise ValueError(reason)

    batch = ArrivalBatch(name=name, filename=filename, state=BatchState.ACCEPTED,
                         row_count=len(versions), author=actor)
    db.add(batch)
    db.flush()
    for version in versions:
        db.add(ArrivalRecord(batch_id=batch.id, service_day=date.fromordinal(version.day),
                             route=version.route, passengers=version.passengers,
                             scheduled_seats=version.seats, posted_at=version.posted_at))
    record(db, actor, 'arrival.accepted', batch.id, rows=len(versions))
    db.commit()
    return batch


def load_versions(db):
    return [
        Version(item.id, item.batch_id, item.service_day.toordinal(), item.route,
                item.passengers, item.scheduled_seats, item.posted_at)
        for item in db.scalars(select(ArrivalRecord).order_by(ArrivalRecord.id)).all()
    ]


def create_replay(db, name, target_day, cutoff_at, params, actor):
    """幂等回放：同目标晚 + 同核账截点直接返回封存回放，不新增方案。"""
    target = parse_target_day(target_day)
    cutoff = parse_cutoff(cutoff_at)
    settings = normalize_replay_params(params)

    existing = db.scalar(select(Replay).where(Replay.target_day == target, Replay.cutoff_at == cutoff))
    if existing is not None:
        if existing.status == ReplayStatus.COMPLETE:
            # 成功封存的方案不可变：同参数重跑只留痕，不新增方案
            existing.reruns += 1
            db.commit()
            return existing, False
        # 证据不足的失败记录允许用补全后的数据重试：删除旧失败记录后重算
        db.delete(existing)
        db.flush()

    versions = load_versions(db)
    item = Replay(name=name, target_day=target, cutoff_at=cutoff, params=settings,
                  status=ReplayStatus.COMPLETE, sealed=True, author=actor)
    try:
        item.result = seal_replay(versions, target.toordinal(), cutoff, settings)
    except ReplayError as error:
        item.status = ReplayStatus.FAILED
        item.error = str(error)[:500]
        item.result = None
        db.add(item)
        record(db, actor, 'replay.failed', 0, target_day=target.isoformat(),
               cutoff_at=cutoff.isoformat(timespec='seconds'), reason=item.error)
        db.commit()
        raise
    db.add(item)
    record(db, actor, 'replay.sealed', 0, target_day=target.isoformat(),
           cutoff_at=cutoff.isoformat(timespec='seconds'))
    db.commit()
    return item, True
