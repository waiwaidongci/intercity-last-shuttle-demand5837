"""实到补报版本合并与备车方案时点回放引擎（纯标准库，无数据库依赖）。

口径要点：
- 补报按（运营日、线路、入账时刻）保留各版；同一入账时刻人数冲突 -> 整批拒收。
- 回放任一原点的可见截点 = min(核账截点, 该运营日 18:00)，只取截点前最新版。
- 缺测（无可见版本或版本实到为空）一律保持 None，不按零参与计算。
- 任一路线证据不足 -> ReplayError，整批失败，不产生方案。
- 每个回测折都记录取数版本与跳过原因，供回放审计。
"""
from dataclasses import dataclass
from datetime import date, datetime, time
from fractions import Fraction
from math import ceil

from src.domain.spec import BUSINESS

NAIVE = '季节朴素'
MOVING = '近期/季节移动'

PARAMETERS = {field['key']: field for field in BUSINESS['parameters']}
ROW_FIELDS = {field['key']: field for field in BUSINESS['fields']}
DAY_CUTOFF = time(18, 0)

# 跳过原因（稳定文案，同时作为机器可读的原因码）
SKIP_NO_VERSION = '截点前无入账版本，按缺测处理（不计为零）'
SKIP_NULL_ACTUAL = '可见版本实到留空，按缺测处理（不计为零）'
SKIP_NAIVE_HISTORY = '季节朴素缺少原点前 7 日实到'
SKIP_MOVING_HISTORY = '近期/季节移动缺少近期或同星期历史实到'


class ReplayError(ValueError):
    """回放证据不足或口径不满足时抛出，触发整批失败。"""


@dataclass(frozen=True)
class Version:
    id: int | None
    batch_id: int | None
    day: int
    route: str
    passengers: int | None
    seats: int | None
    posted_at: datetime


# ---------- 批次校验与冲突合并 ----------

def _parse_day(raw):
    try:
        return date.fromisoformat(raw).toordinal()
    except (TypeError, ValueError):
        raise ValueError('运营日期须为有效日期，格式为 YYYY-MM-DD') from None


