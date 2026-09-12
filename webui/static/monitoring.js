'use strict';

const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const RANGE_LABELS = new Map([[900, '最近 15 分钟'], [3600, '最近 1 小时'], [21600, '最近 6 小时'], [86400, '最近 24 小时'], [604800, '最近 7 天']]);
const RANGE_STORAGE_KEY = 'pe31625g24dira-monitor-range';
const LIVE_RESOLUTION_SECONDS = 5;
const storedRange = Number(window.localStorage.getItem(RANGE_STORAGE_KEY));
let history = []; let selectedRange = RANGE_LABELS.has(storedRange) ? storedRange : 900; let apiClient = null; let uiState = null; let latestTelemetry = null; let latestFormats = null; let rangeControlsBound = false; let hoverRatio = null; let usageRequestId = 0; let usageData = null; let selectedUsageView = 'overview';

function finite(value) { const number = Number(value); return value === null || value === undefined || !Number.isFinite(number) ? null : number; }
function collectSample(data) {
  const traffic = data.port_status?.traffic || {}; const sampled = Number(data.sampled) || Math.floor(Date.now() / 1000);
  return { timestamp: sampled - sampled % LIVE_RESOLUTION_SECONDS, cpu: finite(data.cpu?.usage_percent), memory: finite(data.memory?.usage_percent), memoryUsed: finite(data.memory?.used), rx: finite(traffic.rx_bps), tx: finite(traffic.tx_bps) };
}
function normalizeSample(sample) { return { timestamp: Number(sample.timestamp), cpu: finite(sample.cpu), memory: finite(sample.memory), memoryUsed: finite(sample.memoryUsed), rx: finite(sample.rx), tx: finite(sample.tx) }; }
function upsertSample(sample) {
  if (!Number.isFinite(sample.timestamp)) return; const last = history[history.length - 1];
  if (last && last.timestamp === sample.timestamp) history[history.length - 1] = Object.fromEntries(Object.keys(sample).map((key) => [key, sample[key] === null ? last[key] : sample[key]]));
  else if (!last || sample.timestamp > last.timestamp) history.push(sample);
  const cutoff = Math.floor(Date.now() / 1000) - selectedRange; const first = history.findIndex((item) => item.timestamp >= cutoff); if (first > 0) history.splice(0, first);
}
function niceCeiling(value) { if (!(value > 0)) return 1; const magnitude = 10 ** Math.floor(Math.log10(value)); const normalized = value / magnitude; return (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude; }
function svgElement(name, attributes = {}) { const element = document.createElementNS(SVG_NAMESPACE, name); Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value)); return element; }
function axisTime(timestamp) { const date = new Date(timestamp * 1000); return selectedRange > 86400 ? date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); }
function exactTime(timestamp) { return new Date(timestamp * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }); }

function clearChartHover() {
  hoverRatio = null;
  document.querySelectorAll('.monitor-chart').forEach((root) => { const chart = root._monitorChart; if (!chart) return; chart.line.setAttribute('visibility', 'hidden'); chart.circles.forEach((circle) => { circle.setAttribute('visibility', 'hidden'); }); chart.tooltip.hidden = true; });
}
function updateChartHover(index) {
  const sample = history[index]; if (!sample) return;
  document.querySelectorAll('.monitor-chart').forEach((root) => {
    const chart = root._monitorChart; if (!chart) return; const x = chart.left + (index / (history.length - 1)) * chart.plotWidth;
    chart.line.setAttribute('x1', x); chart.line.setAttribute('x2', x); chart.line.setAttribute('visibility', 'visible'); chart.tooltip.replaceChildren();
    const time = document.createElement('strong'); time.textContent = exactTime(sample.timestamp); chart.tooltip.append(time);
    chart.series.forEach((item, seriesIndex) => {
      const value = sample[item.key]; const row = document.createElement('span'); row.className = `series-${item.tone}`; const dot = document.createElement('i'); const label = document.createElement('span'); const formatted = value === null ? '—' : (chart.options.formatTooltip || chart.options.formatValue)(value, sample); label.textContent = `${item.label} ${formatted}`; row.append(dot, label); chart.tooltip.append(row);
      const circle = chart.circles[seriesIndex]; if (value === null) { circle.setAttribute('visibility', 'hidden'); return; } const ratio = Math.max(0, Math.min(1, (value - chart.lower) / chart.range)); circle.setAttribute('cx', x); circle.setAttribute('cy', chart.bottom - ratio * chart.plotHeight); circle.setAttribute('visibility', 'visible');
    });
    chart.tooltip.style.left = `${Math.max(13, Math.min(87, x / chart.width * 100))}%`; chart.tooltip.hidden = false;
  });
}

