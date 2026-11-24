from src.domain.replay import run_replay


def present(raw):
    """把引擎原始结果转成与分析运行一致的前端结构（列对象 + 字典行）。"""
    tables = []
    for item in raw['tables']:
        columns = [{'key': str(i), 'label': label} for i, label in enumerate(item['columns'])]
        tables.append({'title': item['title'], 'columns': columns, 'rows': [
            dict(zip((col['key'] for col in columns), row, strict=True)) for row in item['rows']
        ]})
    series = raw.get('series') or []
    return {
        'headline': raw['summary'], 'note': ' '.join(raw.get('notes') or []),
        'metrics': raw['metrics'], 'tables': tables,
        'charts': [{'kind': 'bar', 'title': raw.get('series_title', '业务指标对比'), 'series': series}]
        if series else [],
        'ready': True, 'snapshot': raw.get('snapshot', []),
    }


def analyze_replay(versions, target_day, cutoff_at, params):
    return present(run_replay(versions, target_day, cutoff_at, params))