def _parse_posted(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('入账时刻不能为空，格式为 YYYY-MM-DD HH:MM:SS')
    text = raw.strip().replace('T', ' ')
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError('入账时刻格式不正确，应为 YYYY-MM-DD HH:MM:SS') from None
    if stamp.tzinfo is not None:
        raise ValueError('入账时刻请使用北京时间本地时间，不要带时区偏移')
    return stamp.replace(microsecond=0)


def _bounded_int(value, field):
    if type(value) is not int or not field['min'] <= value <= field['max']:
        raise ValueError(f'{field["label"]}须为 {field["min"]} 至 {field["max"]} 之间的整数')
    return value


def parse_batch_rows(rows):
    """把补报 CSV 解析出的字典行转为 Version（id/batch_id 留空，入库时补）。

    批内同一（运营日、线路、入账时刻）出现两条即拒收；
    与库内已入账版本同刻且人数不一致也拒收，由 service 层传入 existing 再判。
    """
    if not rows:
        raise ValueError('补报批次至少需要一行数据')
    versions, moments = [], {}
    for index, row in enumerate(rows, 1):
        try:
            day = _parse_day(row.get('service_date'))
            raw_route = row.get('route')
            route = raw_route.strip() if isinstance(raw_route, str) else ''
            if not route or not route.isprintable() or len(route) > ROW_FIELDS['route']['max_length']:
                raise ValueError('接驳线路须为 1 至 80 字符的有效名称')
            posted_at = _parse_posted(row.get('posted_at'))
            passengers = row.get('passengers')
            seats = row.get('scheduled_seats')
            if passengers is None and seats is None:
                raise ValueError('实到人数和已排正班座位至少填一项')
            if passengers is not None:
                passengers = _bounded_int(passengers, ROW_FIELDS['passengers'])
            if seats is not None:
                seats = _bounded_int(seats, ROW_FIELDS['scheduled_seats'])
            key = (day, route, posted_at)
            if key in moments:
                raise ValueError(
                    f'{date.fromordinal(day).isoformat()} {route} 在 {posted_at:%Y-%m-%d %H:%M:%S} '
                    '存在两条同刻记录，无法确定版本')
            moments[key] = passengers
            versions.append(Version(None, None, day, route, passengers, seats, posted_at))
        except ValueError as error:
            raise ValueError(f'补报第 {index} 条记录 · {error}') from None
    return versions


def reject_conflicts(versions, existing):
    """与已入账版本对照。existing: {(day, route, posted_at): passengers|None}。"""
    for version in versions:
        key = (version.day, version.route, version.posted_at)
        if key in existing and version.passengers != existing[key]:
            stamp = key[2]
            raise ValueError(
                f'{date.fromordinal(version.day).isoformat()} {version.route} 在 '
                f'{stamp:%Y-%m-%d %H:%M:%S} 已入账 {existing[key]} 人，本批报 {version.passengers} 人，'
                '同刻人数冲突，整批拒收')
    return versions


# ---------- 时点视图 ----------

def effective_cutoff(day, cutoff):
    """往日（运营日早于目标晚）的可见截点：核账截点与当天 18:00 取较早者。"""
    eighteen = datetime.combine(date.fromordinal(day), DAY_CUTOFF)
    return min(cutoff, eighteen)


def as_of_view(versions, cutoff, target_day=None):
    """按核账截点生成 (运营日, 线路) -> 最新可见版本视图。

    - 往日数据只可见 min(核账截点, 当天18:00) 前的版本（18 点后补录不进预测）。
    - 目标晚数据只受核账截点约束（目标晚正班座位在 18 点后登记仍可用于回放）。
    - 实到与正班座位各自取截点前携带该字段的最新版；缺测保持缺测，绝不以 0 占位。
    """
    view = {}
    for version in versions:
        limit = cutoff if target_day is not None and version.day >= target_day \
            else effective_cutoff(version.day, cutoff)
        if version.posted_at > limit:
            continue
        slot = view.setdefault((version.day, version.route), {})
        if version.passengers is not None:
            current = slot.get('actual')
            if current is None or version.posted_at > current.posted_at:
                slot['actual'] = version
        elif 'actual' not in slot or version.posted_at > slot['actual'].posted_at:
            # 留空版本也占位，标记“有入账但实到缺测”，区别于整日无版本
            slot['actual'] = version
        if version.seats is not None:
            current = slot.get('seats')
            if current is None or version.posted_at > current.posted_at:
                slot['seats'] = version
    return view


# ---------- 回放参数与预测 ----------

def _settings(params):
    if set(params) - set(PARAMETERS):
        raise ReplayError('存在未知回放参数，请核对回放面板')
    settings = {}
    for key, field in PARAMETERS.items():
        if key == 'forecast_date':
            continue
        value = params.get(key, field['default'])
        settings[key] = _bounded_int(value, field)
    return settings


def _predict(history, origin, settings, model):
    if history.get(origin - 7) is None:
        return None
    if model == NAIVE:
        return Fraction(history[origin - 7])
    recent = [history.get(origin - lag) for lag in range(1, settings['recent_days'] + 1)]
    seasonal = [history.get(origin - week * 7) for week in range(1, settings['seasonal_weeks'] + 1)]
    if None in recent or None in seasonal:
        return None
    weight = Fraction(settings['recent_weight_pct'], 100)
    return (weight * Fraction(sum(recent), len(recent))
            + (1 - weight) * Fraction(sum(seasonal), len(seasonal)))


def _decimal(value):
    return None if value is None else float(round(value, 2))


def _version_ref(version):
    if version is None:
        return None
    return {'record_id': version.id, 'batch_id': version.batch_id,
            'posted_at': version.posted_at.isoformat(timespec='seconds'),
            'passengers': version.passengers}


@dataclass
class Fold:
    stage: str
    route: str
    origin: int
    used: bool
    skip_reason: str | None
    actual: int | None
    actual_ref: dict | None
    naive: Fraction | None
    moving: Fraction | None

    def as_row(self):
        return [self.stage, date.fromordinal(self.origin).isoformat(), self.route,
                '—' if self.actual is None else self.actual,
                self.actual_ref['posted_at'] if self.actual_ref else '—',
                self.actual_ref['batch_id'] if self.actual_ref else '—',
                self.actual_ref['record_id'] if self.actual_ref else '—',
                '是' if self.used else '否', self.skip_reason or '',
                _decimal(self.naive) if self.naive is not None else '—',
                _decimal(self.moving) if self.moving is not None else '—']


def _build_fold(stage, route, origin, history, slot, settings, selected_model=None):
    actual_version = slot.get('actual') if slot else None
    if actual_version is None:
        reason = SKIP_NO_VERSION
        actual = None
    elif actual_version.passengers is None:
        reason = SKIP_NULL_ACTUAL
        actual = None
    else:
        reason = None
        actual = actual_version.passengers
    naive = _predict(history, origin, settings, NAIVE)
    moving = _predict(history, origin, settings, MOVING)
    model_reasons = []
    if naive is None:
        model_reasons.append(SKIP_NAIVE_HISTORY)
    if moving is None:
        model_reasons.append(SKIP_MOVING_HISTORY)
    if actual is None:
        used = False
    elif stage == '选模':
        used = naive is not None and moving is not None
        if not used and reason is None:
            reason = '；'.join(model_reasons)
    else:
        chosen = naive if selected_model == NAIVE else moving
        used = chosen is not None
        if not used:
            reason = SKIP_NAIVE_HISTORY if selected_model == NAIVE else SKIP_MOVING_HISTORY
    return Fold(stage, route, origin, used, reason, actual,
                _version_ref(actual_version), naive, moving), actual


def _require_origins(route, stage, count, days):
    minimum = max(4, (days + 1) // 2)
    if count < minimum:
        raise ReplayError(
            f'{route}的{stage}仅有 {count}/{days} 个有效原点，至少需要 {minimum} 个，证据不足，本次回放整批失败。'
            '旧回放方案不受影响，仍可查询。')


@dataclass
class Assignment:
    route: str
    model: str
    forecast: Fraction
    buffer: int
    scheduled: int
    vehicles: int = 0

    @property
    def point_seats(self):
        return ceil(self.forecast)

    @property
    def target_seats(self):
        return self.point_seats + self.buffer

    def capacity(self, seats_per_vehicle):
        return self.scheduled + self.vehicles * seats_per_vehicle

    def remaining(self, seats_per_vehicle):
        return max(0, self.target_seats - self.capacity(seats_per_vehicle))


def _assign(plans, vehicle_count, seats_per_vehicle):
    def gain(plan):
        point_gap = max(0, plan.point_seats - plan.capacity(seats_per_vehicle))
        return min(seats_per_vehicle, plan.remaining(seats_per_vehicle)), min(seats_per_vehicle, point_gap)

    for _ in range(vehicle_count):
        best = max(plans, key=gain)
        if best.remaining(seats_per_vehicle) == 0:
            break
        best.vehicles += 1


def run_replay(versions, target_day, cutoff_at, params):
    """返回回放结果（原始表结构）。证据不足抛 ReplayError。

    target_day: 运营日 ordinal；cutoff_at: naive datetime（核账截点）。
    """
    if not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is not None:
        raise ReplayError('核账截点须为不带时区的北京时间本地时间')
    settings = _settings(params)
    view = as_of_view(versions, cutoff_at, target_day)
    target_label = date.fromordinal(target_day).isoformat()

    roster = {}
    for (day, route), slot in view.items():
        if day == target_day and slot.get('seats') is not None:
            roster[route] = slot['seats'].seats
    if not roster:
        raise ReplayError(f'核账截点前缺少 {target_label} 的末班正班座位版本，无法回放备车方案')

    calibration_start = target_day - settings['calibration_days']
    comparison_start = calibration_start - settings['comparison_days']
    if comparison_start < 1:
        raise ReplayError('目标运营日过早，无法容纳选模回测与缓冲校准窗口')
    comparison_origins = range(comparison_start, calibration_start)
    calibration_origins = range(calibration_start, target_day)

    folds, comparison, calibration, plans = [], [], [], []
    for route in sorted(roster):
        if roster[route] is None:
            raise ReplayError(f'{target_label} 的{route}缺少已排正班座位版本，缺班请补报 0')
        history = {}
        for (day, row_route), slot in view.items():
            actual = slot.get('actual')
            if row_route == route and day < target_day and actual is not None and actual.passengers is not None:
                history[day] = actual.passengers  # 缺测的日不写入，绝不以 0 占位

        naive_errors, moving_errors = [], []
        for origin in comparison_origins:
            fold, actual = _build_fold('选模', route, origin, history,
                                       view.get((origin, route)), settings)
            folds.append(fold)
            if fold.used:
                naive_errors.append(abs(actual - fold.naive))
                moving_errors.append(abs(actual - fold.moving))
        count = len(naive_errors)
        _require_origins(route, '选模回测', count, len(comparison_origins))
        naive_mae, moving_mae = sum(naive_errors) / count, sum(moving_errors) / count
        model = MOVING if moving_mae < naive_mae else NAIVE
        comparison.append([route, count, len(comparison_origins) - count,
                           _decimal(naive_mae), _decimal(moving_mae), model])

        observed = []
        for origin in calibration_origins:
            fold, actual = _build_fold('校准', route, origin, history,
                                       view.get((origin, route)), settings, model)
            folds.append(fold)
            if fold.used:
                predicted = fold.naive if model == NAIVE else fold.moving
                observed.append((actual, predicted))
        count = len(observed)
        _require_origins(route, '缓冲校准', count, len(calibration_origins))
        losses = sorted(max(0, actual - predicted) for actual, predicted in observed)
        rank = (settings['buffer_percentile'] * count + 99) // 100
        buffer = ceil(losses[rank - 1])
        point_covered = sum(actual <= ceil(predicted) for actual, predicted in observed)
        buffered_covered = sum(actual <= ceil(predicted) + buffer for actual, predicted in observed)
        calibration.append([route, model, count, len(calibration_origins) - count, buffer,
                            round(100 * point_covered / count, 1),
                            round(100 * buffered_covered / count, 1)])

        forecast = _predict(history, target_day, settings, model)
        if forecast is None:
            raise ReplayError(f'{route}缺少目标日{model}所需的历史实到版本，证据不足，本次回放整批失败')
        plans.append(Assignment(route, model, forecast, buffer, roster[route]))

    reserve_seats = settings['reserve_seats']
    reserve_vehicles = settings['reserve_vehicles']
    _assign(plans, reserve_vehicles, reserve_seats)
    assigned = sum(plan.vehicles for plan in plans)
    shortage = sum(plan.remaining(reserve_seats) for plan in plans)
    target = sum(plan.target_seats for plan in plans)
    base_covered = sum(min(plan.scheduled, plan.target_seats) for plan in plans)
    action = f'仍缺 {shortage} 席，请夜班值班长协调支援。' if shortage else '缓冲后需求已覆盖，请夜班值班长确认派车。'
    departures = [[plan.route, plan.model, _decimal(plan.forecast), plan.point_seats, plan.buffer,
                   plan.target_seats, plan.scheduled, plan.vehicles,
                   plan.capacity(reserve_seats), plan.remaining(reserve_seats)] for plan in plans]
    pre_gaps = sorted(((plan.route, max(0, plan.target_seats - plan.scheduled)) for plan in plans),
                      key=lambda item: (-item[1], item[0]))

    snapshot = []
    for (day, route) in sorted(view):
        slot = view[(day, route)]
        actual, seats = slot.get('actual'), slot.get('seats')
        snapshot.append({
            'service_date': date.fromordinal(day).isoformat(), 'route': route,
            'passengers': actual.passengers if actual else None,
            'scheduled_seats': seats.seats if seats else None,
            'actual_record_id': actual.id if actual else None,
            'actual_batch_id': actual.batch_id if actual else None,
            'actual_posted_at': actual.posted_at.isoformat(timespec='seconds') if actual else None,
            'seats_record_id': seats.id if seats else None,
            'seats_posted_at': seats.posted_at.isoformat(timespec='seconds') if seats else None,
        })

    return {
        'summary': (f'{target_label} 末班回放：{len(plans)} 条线路共需 {target} 席，'
                    f'建议加派 {assigned} 辆备车。{action}'),
        'metrics': [
            {'label': '点预测席位', 'value': sum(plan.point_seats for plan in plans), 'detail': '逐线路对点预测人数向上取整'},
            {'label': '缓冲席位', 'value': sum(plan.buffer for plan in plans),
             'detail': f'取各线路低估误差的第 {settings["buffer_percentile"]} 百分位'},
            {'label': '正班覆盖席位', 'value': base_covered, 'detail': '逐线路以该线目标席位为上限'},
            {'label': '已分配备车', 'value': assigned, 'detail': f'共享上限 {reserve_vehicles} 辆，每辆 {reserve_seats} 席'},
            {'label': '未覆盖席位', 'value': shortage, 'detail': '分配备车后仍需要协调的座位'},
        ],
        'tables': [
            {'title': '末班线路席位与备车分配',
             'columns': ['线路', '所用模型', '点预测（人）', '点预测席位', '缓冲席位', '目标席位',
                         '正班座位', '备车（辆）', '合计可用座位', '未覆盖席位'],
             'rows': departures},
            {'title': '选模窗口 · 一步滚动回测',
             'columns': ['线路', '共同有效原点', '跳过原点', '季节朴素 MAE（人）', '近期/季节移动 MAE（人）', '所用模型'],
             'rows': comparison},
            {'title': '校准窗口 · 低估缓冲与历史覆盖',
             'columns': ['线路', '所用模型', '有效原点', '跳过原点', '缓冲席位', '点预测覆盖率（%）', '缓冲后覆盖率（%）'],
             'rows': calibration},
            {'title': '回放取数版本与跳过明细（每折可追溯）',
             'columns': ['阶段', '原点日', '线路', '实到（版本值）', '入账时刻', '批次ID', '记录ID',
                         '是否参与', '跳过原因', '季节朴素预测', '移动模型预测'],
             'rows': [fold.as_row() for fold in folds]},
        ],
        'series_title': '派备车前的线路缺口（席）',
        'series': [{'label': route, 'value': gap} for route, gap in pre_gaps if gap > 0],
        'notes': [
            f'核账截点 {cutoff_at:%Y-%m-%d %H:%M:%S}；各原点只可见 min(核账截点, 当天18:00) 前入账的最新版本。',
            f'选模窗口 {date.fromordinal(comparison_start).isoformat()} 至 '
            f'{date.fromordinal(calibration_start - 1).isoformat()}；'
            f'校准窗口 {date.fromordinal(calibration_start).isoformat()} 至 '
            f'{date.fromordinal(target_day - 1).isoformat()}。',
            '缺测（无版本或实到留空）不按零计算；封存后补录的版本不影响本回放，更换核账截点才会另算新方案。',
            f'余留共享备车 {reserve_vehicles - assigned} 辆。各线路正班座位专用，规划按同一末班窗口与同型备车计算。',
        ],
        'snapshot': snapshot,
    }
