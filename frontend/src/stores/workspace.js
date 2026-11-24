import {request, saveFile} from '../api/http.js';
import {session} from './session.js';

const call = (path, body) => request(path, {token:session.token, body});
export const workspace = {
  spec: null,
  datasets: [],
  runs: [],
  async init() { this.spec = await request('/spec'); },
  async refresh() {
    const calls = [call('/datasets'), call('/runs'), call('/arrival-batches'), call('/replays')];
    [this.datasets, this.runs, this.batches, this.replays] = await Promise.all(calls);
  },
  getDataset: (id, offset = 0) => call(`/datasets/${id}?offset=${offset}`),
  getRun: id => call(`/runs/${id}`),
  upload: data => call('/datasets', data),
  start: data => call('/runs', data),
  review: (id, data) => call(`/runs/${id}/review`, data),
  audit: () => call('/audit'),
  schedules: () => call('/schedules'),
  deliveries: () => call('/deliveries'),
  schedule: data => call('/schedules', data),
  disable: id => call(`/schedules/${id}/disable`, {}),
  arrivalRecords: () => call('/arrival-records'),
  uploadArrival: data => call('/arrival-batches', data),
  startReplay: data => call('/replays', data),
  getReplay: id => call(`/replays/${id}`),
  download: (path, filename) => saveFile(path, session.token, filename),
};
