import {feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {plot} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {REPLAY_STATUS} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

function replayForm(spec, error) {
  const submit = button('封存回放方案', {type:'submit', class:'primary', 'data-testid':'start-replay'});
  const replayParams = spec.params.filter(param => param.key !== 'forecast_date');
  return el('form', {class:'fields', 'data-testid':'replay-form',
    onSubmit: handler(submit, error, async event => {
      const data = new FormData(event.target);
      const payload = {
        name: data.get('name'),
        target_day: data.get('target_day'),
        cutoff_at: data.get('cutoff_at'),
        params: Object.fromEntries(replayParams.map(param => [param.key, data.get(param.key)])),
      };
      try {
        const item = await workspace.startReplay(payload);
        location.hash = `#replay/${item.id}`;
      } catch (err) {
        // 整批失败已在后端登记，刷新清单展示失败记录，错误由 feedback 呈现
        await refresh();
        throw err;
      }
    })},
    field({key:'name', label:'回放名称', type:'string', max_length:120}, `备车方案回放`),
    el('div', {class:'form-grid'},
      field({key:'target_day', label:'目标晚（运营日）', type:'date'}, spec.params.find(p => p.key === 'forecast_date')?.default),
      el('label', {class:'field', for:'field-cutoff_at'}, el('span', {}, '核账截点（北京时间）'),
        el('input', {id:'field-cutoff_at', name:'cutoff_at', type:'datetime-local', required:true}))),
    el('div', {class:'form-grid'}, replayParams.map(param => field(param))),
    el('div', {class:'actions'}, submit));
}

function replayResult(job) {
  const result = job.result;
  if (!result) {
    return panel(job.name, pill(REPLAY_STATUS[job.status] || job.status, job.status),
      p(job.error || '证据不足，本次回放整批失败；旧回放方案仍可在下方列表查询。', 'feedback'));
  }
  return el('section', {class:'analysis', 'data-testid':'replay-complete'},
    el('div', {class:'result-head'},
      p(job.name, 'eyeline'), h(2, result.headline), p(result.note, 'intro-copy'),
      el('div', {class:'actions'}, pill(REPLAY_STATUS[job.status] || job.status, job.status),
        p(`重跑次数 ${job.reruns} · 封存于 ${date(job.created_at)}`, 'subtle'))),
    metrics(result.metrics),
    result.charts.map(plot),
    result.tables.map(item => panel(item.title, table(item.columns, item.rows, {caption:item.title, pageSize:10}))));
}

export async function replayPage(route, refresh) {
  const {spec, replays} = workspace;
  const error = feedback();
  const selectedId = route.id || replays?.[0]?.id;
  const job = selectedId ? await workspace.getReplay(selectedId) : null;

  const list = table([
    {label:'回放', render:item => link(item.name, `#replay/${item.id}`)},
    {key:'target_day', label:'目标晚'},
    {key:'cutoff_at', label:'核账截点'},
    {label:'状态', render:item => pill(REPLAY_STATUS[item.status] || item.status, item.status)},
    {key:'reruns', label:'重跑次数'},
  ], replays || [], {caption:'已封存回放', empty:'还没有回放方案。'});

  const detail = job
    ? replayResult(job)
    : panel('备车方案回放', empty('选择左侧回放，或填写目标晚与核账截点封存新方案。'));

  const paramList = job && el('dl', {class:'dictionary'},
    Object.entries(job.params || {}).map(([key, val]) =>
      el('div', {}, el('dt', {}, key), el('dd', {}, value(val)))));

  return el('div', {},
    el('div', {class:'two-col'},
      el('div', {class:'wide-panel'}, detail),
      el('aside', {class:'sidebar-panel'},
        panel('回放清单', list),
        session.canWrite && panel('新建回放', replayForm(spec, error), error),
        job && panel('本次回放参数', paramList),
        panel('口径说明', el('ul', {class:'method-list'}, [
          '往日历史只取 min(核账截点, 当天18:00) 前入账的最新版本。',
          '缺测（无版本或实到留空）不按零计算。',
          '同目标晚 + 同核账截点重跑不新增方案，只增加重跑次数。',
          '封存后补录不改原结果；更换核账截点另算新方案。',
          '任一路线证据不足则整批失败，旧方案照常可查。',
        ].map(text => el('li', {}, text)))))));
}
