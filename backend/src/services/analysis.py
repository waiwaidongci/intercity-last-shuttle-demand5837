from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import ceil

from ..domain.spec import BUSINESS


NAIVE = '季节朴素'
MOVING = '近期/季节移动'
PARAMETERS = {field['key']: field for field in BUSINESS['parameters']}
ROW_FIELDS = {field['key']: field for field in BUSINESS['fields']}


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


def _day(raw, label):
    try:
        return date.fromisoformat(raw).toordinal()
    except (TypeError, ValueError):
        raise ValueError(f'{label}须为有效日期，格式为 YYYY-MM-DD') from None


def _display_day(day):
    return date.fromordinal(day).isoformat()


def _integer(value, field):
    if type(value) is not int or not field['min'] <= value <= field['max']:
        raise ValueError(f'{field["label"]}须为 {field["min"]} 至 {field["max"]} 之间的整数')
    return value


def _settings(params):
    if set(params) - PARAMETERS.keys():
        raise ValueError('存在未知运行参数，请核对运行面板')
    settings = {}
    for key, field in PARAMETERS.items():
        value = params.get(key, field['default'])
        if field['type'] == 'date':
            settings[key] = _day(value, field['label'])
        else:
            settings[key] = _integer(value, field)
    return settings


def _index_rows(rows):
    if not rows:
        raise ValueError('台账至少需要一行数据')
    history_by_route, seats_by_day = {}, {}
    seen = set()
    for index, row in enumerate(rows, 1):
        try:
            day = _day(row.get('service_date'), '运营日期')
            raw_route = row.get('route')
            route = raw_route.strip() if isinstance(raw_route, str) else ''
            if not route or not route.isprintable() or len(route) > ROW_FIELDS['route']['max_length']:
                raise ValueError('接驳线路须为 1 至 80 字符的有效名称')
            key = (day, route)
            if key in seen:
                raise ValueError(f'{_display_day(day)} 的{route}重复，同日同线路只允许一条记录')
            seen.add(key)
            passengers = row.get('passengers')
            seats = row.get('scheduled_seats')
            if passengers is None and seats is None:
                raise ValueError('实到人数和已排正班座位至少填一项')
            if passengers is not None:
                history_by_route.setdefault(route, {})[day] = _integer(passengers, ROW_FIELDS['passengers'])
            if seats is not None:
                seats = _integer(seats, ROW_FIELDS['scheduled_seats'])
            seats_by_day.setdefault(day, {})[route] = seats
        except ValueError as error:
            raise ValueError(f'台账第 {index} 条记录 · {error}') from None
    return history_by_route, seats_by_day


def validate_rows(rows):
    _index_rows(rows)


def _predict(history, origin, settings, model):
    last_week = history.get(origin - 7)
    if last_week is None:
        return None
    if model == NAIVE:
        return Fraction(last_week)
    recent = [history.get(origin - lag) for lag in range(1, settings['recent_days'] + 1)]
    seasonal = [history.get(origin - week * 7) for week in range(1, settings['seasonal_weeks'] + 1)]
    if None in recent or None in seasonal:
        return None
    weight = Fraction(settings['recent_weight_pct'], 100)
    return weight * Fraction(sum(recent), len(recent)) + (1 - weight) * Fraction(sum(seasonal), len(seasonal))