function renderChart(selector, series, options) {
  const root = document.querySelector(selector); if (!root) return; root.replaceChildren();
  const legend = document.createElement('div'); legend.className = 'monitor-chart-legend';
  series.forEach((item) => { const currentSample = [...history].reverse().find((sample) => sample[item.key] !== null); const entry = document.createElement('span'); entry.className = `monitor-legend-item series-${item.tone}`; const dot = document.createElement('i'); dot.className = 'monitor-legend-dot'; const label = document.createElement('span'); label.textContent = item.label; const value = document.createElement('b'); value.textContent = currentSample ? (options.formatLegend || options.formatValue)(currentSample[item.key], currentSample) : '—'; entry.append(dot, label, value); legend.append(entry); }); root.append(legend);
  if (history.length < 2) { const empty = document.createElement('div'); empty.className = 'monitor-chart-empty'; empty.textContent = '正在积累趋势数据…'; root.append(empty); return; }
  const values = history.flatMap((sample) => series.map((item) => sample[item.key])).filter((value) => value !== null); if (!values.length) { const empty = document.createElement('div'); empty.className = 'monitor-chart-empty'; empty.textContent = '暂无可用数据'; root.append(empty); return; }
  const [lower, upper] = options.domain(values); const range = Math.max(upper - lower, 1e-9); const axisLabels = Array.from({ length: 5 }, (_, index) => options.formatAxis(upper - index / 4 * range)); const width = 640; const height = 210; const left = Math.min(90, Math.max(48, Math.max(...axisLabels.map((label) => label.length)) * 5.7 + 12)); const right = 626; const top = 12; const bottom = 184; const plotWidth = right - left; const plotHeight = bottom - top; const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': options.label });
  for (let index = 0; index <= 4; index += 1) { const ratio = index / 4; const y = top + ratio * plotHeight; svg.append(svgElement('line', { x1: left, y1: y, x2: right, y2: y, class: 'monitor-chart-grid' })); const label = svgElement('text', { x: left - 8, y: y + 3, 'text-anchor': 'end', class: 'monitor-chart-axis' }); label.textContent = axisLabels[index]; svg.append(label); }
  const startLabel = svgElement('text', { x: left, y: 204, class: 'monitor-chart-axis' }); startLabel.textContent = axisTime(history[0].timestamp); const endLabel = svgElement('text', { x: right, y: 204, 'text-anchor': 'end', class: 'monitor-chart-axis' }); endLabel.textContent = axisTime(history[history.length - 1].timestamp); svg.append(startLabel, endLabel);
  series.forEach((item) => { let path = ''; let drawing = false; history.forEach((sample, index) => { const value = sample[item.key]; if (value === null) { drawing = false; return; } const x = left + (index / (history.length - 1)) * plotWidth; const ratio = Math.max(0, Math.min(1, (value - lower) / range)); const y = bottom - ratio * plotHeight; path += `${drawing ? ' L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`; drawing = true; }); if (path) svg.append(svgElement('path', { d: path, class: `monitor-chart-line series-${item.tone}` })); });
  const hoverLine = svgElement('line', { y1: top, y2: bottom, visibility: 'hidden', class: 'monitor-chart-hover-line' }); const circles = series.map((item) => { const circle = svgElement('circle', { r: 4, visibility: 'hidden', class: `monitor-chart-hover-point series-${item.tone}` }); svg.append(circle); return circle; }); svg.append(hoverLine);
  const capture = svgElement('rect', { x: left, y: top, width: plotWidth, height: plotHeight, class: 'monitor-chart-capture' }); capture.addEventListener('pointermove', (event) => { const matrix = svg.getScreenCTM(); if (!matrix) return; const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY; const viewPoint = point.matrixTransform(matrix.inverse()); hoverRatio = Math.max(0, Math.min(1, (viewPoint.x - left) / plotWidth)); updateChartHover(Math.round(hoverRatio * (history.length - 1))); }); capture.addEventListener('pointerleave', (event) => { if (event.pointerType !== 'touch') clearChartHover(); }); svg.append(capture); root.append(svg);
  const tooltip = document.createElement('div'); tooltip.className = 'monitor-chart-tooltip'; tooltip.hidden = true; root.append(tooltip); root._monitorChart = { series, options, width, left, right, top, bottom, plotWidth, plotHeight, lower, range, line: hoverLine, circles, tooltip };
}

