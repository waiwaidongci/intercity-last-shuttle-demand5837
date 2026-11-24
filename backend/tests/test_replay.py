import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.domain.replay import (  # noqa: E402
    NAIVE,
    SKIP_NO_VERSION,
    SKIP_NULL_ACTUAL,
    Version,
    ReplayError,
    as_of_view,
    effective_cutoff,
    parse_batch_rows,
    reject_conflicts,
    run_replay,
)
from src.domain.replay_flow import (  # noqa: E402
    decide_batch,
    normalize_replay_params,
    parse_cutoff,
    parse_target_day,
    seal_replay,
)

TARGET = date(2026, 11, 24).toordinal()
CUTOFF = datetime(2026, 11, 24, 18, 0)
ROUTES = ['东港线', '临河线', '青溪线']
SETTINGS = {
    'comparison_days': 14, 'calibration_days': 14, 'recent_days': 7,
    'seasonal_weeks': 3, 'recent_weight_pct': 40, 'buffer_percentile': 90,
    'reserve_vehicles': 3, 'reserve_seats': 18,
}


def make_versions(routes=ROUTES, span=50, null_days=None, post_cutoff_day=None, conflict=False):
    """构造每条线路 span 天的 17:50 入账版本；目标日只给正班座位。"""
    null_days = null_days or set()
    versions = []
    start = TARGET - span
    record_id = 1
    for route_index, route in enumerate(routes):
        for offset in range(span + 1):
            day = start + offset
            day_date = date.fromordinal(day)
            if day == TARGET:
                versions.append(Version(record_id, 1, day, route, None, 30 + route_index,
                                        datetime.combine(day_date, datetime.min.time()).replace(hour=17, minute=55)))
                record_id += 1
                continue
            passengers = None if day in null_days else 20 + (offset * 3 + route_index * 7) % 40
            posted = datetime.combine(day_date, datetime.min.time()).replace(hour=17, minute=50)
            if post_cutoff_day == day:
                posted = datetime.combine(day_date, datetime.min.time()).replace(hour=20, minute=0)
            versions.append(Version(record_id, 1, day, route, passengers, None, posted))
            record_id += 1
            if conflict and post_cutoff_day == day:
                versions.append(Version(record_id, 2, day, route, passengers + 5, None,
                                        datetime.combine(day_date, datetime.min.time()).replace(hour=21, minute=0)))
                record_id += 1
    return versions


class BatchParsingTests(unittest.TestCase):
    def test_parse_requires_posted_at(self):
        with self.assertRaises(ValueError):
            parse_batch_rows([{'service_date': '2026-11-23', 'route': '东港线',
                               'posted_at': '', 'passengers': 10}])

    def test_parse_rejects_aware_timestamp(self):
        with self.assertRaises(ValueError):
            parse_batch_rows([{'service_date': '2026-11-23', 'route': '东港线',
                               'posted_at': '2026-11-23T17:50:00+08:00', 'passengers': 10}])

    def test_duplicate_moment_within_batch_rejected(self):
        rows = [
            {'service_date': '2026-11-23', 'route': '东港线', 'posted_at': '2026-11-23 17:50:00',
             'passengers': 10},
            {'service_date': '2026-11-23', 'route': '东港线', 'posted_at': '2026-11-23 17:50:00',
             'passengers': 12},
        ]
        with self.assertRaisesRegex(ValueError, '同刻'):
            parse_batch_rows(rows)

    def test_conflict_with_existing_rejects_whole_batch(self):
        versions = parse_batch_rows([
            {'service_date': '2026-11-23', 'route': '东港线', 'posted_at': '2026-11-23 17:50:00',
             'passengers': 12}])
        existing = {(TARGET - 1, '东港线', datetime(2026, 11, 23, 17, 50)): 10}
        with self.assertRaisesRegex(ValueError, '同刻人数冲突，整批拒收'):
            reject_conflicts(versions, existing)

    def test_same_moment_same_value_is_allowed(self):
        versions = parse_batch_rows([
            {'service_date': '2026-11-23', 'route': '东港线', 'posted_at': '2026-11-23 17:50:00',
             'passengers': 10}])
        existing = {(TARGET - 1, '东港线', datetime(2026, 11, 23, 17, 50)): 10}
        self.assertEqual(reject_conflicts(versions, existing), versions)


