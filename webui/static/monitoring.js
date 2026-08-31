'use strict';

const MAX_SAMPLES = 300;
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const history = [];

function finite(value) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) ? null : number;
}

function collectSample(data) {
  const traffic = data.port_status?.traffic || {};
  return {
    cpu: finite(data.cpu?.usage_percent),
    memory: finite(data.memory?.usage_percent),
    rx: finite(traffic.rx_bps),
    tx: finite(traffic.tx_bps),
  };
}

function setText(selector, value) {
  const target = document.querySelector(selector);
  if (target) target.textContent = value;
}

function niceCeiling(value) {
  if (!(value > 0)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderChart(selector, series, options) {
  const root = document.querySelector(selector);
  if (!root) return;
  root.replaceChildren();

  const legend = document.createElement('div');
  legend.className = 'monitor-chart-legend';
  series.forEach((item) => {
    const current = [...history].reverse().map((sample) => sample[item.key]).find((value) => value !== null);
    const entry = document.createElement('span');
    entry.className = `monitor-legend-item series-${item.tone}`;
    const dot = document.createElement('i'); dot.className = 'monitor-legend-dot';
    const label = document.createElement('span'); label.textContent = item.label;
    const value = document.createElement('b'); value.textContent = current === undefined ? '—' : options.formatValue(current);
    entry.append(dot, label, value); legend.append(entry);
  });
  root.append(legend);

  if (history.length < 2) {
    const empty = document.createElement('div');
    empty.className = 'monitor-chart-empty';
    empty.textContent = '正在积累趋势数据…';
    root.append(empty);
    return;
  }

  const values = history.flatMap((sample) => series.map((item) => sample[item.key])).filter((value) => value !== null);
  if (!values.length) {
    const empty = document.createElement('div');
    empty.className = 'monitor-chart-empty';
    empty.textContent = '暂无可用数据';
    root.append(empty);
    return;
  }

  const [lower, upper] = options.domain(values);
  const width = 640; const height = 210;
  const left = 48; const right = 626; const top = 12; const bottom = 184;
  const plotWidth = right - left; const plotHeight = bottom - top;
  const range = Math.max(upper - lower, 1e-9);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': options.label });

  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = top + ratio * plotHeight;
    svg.append(svgElement('line', { x1: left, y1: y, x2: right, y2: y, class: 'monitor-chart-grid' }));
    const label = svgElement('text', { x: left - 8, y: y + 3, 'text-anchor': 'end', class: 'monitor-chart-axis' });
    label.textContent = options.formatAxis(upper - ratio * range);
    svg.append(label);
  }

  const startLabel = svgElement('text', { x: left, y: 204, class: 'monitor-chart-axis' });
  startLabel.textContent = history.length >= MAX_SAMPLES ? '5 分钟前' : '会话开始';
  const endLabel = svgElement('text', { x: right, y: 204, 'text-anchor': 'end', class: 'monitor-chart-axis' });
  endLabel.textContent = '现在';
  svg.append(startLabel, endLabel);

  series.forEach((item) => {
    let path = '';
    let drawing = false;
    history.forEach((sample, index) => {
      const value = sample[item.key];
      if (value === null) { drawing = false; return; }
      const x = history.length === 1 ? right : left + (index / (history.length - 1)) * plotWidth;
      const ratio = Math.max(0, Math.min(1, (value - lower) / range));
      const y = bottom - ratio * plotHeight;
      path += `${drawing ? ' L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`;
      drawing = true;
    });
    if (path) svg.append(svgElement('path', { d: path, class: `monitor-chart-line series-${item.tone}` }));
  });
  root.append(svg);
}

function renderMonitoring(data, formatRate, formatBytes) {
  const latest = history[history.length - 1];
  setText('#monitor-cpu', latest.cpu === null ? '—' : `${latest.cpu}%`);
  setText('#monitor-cpu-note', `${data.cpu?.cores || 0} 核`);
  setText('#monitor-memory', latest.memory === null ? '—' : `${latest.memory}%`);
  setText('#monitor-memory-note', `${formatBytes(data.memory?.used)} / ${formatBytes(data.memory?.total)}`);
  setText('#monitor-rx', latest.rx === null ? '—' : formatRate(latest.rx));
  setText('#monitor-tx', latest.tx === null ? '—' : formatRate(latest.tx));

  renderChart('#monitor-resource-chart', [
    { key: 'cpu', label: 'CPU', tone: 'blue' },
    { key: 'memory', label: '内存', tone: 'green' },
  ], {
    label: 'CPU 与内存使用率趋势',
    domain: () => [0, 100],
    formatAxis: (value) => `${Math.round(value)}%`,
    formatValue: (value) => `${value.toFixed(1)}%`,
  });

  renderChart('#monitor-traffic-chart', [
    { key: 'rx', label: '接收', tone: 'purple' },
    { key: 'tx', label: '发送', tone: 'cyan' },
  ], {
    label: '交换端口收发速率趋势',
    domain: (values) => [0, niceCeiling(Math.max(...values) * 1.08)],
    formatAxis: formatRate,
    formatValue: formatRate,
  });

}

export function recordMonitoringSample(data, formatRate, formatBytes) {
  history.push(collectSample(data));
  if (history.length > MAX_SAMPLES) history.splice(0, history.length - MAX_SAMPLES);
  renderMonitoring(data, formatRate, formatBytes);
}