function renderMonitoring(data, formatRate, formatBytes) {
  const percent = (selector, key, label, tone) => renderChart(selector, [{ key, label, tone }], { label: `${label}趋势`, domain: () => [0, 100], formatAxis: (value) => `${Math.round(value)}%`, formatValue: (value) => `${value.toFixed(1)}%` }); const rate = (selector, key, label, tone) => renderChart(selector, [{ key, label, tone }], { label: `${label}趋势`, domain: (values) => [0, niceCeiling(Math.max(...values) * 1.08)], formatAxis: formatRate, formatValue: formatRate });
  percent('#monitor-cpu-chart', 'cpu', 'CPU 使用率', 'blue');
  renderChart('#monitor-memory-chart', [{ key: 'memory', label: '内存', tone: 'green' }], { label: '内存使用率趋势', domain: () => [0, 100], formatAxis: (value) => `${Math.round(value)}%`, formatValue: (value) => `${value.toFixed(1)}%`, formatLegend: (value, sample) => `${value.toFixed(1)}% · ${formatBytes(sample.memoryUsed)} / ${formatBytes(data.memory?.total)}`, formatTooltip: (value, sample) => `${value.toFixed(1)}% · ${formatBytes(sample.memoryUsed)} / ${formatBytes(data.memory?.total)}` });
  rate('#monitor-rx-chart', 'rx', '交换接收', 'purple'); rate('#monitor-tx-chart', 'tx', '交换发送', 'cyan');
  if (hoverRatio !== null && history.length > 1 && document.querySelector('.monitor-chart:hover')) updateChartHover(Math.round(hoverRatio * (history.length - 1)));
  else if (!document.querySelector('.monitor-chart:hover')) clearChartHover();
}

