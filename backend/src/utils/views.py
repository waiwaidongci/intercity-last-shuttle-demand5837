from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def arrival_batch(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'state': item.state,
            'row_count': item.row_count, 'reject_reason': item.reject_reason,
            'author': item.author, 'created_at': iso(item.created_at)}


def arrival_record(item):
    return {'id': item.id, 'batch_id': item.batch_id,
            'service_date': item.service_day.isoformat(), 'route': item.route,
            'passengers': item.passengers, 'scheduled_seats': item.scheduled_seats,
            'posted_at': item.posted_at.isoformat(timespec='seconds')}


def replay(item, detail=False):
    view = {'id': item.id, 'name': item.name, 'target_day': item.target_day.isoformat(),
            'cutoff_at': item.cutoff_at.isoformat(timespec='seconds'), 'status': item.status,
            'sealed': item.sealed, 'reruns': item.reruns, 'error': item.error,
            'author': item.author, 'created_at': iso(item.created_at)}
    if detail:
        view.update(params=item.params, result=item.result)
    return view