class AsOfViewTests(unittest.TestCase):
    def test_effective_cutoff_uses_18h_when_cutoff_later(self):
        day = TARGET - 1
        self.assertEqual(effective_cutoff(day, datetime(2026, 11, 30, 0, 0)),
                         datetime(2026, 11, 23, 18, 0))

    def test_effective_cutoff_uses_accounting_when_earlier(self):
        day = TARGET - 1
        self.assertEqual(effective_cutoff(day, datetime(2026, 11, 23, 12, 0)),
                         datetime(2026, 11, 23, 12, 0))

    def test_latest_visible_version_wins_and_late_posting_hidden(self):
        day = TARGET - 2
        versions = [
            Version(1, 1, day, '东港线', 10, None, datetime(2026, 11, 22, 17, 50)),
            Version(2, 2, day, '东港线', 14, None, datetime(2026, 11, 22, 17, 55)),
            Version(3, 3, day, '东港线', 99, None, datetime(2026, 11, 22, 20, 0)),
        ]
        view = as_of_view(versions, CUTOFF)
        self.assertEqual(view[(day, '东港线')]['actual'].passengers, 14)
        self.assertEqual(view[(day, '东港线')]['actual'].id, 2)

    def test_null_version_is_kept_as_missing_not_zero(self):
        day = TARGET - 3
        versions = [Version(1, 1, day, '东港线', None, None, datetime(2026, 11, 20, 17, 50))]
        view = as_of_view(versions, CUTOFF)
        # 留空版本占位但实到仍为 None，计算时按缺测而非零
        self.assertIsNone(view[(day, '东港线')]['actual'].passengers)

    def test_actual_and_seats_can_come_from_different_versions(self):
        day = TARGET - 4
        versions = [
            Version(1, 1, day, '东港线', 11, None, datetime(2026, 11, 20, 17, 50)),
            Version(2, 2, day, '东港线', None, 33, datetime(2026, 11, 20, 21, 0)),
        ]
        # 21:00 超过当天 18:00 截点，座位版本不可见
        view = as_of_view(versions, CUTOFF)
        self.assertEqual(view[(day, '东港线')]['actual'].passengers, 11)
        self.assertNotIn('seats', view[(day, '东港线')])