async function loadHistory(seconds) {
  if (!apiClient) return; selectedRange = seconds; window.localStorage.setItem(RANGE_STORAGE_KEY, String(seconds)); document.querySelectorAll('[data-monitor-range]').forEach((button) => button.classList.toggle('active', Number(button.dataset.monitorRange) === seconds)); document.querySelectorAll('.monitor-range-label').forEach((label) => { label.textContent = RANGE_LABELS.get(seconds) || `最近 ${seconds} 秒`; });
  try { const result = await apiClient(`/api/telemetry/history?range=${seconds}`); history = (result.samples || []).map(normalizeSample).filter((sample) => Number.isFinite(sample.timestamp)); if (latestTelemetry && latestFormats) renderMonitoring(latestTelemetry, latestFormats.formatRate, latestFormats.formatBytes); } catch (error) { console.warn('监控历史读取失败', error); }
}
function usageValue(value) {
  const bytes = latestFormats?.formatBytes || ((number) => `${number} B`); return `接收 ${bytes(value?.rx_bytes || 0)} · 发送 ${bytes(value?.tx_bytes || 0)}`;
}
function usageBytes(value) { return (latestFormats?.formatBytes || ((number) => `${number} B`))(Number(value) || 0); }
function usageRate(value) { return (latestFormats?.formatRate || ((number) => `${number} bit/s`))(Number(value) || 0); }
function usageTotal(value) { return Number(value?.rx_bytes || 0) + Number(value?.tx_bytes || 0); }
function seriesLabel(item, view) {
  if (view === 'five_minute' || view === 'hourly') return item.label.slice(6);
  if (view === 'daily') return item.label.slice(5);
  return item.label;
}
function renderTrafficBars(chart, items, emptyText, options = {}) {
  chart.replaceChildren(); chart.classList.toggle('empty', !items.length); chart.classList.toggle('dense', Boolean(options.dense));
  if (!items.length) { chart.append(Object.assign(document.createElement('div'), { className: 'usage-empty-state', textContent: emptyText })); return; }
  const maximum = niceCeiling(Math.max(...items.flatMap((item) => [Number(item.rx_bytes), Number(item.tx_bytes)]), 1));
  const legend = document.createElement('div'); legend.className = 'usage-chart-legend'; legend.innerHTML = '<span><i class="usage-bar-rx"></i>接收</span><span><i class="usage-bar-tx"></i>发送</span>';
  const body = document.createElement('div'); body.className = 'usage-chart-body'; const axis = document.createElement('div'); axis.className = 'usage-chart-axis'; [maximum, maximum / 2, 0].forEach((amount) => axis.append(Object.assign(document.createElement('span'), { textContent: usageBytes(amount) })));
  const scroll = document.createElement('div'); scroll.className = 'usage-chart-scroll'; const plot = document.createElement('div'); plot.className = 'usage-chart-plot'; const tooltip = document.createElement('div'); tooltip.className = 'usage-chart-tooltip'; tooltip.hidden = true;
  const showTooltip = (item, column, event = null) => { tooltip.replaceChildren(); const title = document.createElement('strong'); title.textContent = item.label; const rx = document.createElement('span'); rx.textContent = `接收 ${usageBytes(item.rx_bytes)}`; const tx = document.createElement('span'); tx.textContent = `发送 ${usageBytes(item.tx_bytes)}`; const total = document.createElement('span'); total.textContent = `合计 ${usageBytes(usageTotal(item))}`; tooltip.append(title, rx, tx, total); if (item.average_bps !== undefined) { const average = document.createElement('span'); average.textContent = `平均 ${usageRate(item.average_bps)}`; tooltip.append(average); } const chartRect = chart.getBoundingClientRect(); const columnRect = column.getBoundingClientRect(); const x = event ? event.clientX - chartRect.left : columnRect.left + columnRect.width / 2 - chartRect.left; tooltip.style.left = `${Math.max(82, Math.min(chartRect.width - 82, x))}px`; tooltip.hidden = false; };
  const labelEvery = options.labelEvery || 3;
  items.forEach((item, index) => { const axisLabel = options.axisLabel ? options.axisLabel(item) : item.label; const column = document.createElement('div'); column.className = 'usage-day'; column.tabIndex = 0; column.setAttribute('role', 'img'); column.setAttribute('aria-label', `${item.label}，${usageValue(item)}`); const bars = document.createElement('div'); bars.className = 'usage-day-bars'; const rx = document.createElement('i'); rx.className = 'usage-bar-rx'; rx.style.height = `${Math.max(2, Number(item.rx_bytes) / maximum * 100)}%`; const tx = document.createElement('i'); tx.className = 'usage-bar-tx'; tx.style.height = `${Math.max(2, Number(item.tx_bytes) / maximum * 100)}%`; bars.append(rx, tx); const label = document.createElement('small'); label.textContent = index === items.length - 1 || index % labelEvery === 0 ? axisLabel : ''; column.append(bars, label); column.addEventListener('pointerenter', (event) => showTooltip(item, column, event)); column.addEventListener('pointermove', (event) => showTooltip(item, column, event)); column.addEventListener('pointerleave', () => { tooltip.hidden = true; }); column.addEventListener('focus', () => showTooltip(item, column)); column.addEventListener('blur', () => { tooltip.hidden = true; }); plot.append(column); });
  scroll.append(plot); body.append(axis, scroll); chart.append(legend, body, tooltip);
}
function renderUsageTable(root, items, emptyText) {
  root.replaceChildren();
  if (!items.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 5; cell.className = 'usage-table-empty'; cell.textContent = emptyText; row.append(cell); root.append(row); return; }
  [...items].reverse().forEach((item) => { const row = document.createElement('tr'); [item.label, usageBytes(item.rx_bytes), usageBytes(item.tx_bytes), usageBytes(usageTotal(item)), usageRate(item.average_bps)].forEach((value, index) => { const cell = document.createElement('td'); cell.textContent = value; if (index) cell.className = 'numeric'; row.append(cell); }); root.append(row); });
}
const SERIES_VIEWS = {
  five_minute: { title: '5 分钟流量', description: '最近 24 小时', dense: true, labelEvery: 12 },
  hourly: { title: '小时流量', description: '最近 48 小时', labelEvery: 4 },
  daily: { title: '每日流量', description: '当前保留期', labelEvery: 3 },
  monthly: { title: '每月流量', description: '当前保留期', labelEvery: 1 },
};
function peakItem(items) { return (items || []).reduce((best, item) => !best || usageTotal(item) > usageTotal(best) ? item : best, null); }
function renderPeakCards() {
  const root = document.querySelector('#usage-peak-cards'); root.replaceChildren();
  [['5 分钟峰值', 'five_minute'], ['小时峰值', 'hourly'], ['单日峰值', 'daily']].forEach(([title, key]) => { const item = peakItem(usageData?.series?.[key]); const card = document.createElement('section'); card.className = 'panel usage-peak-card'; const label = document.createElement('small'); label.textContent = title; const time = document.createElement('strong'); time.textContent = item?.label || '暂无记录'; const amount = document.createElement('span'); amount.textContent = item ? `${usageBytes(usageTotal(item))} · ${usageRate(item.average_bps)}` : '—'; card.append(label, time, amount); root.append(card); });
  renderUsageTable(document.querySelector('#usage-top-day-rows'), usageData?.top_days || [], '尚无每日流量记录');
}
function renderUsageSubview() {
  document.querySelectorAll('[data-usage-view]').forEach((button) => button.classList.toggle('active', button.dataset.usageView === selectedUsageView));
  document.querySelector('#usage-overview-view').hidden = selectedUsageView !== 'overview';
  document.querySelector('#usage-series-view').hidden = !SERIES_VIEWS[selectedUsageView];
  document.querySelector('#usage-peaks-view').hidden = selectedUsageView !== 'peaks';
  if (!usageData) return;
  if (SERIES_VIEWS[selectedUsageView]) {
    const config = SERIES_VIEWS[selectedUsageView]; const items = usageData.series?.[selectedUsageView] || [];
    document.querySelector('#usage-series-title').textContent = config.title; document.querySelector('#usage-series-description').textContent = config.description;
    renderTrafficBars(document.querySelector('#usage-series-chart'), items, `正在积累${config.title}…`, { dense: config.dense, labelEvery: config.labelEvery, axisLabel: (item) => seriesLabel(item, selectedUsageView) });
    renderUsageTable(document.querySelector('#usage-series-rows'), items, `尚无${config.title}记录`);
  } else if (selectedUsageView === 'peaks') renderPeakCards();
}
function renderUsage(value) {
  usageData = value;
  document.querySelector('#usage-error').hidden = true;
  document.querySelector('#usage-disabled').hidden = value.enabled;
  document.querySelector('#usage-content').hidden = !value.enabled;
  if (!value.enabled) return;
  const totalLabel = document.querySelector('#usage-total-label');
  totalLabel.textContent = `最近 ${value.retention_days} 日`;
  totalLabel.title = `数据库当前保留的全部流量记录之和；历史最多保留 ${value.retention_days} 天，清除历史后重新累计。`;
  document.querySelector('#usage-ranking-panel').hidden = Boolean(value.selected_port);
  [['#usage-today', 'today'], ['#usage-yesterday', 'yesterday'], ['#usage-month', 'month'], ['#usage-total', 'total']].forEach(([selector, key]) => { document.querySelector(selector).textContent = usageValue(value.summary?.[key]); });
  const recentHours = (value.series?.hourly || []).slice(-24); renderTrafficBars(document.querySelector('#usage-overview-chart'), recentHours, '正在积累小时流量…', { labelEvery: 3, axisLabel: (item) => seriesLabel(item, 'hourly') });
  const list = document.querySelector('#usage-port-list'); list.replaceChildren(); const ports = (value.ports || []).filter((port) => Number(port.rx_bytes) + Number(port.tx_bytes) > 0); list.classList.toggle('empty', !ports.length);
  if (!ports.length) list.append(Object.assign(document.createElement('div'), { className: 'usage-empty-state', textContent: '今日尚无端口流量记录' }));
  ports.forEach((port, index) => { const endpoint = (uiState?.l2?.endpoints || []).find((item) => item.logical === port.logical); const row = document.createElement('div'); row.className = 'usage-port-row'; const identity = document.createElement('span'); const name = document.createElement('strong'); name.textContent = `${index + 1}. ${endpoint?.name || `端口 ${port.logical}`}`; const location = document.createElement('small'); location.textContent = `EPL ${port.epl} · Lane ${port.lane}`; identity.append(name, location); const amount = document.createElement('span'); amount.textContent = usageValue(port); row.append(identity, amount); list.append(row); });
  renderUsageSubview();
}
async function loadUsage() {
  const requestId = ++usageRequestId; const refresh = document.querySelector('#usage-refresh'); refresh.disabled = true;
  try {
    const port = document.querySelector('#usage-port').value;
    const value = await apiClient(`/api/telemetry/traffic-usage${port ? `?port=${encodeURIComponent(port)}` : ''}`);
    if (requestId === usageRequestId) renderUsage(value);
  } catch (error) {
    if (requestId !== usageRequestId) return;
    document.querySelector('#usage-disabled').hidden = true; document.querySelector('#usage-content').hidden = true;
    document.querySelector('#usage-error-message').textContent = error.message || '请稍后重试。'; document.querySelector('#usage-error').hidden = false;
    console.warn('流量统计读取失败', error);
  } finally { if (requestId === usageRequestId) refresh.disabled = false; }
}
async function selectView(view) {
  document.querySelectorAll('[data-monitor-view]').forEach((button) => button.classList.toggle('active', button.dataset.monitorView === view));
  document.querySelector('#monitor-realtime-view').hidden = view !== 'realtime'; document.querySelector('#monitor-usage-view').hidden = view !== 'usage';
  if (view === 'usage') await loadUsage();
}
export async function initializeMonitoring(api, state) { apiClient = api; uiState = state; if (!rangeControlsBound) { document.querySelectorAll('[data-monitor-range]').forEach((button) => button.addEventListener('click', () => loadHistory(Number(button.dataset.monitorRange)))); document.querySelectorAll('[data-monitor-view]').forEach((button) => button.addEventListener('click', () => selectView(button.dataset.monitorView))); document.querySelectorAll('[data-usage-view]').forEach((button) => button.addEventListener('click', () => { selectedUsageView = button.dataset.usageView; renderUsageSubview(); })); const select = document.querySelector('#usage-port'); (state?.endpoints || []).forEach((endpoint) => { const option = document.createElement('option'); option.value = `${endpoint.group}.lane${endpoint.lane ?? 0}`; const configured = (state?.l2?.endpoints || []).find((item) => item.key === endpoint.key); option.textContent = configured?.name ? `${configured.name} · 端口 ${endpoint.logical} · ${endpoint.label}` : `端口 ${endpoint.logical} · ${endpoint.label}`; select.append(option); }); select.addEventListener('change', loadUsage); document.querySelector('#usage-refresh').addEventListener('click', loadUsage); window.addEventListener('telemetry-history-changed', loadUsage); rangeControlsBound = true; } await loadHistory(selectedRange); }
export function recordMonitoringSample(data, formatRate, formatBytes) { latestTelemetry = data; latestFormats = { formatRate, formatBytes }; upsertSample(collectSample(data)); renderMonitoring(data, formatRate, formatBytes); }