def _require_origins(route, stage, count, days):
    minimum = max(4, (days + 1) // 2)
    if count < minimum:
        raise ValueError(f'{route}的{stage}仅有 {count}/{days} 个有效原点，至少需要 {minimum} 个。请核对缺测或调整窗口。')


def _compare(route, history, origins, settings):
    naive_errors, moving_errors = [], []
    for origin in origins:
        actual = history.get(origin)
        if actual is None:
            continue
        naive = _predict(history, origin, settings, NAIVE)
        moving = _predict(history, origin, settings, MOVING)
        if naive is not None and moving is not None:
            naive_errors.append(abs(actual - naive))
            moving_errors.append(abs(actual - moving))
    count = len(naive_errors)
    _require_origins(route, '选模回测', count, len(origins))
    naive_mae, moving_mae = sum(naive_errors) / count, sum(moving_errors) / count
    model = MOVING if moving_mae < naive_mae else NAIVE
    return model, [route, count, len(origins) - count, _decimal(naive_mae), _decimal(moving_mae), model]


def _calibrate(route, model, history, origins, settings):
    observed = []
    for origin in origins:
        actual = history.get(origin)
        if actual is None:
            continue
        prediction = _predict(history, origin, settings, model)
        if prediction is not None:
            observed.append((actual, prediction))
    count = len(observed)
    _require_origins(route, '缓冲校准', count, len(origins))
    losses = sorted(max(0, actual - predicted) for actual, predicted in observed)
    rank = (settings['buffer_percentile'] * count + 99) // 100
    buffer = ceil(losses[rank - 1])
    point_covered = sum(actual <= ceil(predicted) for actual, predicted in observed)
    buffered_covered = sum(actual <= ceil(predicted) + buffer for actual, predicted in observed)
    return buffer, [
        route, model, count, len(origins) - count, buffer,
        round(100 * point_covered / count, 1), round(100 * buffered_covered / count, 1),
    ]


def _decimal(value):
    return float(round(value, 2))


def _assign(plans, vehicle_count, seats_per_vehicle):
    def gain(plan):
        point_gap = max(0, plan.point_seats - plan.capacity(seats_per_vehicle))
        return min(seats_per_vehicle, plan.remaining(seats_per_vehicle)), min(seats_per_vehicle, point_gap)

    # plans is in route order; max retains the first route for an exact tie.
    for _ in range(vehicle_count):
        best = max(plans, key=gain)
        if best.remaining(seats_per_vehicle) == 0:
            break
        best.vehicles += 1


def analyze(rows, params):
    settings = _settings(params)
    history_by_route, seats_by_day = _index_rows(rows)
    forecast_day = settings['forecast_date']
    day_label = _display_day(forecast_day)
    roster = seats_by_day.get(forecast_day)
    if not roster:
        raise ValueError(f'台账缺少 {day_label} 的末班计划，请按线路填写已排正班座位')

    calibration_start = forecast_day - settings['calibration_days']
    comparison_start = calibration_start - settings['comparison_days']
    if comparison_start < 1:
        raise ValueError('预测运营日过早，无法容纳选模回测与缓冲校准窗口')
    comparison_origins = range(comparison_start, calibration_start)
    calibration_origins = range(calibration_start, forecast_day)
    comparison, calibration, plans = [], [], []
    for route, scheduled in sorted(roster.items()):
        if scheduled is None:
            raise ValueError(f'{day_label} 的{route}缺少已排正班座位，缺班请填 0')
        history = history_by_route.get(route, {})
        model, selection_row = _compare(route, history, comparison_origins, settings)
        buffer, calibration_row = _calibrate(route, model, history, calibration_origins, settings)
        forecast = _predict(history, forecast_day, settings, model)
        if forecast is None:
            raise ValueError(f'{route}缺少目标日{model}所需的历史实到人数，请补齐后重试')
        comparison.append(selection_row)
        calibration.append(calibration_row)
        plans.append(Assignment(route, model, forecast, buffer, scheduled))

    reserve_seats = settings['reserve_seats']
    reserve_vehicles = settings['reserve_vehicles']
    _assign(plans, reserve_vehicles, reserve_seats)
    assigned = sum(plan.vehicles for plan in plans)
    shortage = sum(plan.remaining(reserve_seats) for plan in plans)
    target = sum(plan.target_seats for plan in plans)
    base_covered = sum(min(plan.scheduled, plan.target_seats) for plan in plans)
    action = f'仍缺 {shortage} 席，请夜班值班长协调支援。' if shortage else '缓冲后需求已覆盖，请夜班值班长确认派车。'
    departures = [
        [plan.route, plan.model, _decimal(plan.forecast), plan.point_seats, plan.buffer, plan.target_seats,
         plan.scheduled, plan.vehicles, plan.capacity(reserve_seats), plan.remaining(reserve_seats)]
        for plan in plans
    ]
    pre_assignment_gaps = sorted(
        ((plan.route, max(0, plan.target_seats - plan.scheduled)) for plan in plans),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        'summary': f'{day_label} 末班 {len(plans)} 条线路共需 {target} 席，建议加派 {assigned} 辆备车。{action}',
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
        ],
        'series_title': '派备车前的线路缺口（席，前 12 条）',
        'series': [{'label': route, 'value': gap} for route, gap in pre_assignment_gaps[:12] if gap > 0],
        'notes': [
            f'选模窗口为 {_display_day(comparison_start)} 至 {_display_day(calibration_start - 1)}。'
            f'校准窗口为 {_display_day(calibration_start)} 至 {_display_day(forecast_day - 1)}。',
            '各原点只使用更早的实到记录。两模型在共同有效原点比较，按未舍入的误差选模。覆盖率来自各线路的历史校准样本。',
            f'余留共享备车 {reserve_vehicles - assigned} 辆。各线路正班座位专用，规划按同一末班窗口与同型备车计算。',
        ],
    }
