"""补报批次受理与回放封存的纯逻辑编排（无数据库/第三方依赖）。

DB 层（services/arrivals.py）只负责把这里的决策落库，保证核心规则可独立单测。
"""
from datetime import date, datetime

from src.domain.replay import (
    PARAMETERS,
    ReplayError,
    parse_batch_rows,
    reject_conflicts,
    run_replay,
)
from src.domain.replay_view import present


def _int_param(value, field):
    if type(value) is not int or not field['min'] <= value <= field['max']:
        raise ValueError(f'{field["label"]}须为 {field["min"]} 至 {field["max"]} 之间的整数')
    return value


def normalize_replay_params(params):
    """回放参数不含 forecast_date（目标晚单独传），未知参数拒绝、其余填默认并校界。"""
    allowed = {key: field for key, field in PARAMETERS.items() if key != 'forecast_date'}
    if set(params) - set(allowed):
        raise ValueError('存在未知回放参数，请核对回放面板')
    return {key: _int_param(params.get(key, field['default']), field) for key, field in allowed.items()}


def parse_target_day(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValueError('目标晚须为有效日期，格式为 YYYY-MM-DD') from None


def parse_cutoff(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('核账截点不能为空')
    try:
        stamp = datetime.fromisoformat(raw.strip().replace(' ', 'T'))
    except ValueError:
        raise ValueError('核账截点格式不正确，应为 YYYY-MM-DDTHH:MM') from None
    if stamp.tzinfo is not None:
        raise ValueError('核账截点请使用北京时间本地时间，不要带时区偏移')
    return stamp.replace(second=0, microsecond=0)


def decide_batch(rows, existing):
    """返回 ('accept', None, versions) 或 ('reject', reason, versions)。

    existing: {(day_ordinal, route, posted_at): passengers|None} 库内已入账版本。
    """
    versions = parse_batch_rows(rows)
    try:
        reject_conflicts(versions, existing)
    except ValueError as error:
        return 'reject', str(error), versions
    return 'accept', None, versions


def seal_replay(versions, target_day, cutoff_at, settings):
    """计算回放并转前端结构；证据不足抛 ReplayError，由调用方记整批失败。"""
    return present(run_replay(versions, target_day, cutoff_at, settings))