class ReplayTests(unittest.TestCase):
    def test_successful_replay_contains_tables_lineage_and_snapshot(self):
        result = run_replay(make_versions(), TARGET, CUTOFF, SETTINGS)
        titles = [table['title'] for table in result['tables']]
        self.assertIn('回放取数版本与跳过明细（每折可追溯）', titles)
        lineage = result['tables'][-1]
        self.assertTrue(any(row[7] == '是' for row in lineage['rows']))
        # 血缘每行都带记录 ID / 批次 ID / 入账时刻列
        for row in lineage['rows']:
            self.assertEqual(len(row), len(lineage['columns']))
        self.assertTrue(result['snapshot'])
        # 三线路均产出分配方案
        departures = result['tables'][0]['rows']
        self.assertEqual(len(departures), 3)

    def test_sealed_late_records_do_not_change_result(self):
        # 封存后（当天 18 点后）补录的版本对 18:00 截点不可见，结果与快照完全不变
        late_day = TARGET - 25
        versions = make_versions()
        base_day = datetime.combine(date.fromordinal(late_day), datetime.min.time())
        for route in ROUTES:
            versions.append(Version(9100, 9, late_day, route, 99, None, base_day.replace(hour=21)))
        before = run_replay(make_versions(), TARGET, CUTOFF, SETTINGS)
        after = run_replay(versions, TARGET, CUTOFF, SETTINGS)
        self.assertEqual(before['tables'][0]['rows'], after['tables'][0]['rows'])
        self.assertEqual(before['snapshot'], after['snapshot'])

    def test_earlier_cutoff_creates_distinct_picture(self):
        versions = make_versions()
        day = TARGET - 10
        base_day = datetime.combine(date.fromordinal(day), datetime.min.time())
        # 同一运营日再补两版：17:55 报 88（18 点截点前可见），19:30 报 99（封存后不可见）
        versions.append(Version(9001, 7, day, '东港线', 88, None, base_day.replace(hour=17, minute=55)))
        versions.append(Version(9002, 8, day, '东港线', 99, None, base_day.replace(hour=19, minute=30)))

        def snapshot_at(cutoff):
            view = as_of_view(versions, cutoff, TARGET)
            return view[(day, '东港线')]['actual']

        # 18:00 截点可见 17:55 修订版（取 88）；15:00 截点下当日所有版本均不可见
        self.assertEqual(snapshot_at(CUTOFF).passengers, 88)
        self.assertNotIn((day, '东港线'),
                         as_of_view(versions, base_day.replace(hour=15), TARGET))
        # 19:30 的封存后补录在任何截点都不可见
        self.assertNotEqual(as_of_view(versions, CUTOFF, TARGET)[(day, '东港线')]
                            ['actual'].passengers, 99)

    def test_missing_history_causes_whole_batch_failure(self):
        # 清空一条线路大部分历史：证据不足，整批失败且无任何方案产出
        versions = make_versions(null_days={TARGET - d for d in range(1, 20)})
        with self.assertRaisesRegex(ReplayError, '整批失败'):
            run_replay(versions, TARGET, CUTOFF, SETTINGS)

    def test_skip_reason_recorded_for_missing_actual(self):
        # 选模窗口内一条实到留空的入账版本：门槛被击穿 -> 整批失败，且无方案产出
        versions = make_versions()
        null_day = TARGET - 22
        versions = [v for v in versions if not (v.day == null_day and v.route == '东港线')]
        versions.append(Version(9300, 11, null_day, '东港线', None, None,
                                datetime.combine(date.fromordinal(null_day),
                                                 datetime.min.time()).replace(hour=17, minute=50)))
        with self.assertRaisesRegex(ReplayError, '选模回测.*整批失败'):
            run_replay(versions, TARGET, CUTOFF, SETTINGS)

    def test_skip_reason_visible_when_window_still_sufficient(self):
        # 选模窗口内留空一折，其余证据充足：回放成功且血缘表记录留空原因
        settings = {**SETTINGS, 'calibration_days': 20, 'comparison_days': 20}
        versions = make_versions(span=70)
        null_day = TARGET - 25
        versions = [v for v in versions if not (v.day == null_day and v.route == '东港线')]
        versions.append(Version(9301, 11, null_day, '东港线', None, None,
                                datetime.combine(date.fromordinal(null_day),
                                                 datetime.min.time()).replace(hour=17, minute=50)))
        result = run_replay(versions, TARGET, CUTOFF, settings)
        lineage = result['tables'][-1]['rows']
        target_rows = [row for row in lineage
                       if row[1] == date.fromordinal(null_day).isoformat() and row[0] == '选模'
                       and row[2] == '东港线']
        self.assertTrue(target_rows)
        for row in target_rows:
            self.assertEqual(row[7], '否')
            self.assertIn(SKIP_NULL_ACTUAL, row[8])

    def test_no_version_at_all_uses_no_version_reason(self):
        # 选模窗口中段的一条线路整日无版本，其余证据仍充足
        versions = [v for v in make_versions()
                    if not (v.day == TARGET - 20 and v.route == '东港线')]
        result = run_replay(versions, TARGET, CUTOFF, SETTINGS)
        lineage = result['tables'][-1]['rows']
        row = next(r for r in lineage
                   if r[1] == date.fromordinal(TARGET - 20).isoformat() and r[2] == '东港线')
        self.assertEqual(row[7], '否')
        self.assertEqual(row[8], SKIP_NO_VERSION)

    def test_target_day_without_seats_versions_fails(self):
        versions = [v for v in make_versions() if not (v.day == TARGET and v.seats is not None)]
        with self.assertRaisesRegex(ReplayError, '正班座位版本'):
            run_replay(versions, TARGET, CUTOFF, SETTINGS)

    def test_unknown_param_rejected(self):
        with self.assertRaises(ReplayError):
            run_replay(make_versions(), TARGET, CUTOFF, {**SETTINGS, 'bogus': 1})

    def test_naive_tie_prefers_seasonal_naive(self):
        # 构造完全周期化数据，使两模型预测相同，选模应落到季节朴素
        versions = []
        start = TARGET - 50
        rid = 1
        for route in ROUTES:
            for offset in range(51):
                day = start + offset
                stamp = datetime.combine(date.fromordinal(day), datetime.min.time()).replace(hour=17, minute=50)
                if day == TARGET:
                    versions.append(Version(rid, 1, day, route, None, 30, stamp.replace(hour=17, minute=55)))
                else:
                    versions.append(Version(rid, 1, day, route, 25 + (day % 7), None, stamp))
                rid += 1
        result = run_replay(versions, TARGET, CUTOFF, SETTINGS)
        comparison = result['tables'][1]['rows']
        self.assertTrue(all(row[5] == NAIVE for row in comparison))


