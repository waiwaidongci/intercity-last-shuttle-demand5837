import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_rows
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.replay import ArrivalBatch
from src.models.source import Dataset
from src.services.arrival_seed import seed_arrivals
from src.services.arrivals import save_batch
from src.services.datasets import save
from src.utils.security import hash_password


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        arrival_batches = []
        if not db.scalar(select(ArrivalBatch).limit(1)):
            arrival_batches = seed_arrivals(db, save_batch)
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created, 'arrival_batches': arrival_batches}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
