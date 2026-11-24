from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.replay import ArrivalBatch, ArrivalRecord, Replay
from src.schemas.requests import ArrivalUpload, ReplayRequest
from src.services.arrivals import create_replay, save_batch
from src.services.validation import parse_arrival_rows
from src.utils import views

router = APIRouter()


@router.get('/arrival-batches')
def arrival_batches(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(ArrivalBatch).order_by(ArrivalBatch.id.desc()).limit(100))
    return [views.arrival_batch(item) for item in items]


@router.get('/arrival-records')
def arrival_records(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(ArrivalRecord).order_by(
        ArrivalRecord.service_day.desc(), ArrivalRecord.route, ArrivalRecord.posted_at).limit(1000))
    return [views.arrival_record(item) for item in items]


@router.post('/arrival-batches', status_code=201)
def upload_arrival(body: ArrivalUpload, db: Session = Depends(connection),
                   actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = parse_arrival_rows(body.csv_text)
    batch = save_batch(db, body.name, body.filename, rows, actor.username)
    return {**views.arrival_batch(batch), 'created': True}


@router.get('/replays')
def replays(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(Replay).order_by(Replay.id.desc()).limit(100))
    return [views.replay(item) for item in items]


@router.post('/replays', status_code=201)
def start_replay(body: ReplayRequest, db: Session = Depends(connection),
                 actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item, created = create_replay(db, body.name, body.target_day, body.cutoff_at, body.params,
                                  actor.username)
    return {**views.replay(item, detail=True), 'created': created}


@router.get('/replays/{replay_id}')
def replay_detail(replay_id: int, db: Session = Depends(connection),
                  actor: User = Depends(current)):
    item = db.get(Replay, replay_id)
    if not item:
        raise HTTPException(404, '找不到该回放方案')
    return views.replay(item, detail=True)