class ReplayFlowTests(unittest.TestCase):
    def _row(self, posted, passengers=10, route='东港线', day='2026-11-23'):
        return {'service_date': day, 'route': route, 'posted_at': posted,
                'passengers': passengers, 'scheduled_seats': None}

    def test_decide_batch_accepts_clean_rows(self):
        decision, reason, versions = decide_batch([self._row('2026-11-23 17:50:00')], {})
        self.assertEqual(decision, 'accept')
        self.assertIsNone(reason)
        self.assertEqual(len(versions), 1)

    def test_decide_batch_rejects_on_existing_conflict_and_keeps_reason(self):
        existing = {(date(2026, 11, 23).toordinal(), '东港线',
                     datetime(2026, 11, 23, 17, 50)): 10}
        decision, reason, versions = decide_batch([self._row('2026-11-23 17:50:00', passengers=12)],
                                                   existing)
        self.assertEqual(decision, 'reject')
        self.assertIn('整批拒收', reason)
        self.assertEqual(len(versions), 1)  # 解析结果仍可用于登记拒收批次行数

    def test_decide_batch_accepts_matching_repost(self):
        existing = {(date(2026, 11, 23).toordinal(), '东港线',
                     datetime(2026, 11, 23, 17, 50)): 10}
        decision, _, _ = decide_batch([self._row('2026-11-23 17:50:00', passengers=10)], existing)
        self.assertEqual(decision, 'accept')

    def test_seal_replay_success_returns_frontend_shape(self):
        result = seal_replay(make_versions(), TARGET, CUTOFF, SETTINGS)
        self.assertIn('headline', result)
        self.assertTrue(result['tables'])
        self.assertIn('snapshot', result)

    def test_seal_replay_raises_on_insufficient_evidence(self):
        versions = make_versions(null_days={TARGET - d for d in range(1, 20)})
        with self.assertRaises(ReplayError):
            seal_replay(versions, TARGET, CUTOFF, SETTINGS)

    def test_retry_after_supplementing_evidence_succeeds(self):
        # 先证据不足整批失败；补齐缺测日后同截点回放成功，且不把缺测当零
        sparse = make_versions(null_days={TARGET - d for d in range(1, 20)})
        with self.assertRaises(ReplayError):
            seal_replay(sparse, TARGET, CUTOFF, SETTINGS)
        complete = make_versions()
        result = seal_replay(complete, TARGET, CUTOFF, SETTINGS)
        self.assertEqual(len(result['tables'][0]['rows']), 3)

    def test_normalize_params_fills_defaults_and_rejects_unknown(self):
        settings = normalize_replay_params({})
        self.assertEqual(settings['reserve_vehicles'], 3)
        self.assertEqual(settings['buffer_percentile'], 90)
        with self.assertRaises(ValueError):
            normalize_replay_params({'bogus': 1})
        with self.assertRaises(ValueError):
            normalize_replay_params({'reserve_vehicles': 999})

    def test_parse_target_and_cutoff(self):
        self.assertEqual(parse_target_day('2026-11-24').toordinal(), TARGET)
        with self.assertRaises(ValueError):
            parse_target_day('11/24/2026')
        cutoff = parse_cutoff('2026-11-24T18:30')
        self.assertEqual((cutoff.hour, cutoff.minute, cutoff.second), (18, 30, 0))
        self.assertEqual(parse_cutoff('2026-11-24 18:30:00'), cutoff)
        with self.assertRaises(ValueError):
            parse_cutoff('2026-11-24T18:30+08:00')


if __name__ == '__main__':
    unittest.main(verbosity=2)
