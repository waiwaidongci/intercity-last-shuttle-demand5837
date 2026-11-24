from datetime import date, datetime
from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import BatchState, ReplayStatus
from src.utils.time import now


class ArrivalBatch(Base):
    """站务实到补报批次。同刻人数冲突时整批拒收（state=rejected），不写明细。"""
    __tablename__ = 'arrival_batches'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(20), default=BatchState.ACCEPTED, index=True)
    row_count: Mapped[int] = mapped_column(default=0)
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)


class ArrivalRecord(Base):
    """按（运营日、线路、入账时刻）保留的实到版本，同一时刻只允许一个人数版本。"""
    __tablename__ = 'arrival_records'
    __table_args__ = (
        UniqueConstraint('service_day', 'route', 'posted_at', name='uq_arrival_moment'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('arrival_batches.id'), index=True)
    service_day: Mapped[date] = mapped_column(Date, index=True)
    route: Mapped[str] = mapped_column(String(80), index=True)
    passengers: Mapped[int | None]
    scheduled_seats: Mapped[int | None]
    posted_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Replay(Base):
    """备车方案回放。同一目标晚 + 核账截点唯一封存，重跑不新增方案。"""
    __tablename__ = 'replays'
    __table_args__ = (
        UniqueConstraint('target_day', 'cutoff_at', name='uq_replay_target_cutoff'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    target_day: Mapped[date] = mapped_column(Date, index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    params: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=ReplayStatus.COMPLETE, index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    sealed: Mapped[bool] = mapped_column(Boolean, default=True)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    reruns: Mapped[int] = mapped_column(default=0)
