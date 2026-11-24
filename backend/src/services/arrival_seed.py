"""演示用实到补报数据：首批 17:50 入账 + 夜间补报，演示版本、18:00 截点与封存。"""
from datetime import datetime

from src.domain.demo import demo_rows
from src.domain.spec import DEMO
from src.services.validation import parse_arrival_rows

FIRST_BATCH = '首报批次_每日17点50入账'
SUPPLEMENT_BATCH = '夜间补报批次_含18点前修正与封存后补录'
FIRST_POSTED = ' 17:50:00'


def first_report_rows():
    rows = []
    for row in demo_rows():
        posted = f"{row['service_date']}{FIRST_POSTED}"
        rows.append({'service_date': row['service_date'], 'route': row['route'],
                     'posted_at': posted, 'passengers': row['passengers'],
                     'scheduled_seats': row['scheduled_seats']})
    return rows


def supplement_rows():
    return parse_arrival_rows((DEMO / 'arrivals_supplement.csv').read_text(encoding='utf-8-sig'))


def seed_arrivals(db, save_batch):
    created = []
    for name, rows in ((FIRST_BATCH, first_report_rows()), (SUPPLEMENT_BATCH, supplement_rows())):
        batch = save_batch(db, name, f'{name}.csv', rows, 'seed')
        created.append({'id': batch.id, 'name': batch.name, 'state': batch.state,
                        'row_count': batch.row_count})
    return created
