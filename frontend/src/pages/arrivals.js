import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {BATCH_STATE} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const HEADER = 'service_date,route,posted_at,passengers,scheduled_seats';

export async function arrivals(route, refresh) {
  const {batches} = workspace;
  const records = await workspace.arrivalRecords();
  const error = feedback();

  const batchTable = table([
    {key:'name', label:'补报批次'},
    {label:'状态', render:row => pill(BATCH_STATE[row.state] || row.state, row.state)},
    {key:'row_count', label:'行数'},
    {label:'拒收原因', render:row => row.reject_reason || '—'},
    {label:'入账时间', render:row => date(row.created_at)},
  ], batches || [], {caption:'补报批次', empty:'还没有补报批次。'});

  const recordTable = table([
    {key:'service_date', label:'运营日'},
    {key:'route', label:'线路'},
    {label:'实到', render:row => row.passengers === null || row.passengers === undefined ? '缺测' : row.passengers},
    {key:'scheduled_seats', label:'正班座位'},
    {key:'posted_at', label:'入账时刻'},
    {key:'batch_id', label:'来源批次'},
    {key:'id', label:'记录ID'},
  ], records, {caption:'实到版本（按运营日/线路/入账时刻保留各版）', pageSize:14});

  const submit = button('校验并入账', {type:'submit', class:'primary', 'data-testid':'upload-arrival'});
  const upload = session.canWrite && el('form', {class:'fields',
    onSubmit: handler(submit, error, async event => {
      const data = new FormData(event.target);
      const file = data.get('source');
      if (!file?.size) throw new Error('请选择一个补报 CSV 文件');
      if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
      try {
        await workspace.uploadArrival({
          name: data.get('batch_name'), filename: file.name, csv_text: await file.text(),
        });
      } catch (err) {
        // 拒收批次已登记，刷新列表让值班长看到拒收原因
        await refresh();
        throw err;
      }
      await refresh();
    })},
    field({key:'batch_name', label:'批次名称', type:'string', max_length:120}),
    el('label', {class:'field', for:'arrival-source'},
      el('span', {}, '补报 CSV（含入账时刻列）'),
      el('input', {id:'arrival-source', type:'file', name:'source', accept:'.csv,text/csv', required:true})),
    submit);

  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'},
      panel('实到版本台账', p('同一运营日、线路、入账时刻只允许一个人数版本；同刻冲突整批拒收，不写入版本明细。', 'subtle'),
        batchTable, recordTable)),
    el('aside', {class:'sidebar-panel'},
      panel('补报导入', upload || empty('只读角色可查看版本与拒收记录。'), error,
        el('dl', {class:'dictionary'},
          el('div', {}, el('dt', {}, 'CSV 表头'), el('dd', {}, HEADER)),
          el('div', {}, el('dt', {}, '入账时刻'), el('dd', {}, 'YYYY-MM-DD HH:MM:SS，北京时间')),
          el('div', {}, el('dt', {}, '缺测口径'), el('dd', {}, '实到留空即缺测，不要填 0'))))));
}
