'use strict';

import { createApiClient, waitForJob } from '/api-client.js';
import { closeCustomSelects, destroySelects, enhanceNumberInputs, enhanceSelects, syncSelect } from '/controls.js';

const TRAFFIC_UNIT_STORAGE_KEY = 'pe31625g24dira-traffic-unit';
const ui = { state: null, csrf: null, topology: {}, vlans: [], l2: null, l2Saved: null, pendingPortAdmin: null, vlanTaggedPort: null, vlanPreview: null, fan: null, importedConfig: null, upgradeReady: false, upgradeCandidate: null, live: {}, telemetry: null, pendingTelemetry: null, busy: false, poweringOff: false, rebooting: false, telemetryTimer: null, healthTimer: null, logTimer: null, toastTimer: null, topologyPreview: null, logSource: 'system', logsLoaded: false, logAutoFollow: true, logRequestId: 0, trafficUnit: window.localStorage.getItem(TRAFFIC_UNIT_STORAGE_KEY) === 'bytes' ? 'bytes' : 'bits' };
const $ = (selector) => document.querySelector(selector);
const speedLabel = (speed) => `${speed / 1000}G`;
const clone = (value) => JSON.parse(JSON.stringify(value));
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const TELEMETRY_INTERVAL_SECONDS = 1;
const PAGE_PATHS = { overview: '/overview', sensors: '/sensors', logs: '/logs', ports: '/ports', stats: '/statistics', vlans: '/vlans', network: '/network', system: '/system', cooling: '/cooling', maintenance: '/backup', settings: '/settings' };
const PATH_PAGES = Object.fromEntries(Object.entries(PAGE_PATHS).map(([page, path]) => [path, page]));
const api = createApiClient(() => ui.csrf);

function showToast(message, kind = 'error') {
  const toast = $('#toast');
  if (ui.toastTimer) window.clearTimeout(ui.toastTimer);
  toast.textContent = message;
  toast.className = `toast ${kind}`;
  toast.hidden = false;
  ui.toastTimer = window.setTimeout(() => { toast.hidden = true; ui.toastTimer = null; }, kind === 'success' ? 3500 : 6000);
}

function choiceFor(group) {
  return group.layout === 'bonded' ? { layout: 'bonded', speed: group.speed } : { layout: 'split', speeds: group.lanes.map((lane) => lane.speed) };
}

function sameChoice(a, b) {
  if (!a || a.layout !== b.layout) return false;
  return a.layout === 'bonded' ? a.speed === b.speed : a.speeds.every((value, index) => value === b.speeds[index]);
}

function setPage(name, updateHistory = true) {
  if (!PAGE_PATHS[name]) name = 'overview';
  document.querySelectorAll('.page').forEach((page) => page.classList.toggle('active', page.id === `page-${name}`));
  const navigationPage = name === 'stats' ? 'ports' : name;
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.page === navigationPage));
  document.querySelectorAll('.nav-group').forEach((group) => {
    const active = Boolean(group.querySelector(`.nav-item[data-page="${navigationPage}"]`));
    group.classList.toggle('active', active);
    group.classList.toggle('open', active);
    group.querySelector('.nav-group-toggle').setAttribute('aria-expanded', String(group.classList.contains('open')));
  });
  const labels = { overview: '概览', sensors: '传感器', logs: '日志', ports: '端口', stats: '端口统计', vlans: 'VLAN', network: '网络功能', system: '系统信息', cooling: '散热', maintenance: '备份与升级', settings: '设置' };
  $('#page-title').textContent = labels[name];
  $('#top-page-title').textContent = labels[name];
  if (name === 'logs' && !ui.logsLoaded) loadLogs();
  if (updateHistory && window.location.pathname !== PAGE_PATHS[name]) window.history.pushState({ page: name }, '', PAGE_PATHS[name]);
}

function pageFromLocation() { return PATH_PAGES[window.location.pathname] || 'overview'; }

function formatBytes(value) {
  if (value === null || value === undefined) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let number = Number(value); let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number.toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function formatRate(value) {
  if (value === null || value === undefined) return '采样中';
  const bytes = ui.trafficUnit === 'bytes';
  const units = bytes ? ['B/s', 'KB/s', 'MB/s', 'GB/s'] : ['bit/s', 'Kbit/s', 'Mbit/s', 'Gbit/s'];
  let number = Number(value) / (bytes ? 8 : 1); let index = 0;
  while (number >= 1000 && index < units.length - 1) { number /= 1000; index += 1; }
  return `${number.toFixed(index > 0 ? 1 : 0)} ${units[index]}`;
}

function formatBitRate(value) {
  if (value === null || value === undefined) return '采样中';
  const units = ['bit/s', 'Kbit/s', 'Mbit/s', 'Gbit/s'];
  let number = Number(value); let index = 0;
  while (number >= 1000 && index < units.length - 1) { number /= 1000; index += 1; }
  return `${number.toFixed(index > 0 ? 1 : 0)} ${units[index]}`;
}

function formatEthernetLink(speedMbps, duplex) {
  const speed = Number(speedMbps);
  const rate = speed >= 1000
    ? `${Number.isInteger(speed / 1000) ? speed / 1000 : (speed / 1000).toFixed(1)} Gbps`
    : `${speed || '?'} Mbps`;
  const modes = { full: 'Full', half: 'Half' };
  return `${rate} · ${modes[String(duplex).toLowerCase()] || 'Unknown'}`;
}

function formatCount(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function formatUptime(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const total = Math.max(0, Math.floor(Number(seconds)));
  if (!Number.isFinite(total)) return '—';
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const parts = [];
  if (days > 0) parts.push(`${days}d`);
  if (days > 0 || hours > 0) parts.push(`${days > 0 ? String(hours).padStart(2, '0') : hours}h`);
  if (days > 0 || hours > 0 || mins > 0) parts.push(`${days > 0 || hours > 0 ? String(mins).padStart(2, '0') : mins}m`);
  parts.push(`${String(secs).padStart(2, '0')}s`);
  return parts.join(' ');
}

function loadPercent(rate, capacity) {
  if (rate === null || rate === undefined || !capacity) return '—';
  const value = Math.max(0, Number(rate)) / capacity * 100;
  return value > 0 && value < 0.1 ? '<0.1%' : `${value.toFixed(1)}%`;
}

function sensorBox(name, value, suffix = '°C', digits = 1) {
  const box = document.createElement('div');
  const temperature = suffix === '°C' ? Number(value) : null;
  const tone = temperature !== null && temperature >= 100 ? ' danger' : temperature !== null && temperature >= 85 ? ' warning' : '';
  box.className = `sensor${tone}`;
  const label = document.createElement('small'); label.textContent = name;
  const strong = document.createElement('strong'); strong.textContent = `${Number(value).toFixed(digits)}${suffix}`;
  box.append(label, strong);
  return box;
}

function managementCard(net) {
  const card = document.createElement('article'); card.className = `management-card${net.carrier ? ' up' : ''}`;
  const head = document.createElement('div'); head.className = 'management-head';
  const title = document.createElement('div'); const strong = document.createElement('strong'); strong.textContent = net.interface; title.append(strong);
  const badge = document.createElement('span'); badge.className = `link ${net.carrier ? 'up' : ''}`; badge.textContent = net.carrier ? 'UP' : 'DOWN'; head.append(title, badge); card.append(head);
  const details = document.createElement('dl'); details.className = 'management-details';
  [['IPv4', net.ipv4.join(', ') || '—'], ['MAC', net.mac || '—'], ['网关', net.gateway || '—'], ['链路', net.carrier ? formatEthernetLink(net.speed_mbps, net.duplex) : '—']].forEach(([label, value]) => { const dt = document.createElement('dt'); dt.textContent = label; const dd = document.createElement('dd'); dd.textContent = value; details.append(dt, dd); }); card.append(details);
  const traffic = document.createElement('div'); traffic.className = 'management-traffic';
  traffic.innerHTML = `<span>接收 <b>${formatRate(net.rx_bps)}</b><small>${formatBytes(net.statistics.rx_bytes)}</small></span><span>发送 <b>${formatRate(net.tx_bps)}</b><small>${formatBytes(net.statistics.tx_bytes)}</small></span>`; card.append(traffic);
  return card;
}

function renderActivePorts(portStatus) {
  const root = $('#active-port-list'); root.replaceChildren();
  const ports = portStatus && portStatus.state === 'ready' ? portStatus.ports : {};
  const portEntries = Object.entries(ports);
  const active = portEntries.filter(([, item]) => item.oper === 'UP');
  const activeCount = $('#active-port-count');
  activeCount.textContent = `${active.length} / ${portEntries.length}`;
  if (!active.length) {
    root.append(Object.assign(document.createElement('span'), { className: 'muted', textContent: '无活动端口' }));
    return;
  }
  const endpoints = new Map((ui.state && ui.state.endpoints || []).map((item) => [String(item.logical), item]));
  active.forEach(([logical, live]) => {
    const endpoint = endpoints.get(logical);
    const row = document.createElement('div'); row.className = 'active-port-row';
    const identity = document.createElement('div'); identity.className = 'active-port-identity';
    const configured = ui.l2 && ui.l2.endpoints.find((item) => item.logical === Number(logical));
    const name = document.createElement('strong'); name.textContent = configured && configured.name ? configured.name : `端口 ${logical}`;
    const location = document.createElement('small'); location.textContent = endpoint ? endpoint.label : `EPL ${live.epl} · Lane ${Number(live.lane)}`;
    identity.append(name, location);
    const metrics = document.createElement('div'); metrics.className = 'active-port-metrics';
    const traffic = document.createElement('span'); traffic.className = 'active-port-traffic'; traffic.textContent = `↓ ${formatRate(live.rx_bps)}  ↑ ${formatRate(live.tx_bps)}`;
    const speed = document.createElement('strong'); speed.className = 'active-port-speed'; speed.textContent = live.speed || (endpoint ? speedLabel(endpoint.speed) : '—');
    const speedClass = /^\d+G$/.test(speed.textContent) ? `speed-${speed.textContent.toLowerCase()}` : '';
    if (speedClass) speed.classList.add(speedClass);
    metrics.append(traffic, speed);
    row.append(identity, metrics); root.append(row);
  });
}

function statItems(target, entries) {
  const root = $(target); root.replaceChildren();
  entries.forEach(([label, value, tone, alertValue]) => { const item = document.createElement('div'); const alert = Number(alertValue === undefined ? value : alertValue) > 0; item.className = `stat-item${tone && alert ? ` ${tone}` : ''}`; const small = document.createElement('small'); small.textContent = label; const strong = document.createElement('strong'); strong.textContent = typeof value === 'string' ? value : formatCount(value); item.append(small, strong); root.append(item); });
}

function renderPortStatistics() {
  const select = $('#stats-port'); const live = ui.live[String(select.value)];
  if (!live || !live.statistics) return;
  const rx = live.statistics.rx; const tx = live.statistics.tx;
  $('#stat-rx-unicast').textContent = formatCount(rx.unicast); $('#stat-tx-unicast').textContent = formatCount(tx.unicast);
  $('#stat-rx-bytes').textContent = formatBytes(rx.good_bytes); $('#stat-tx-bytes').textContent = formatBytes(tx.good_bytes);
  const rxErrors = rx.framing_errors + rx.fcs_errors;
  const txDiscards = tx.timeout_drops + tx.error_drops + tx.ecc_drops + tx.loopback_drops + tx.ttl_drops;
  $('#stat-rx-errors').textContent = formatCount(rxErrors);
  $('#stat-tx-discards').textContent = formatCount(txDiscards);
  $('#stat-rx-errors').parentElement.classList.toggle('danger', rxErrors > 0);
  $('#stat-tx-discards').parentElement.classList.toggle('warning', txDiscards > 0);
  statItems('#stats-rx', [['速率', formatRate(live.rx_bps)], ['帧', rx.frames], ['单播', rx.unicast], ['组播', rx.multicast], ['广播', rx.broadcast], ['Pause 帧', rx.pause], ['PFC Pause', rx.pfc_pause], ['有效字节', formatBytes(rx.good_bytes)], ['错误字节', formatBytes(rx.bad_bytes), 'danger', rx.bad_bytes]]);
  statItems('#stats-tx', [['速率', formatRate(live.tx_bps)], ['帧', tx.frames], ['单播', tx.unicast], ['组播', tx.multicast], ['广播', tx.broadcast], ['Pause 帧', tx.pause], ['PFC Pause', tx.pfc_pause], ['有效字节', formatBytes(tx.good_bytes)], ['错误字节', formatBytes(tx.bad_bytes), 'danger', tx.bad_bytes]]);
  const lengthNames = { lt_64: '<64 B', eq_64: '64 B', '65_127': '65–127 B', '128_255': '128–255 B', '256_511': '256–511 B', '512_1023': '512–1023 B', '1024_1522': '1024–1522 B', '1523_2047': '1523–2047 B', '2048_4095': '2048–4095 B', '4096_8191': '4096–8191 B', '8192_10239': '8192–10239 B', ge_10240: '≥10240 B' };
  statItems('#stats-rx-length', Object.entries(rx.length).map(([key, value]) => [lengthNames[key] || key, value]));
  statItems('#stats-tx-length', Object.entries(tx.length).map(([key, value]) => [lengthNames[key] || key, value]));
  const a = rx.actions; const d = rx.drops; const m = rx.mac;
  statItems('#stats-errors', [['接收 FCS/CRC 错误', rx.fcs_errors, 'danger'], ['接收帧错误', rx.framing_errors, 'danger'], ['编码错误', m.code_errors, 'danger'], ['超长帧', m.oversize], ['Jabber', m.jabber], ['短帧', m.undersize], ['Runt', m.runt], ['接收 Overrun', m.overrun], ['发送 Underrun', m.underrun], ['发送 FCS 错误', tx.bad_fcs, 'danger'], ['发送错误丢弃', tx.error_drops, 'warning'], ['发送超时丢弃', tx.timeout_drops, 'warning'], ['发送 ECC 丢弃', tx.ecc_drops, 'warning'], ['环回抑制', tx.loopback_drops + d.loopback_suppress], ['STP 丢弃', a.stp], ['VLAN 标签丢弃', a.vlan_tag], ['VLAN 边界丢弃', a.vlan_ingress + a.vlan_egress], ['FFU 丢弃', a.ffu], ['Policer 丢弃', d.policer], ['TTL 丢弃', tx.ttl_drops + d.ttl], ['Trigger 丢弃', a.trigger], ['链路 UP 事件', m.link_events.up], ['本地故障事件', m.link_events.local_fault], ['远端故障事件', m.link_events.remote_fault]]);
}

function hasTextSelection() {
  const selection = window.getSelection();
  return Boolean(selection && !selection.isCollapsed && String(selection).trim());
}

function renderTelemetry(data) {
  if (hasTextSelection()) {
    ui.pendingTelemetry = data;
    return;
  }
  ui.pendingTelemetry = null;
  ui.telemetry = data;
  if (data.port_status && data.port_status.state === 'ready') {
    ui.live = data.port_status.ports;
    renderPortLinks();
    renderPortStatistics();
  }
  const identity = data.hardware_identity || {};
  $('#hardware-model').textContent = identity.display_model || identity.model || '未知';
  $('#hardware-version').textContent = [identity.vpd_version, identity.hardware_family].filter(Boolean).join(' · ') || '未知';
  $('#hardware-platform').textContent = [identity.platform, identity.hw_version === null || identity.hw_version === undefined ? null : `hw_version ${identity.hw_version}`].filter(Boolean).join(' · ') || '未知';
  $('#hardware-serial').textContent = identity.serial || '未知';
  $('#system-device-model').textContent = identity.display_model || identity.model || '未知';
  $('#system-device-version').textContent = [identity.vpd_version, identity.hardware_family].filter(Boolean).join(' · ') || '未知';
  $('#system-device-platform').textContent = [identity.platform, identity.hw_version === null || identity.hw_version === undefined ? null : `hw_version ${identity.hw_version}`].filter(Boolean).join(' · ') || '未知';
  $('#system-device-serial').textContent = identity.serial || '未知';
  $('#system-device-cpu').textContent = data.cpu.model || '未知';
  $('#hostname').textContent = data.hostname;
  $('#cpu-usage').textContent = data.cpu.usage_percent === null ? '采样中' : `${data.cpu.usage_percent}%`;
  $('#cpu-note').textContent = `${data.cpu.cores} 核`;
  $('#cpu-model').textContent = data.cpu.model;
  $('#system-cpu-usage').textContent = data.cpu.usage_percent === null ? '采样中' : `${data.cpu.usage_percent}%`;
  $('#cpu-load').textContent = data.cpu.load.join(' / ');
  $('#kernel').textContent = data.kernel;
  $('#uptime').textContent = formatUptime(data.uptime_seconds);
  $('#system-info-uptime').textContent = formatUptime(data.uptime_seconds);
  $('#memory-usage').textContent = `${data.memory.usage_percent}%`;
  $('#memory-note').textContent = `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}`;

  const temperatureGroups = {
    'cpu-core': $('#cpu-core-sensors'),
    soc: $('#soc-sensors'),
    acpi: $('#acpi-sensors'),
    board: $('#board-sensors'),
  };
  Object.values(temperatureGroups).forEach((root) => root.replaceChildren());
  Object.entries(temperatureGroups).forEach(([category, root]) => {
    const items = data.temperatures.filter((item) => item.category === category);
    if (!items.length && category !== 'board') root.append(Object.assign(document.createElement('span'), { className: 'muted', textContent: '未发现该类传感器' }));
    items.forEach((item) => root.append(sensorBox(item.display_label || item.label || item.chip, item.celsius, '°C', 1)));
  });
  const boardSensors = data.temperatures.filter((item) => item.category === 'board');
  $('#board-sensor-category').hidden = boardSensors.length === 0;
  const cpuTemperatures = data.temperatures.filter((item) => item.category === 'cpu-core');
  const cpuMaximum = cpuTemperatures.length ? Math.max(...cpuTemperatures.map((item) => item.celsius)) : null;
  $('#overview-cpu-temp').textContent = cpuMaximum === null ? '—' : `${cpuMaximum.toFixed(1)}°C`;
  $('#overview-cpu-temp').parentElement.classList.toggle('warning', cpuMaximum !== null && cpuMaximum >= 85 && cpuMaximum < 100);
  $('#overview-cpu-temp').parentElement.classList.toggle('danger', cpuMaximum !== null && cpuMaximum >= 100);

  const net = data.management || { interfaces: [], connected: 0, total: 0 }; const interfaces = net.interfaces || [];
  $('#mgmt-state').textContent = `${net.connected}/${net.total} UP`;
  const primary = interfaces.find((item) => item.interface === net.primary) || interfaces.find((item) => item.carrier) || interfaces[0];
  $('#mgmt-ip').textContent = primary ? (primary.ipv4[0] || primary.interface) : '未发现管理口';
  const managementGrid = $('#management-grid'); managementGrid.replaceChildren();
  if (!interfaces.length) managementGrid.append(Object.assign(document.createElement('span'), { className: 'muted', textContent: '未发现板载 I211 管理口' }));
  interfaces.forEach((item) => managementGrid.append(managementCard(item)));

  const traffic = data.port_status && data.port_status.traffic;
  if (traffic) {
    $('#switch-rx-rate').textContent = formatRate(traffic.rx_bps); $('#switch-tx-rate').textContent = formatRate(traffic.tx_bps);
    $('#switch-rx-total').textContent = `累计 ${formatBytes(traffic.rx_bytes)} · ${formatCount(traffic.rx_frames)} 帧 · ${formatCount(traffic.rx_errors)} 错误`;
    $('#switch-tx-total').textContent = `累计 ${formatBytes(traffic.tx_bytes)} · ${formatCount(traffic.tx_frames)} 帧 · ${formatCount(traffic.tx_discards)} 丢弃`;
    $('#switch-traffic-source').textContent = `${traffic.port_count} 个端口`;
    const forwardingRate = Math.max(Number(traffic.rx_bps) || 0, Number(traffic.tx_bps) || 0);
    const forwardingCapacity = ((ui.state && ui.state.budget && ui.state.budget.guaranteed) || 600000) * 1000000;
    $('#switch-load').textContent = loadPercent(forwardingRate, forwardingCapacity);
    $('#switch-load-note').textContent = `${formatBitRate(forwardingRate)} / 600 Gbit/s`;
  }
  renderActivePorts(data.port_status);

  const sdk = data.switch_sensors; const switchGrid = $('#switch-sensors'); switchGrid.replaceChildren(); $('#voltage-sensors').replaceChildren();
  if (sdk.state === 'ready') {
    const switchPoints = sdk.temperatures.filter((item) => item.category === 'switch');
    const summaryPoints = switchPoints.length ? switchPoints : sdk.temperatures;
    const maximum = Math.max(...summaryPoints.map((item) => item.celsius));
    $('#overview-switch-temp').textContent = `${maximum.toFixed(1)}°C`;
    $('#cooling-switch-temp').textContent = `${maximum.toFixed(1)}°C`;
    $('#overview-switch-temp').parentElement.classList.toggle('warning', maximum >= 85 && maximum < 100);
    $('#overview-switch-temp').parentElement.classList.toggle('danger', maximum >= 100);
    $('#sensor-age').textContent = new Date(sdk.sampled * 1000).toLocaleTimeString();
    sdk.temperatures.forEach((item) => {
      const index = item.sensor_index === undefined ? '?' : item.sensor_index;
      const location = item.documented === false ? `TEMPERATURE[${index}]` : item.location;
      switchGrid.append(sensorBox(location || item.name.replace(' TEMP SENSOR', ''), item.celsius, '°C', 1));
    });
    sdk.voltages.forEach((item) => $('#voltage-sensors').append(sensorBox(item.name.replace('VOLTAGE SENSOR ', ''), item.volts, ' V', 3)));
  } else {
    $('#overview-switch-temp').textContent = sdk.state === 'error' ? '读取失败' : '读取中';
    $('#cooling-switch-temp').textContent = sdk.state === 'error' ? '读取失败' : '读取中';
    $('#sensor-age').textContent = sdk.state === 'error' ? 'SDK 错误' : '等待 SDK';
    const message = document.createElement('span'); message.className = 'muted'; message.textContent = sdk.error || '读取中…'; switchGrid.append(message);
  }

  const fanData = data.fans || { state: 'not-detected', fans: [] };
  const fan = fanData.fans[0];
  $('#cooling-fan-rpm').textContent = fan ? `${Math.round(fan.rpm)} RPM` : '未检测到';

}

async function loadTelemetry() {
  try { renderTelemetry(await api('/api/telemetry')); } catch (error) { console.warn(error); }
}

function setTelemetryInterval(seconds) {
  if (ui.telemetryTimer) window.clearInterval(ui.telemetryTimer);
  ui.telemetryTimer = window.setInterval(loadTelemetry, seconds * 1000);
}

function updateLinkBadge(badge, logical) {
  const endpoint = logical === null || !ui.state ? null : ui.state.endpoints.find((item) => item.logical === logical);
  const live = logical === null ? null : ui.live[String(logical)];
  badge.className = 'link'; badge.textContent = '未读取'; badge.title = '';
  if (endpoint && endpoint.enabled === false) {
    badge.className = 'link off'; badge.textContent = 'OFF'; badge.title = '端口已由用户关闭'; return;
  }
  if (!live) return;
  if (live.oper === 'DOWN' && live.fault === 'local' && !live.rx_link_up) {
    badge.className = 'link';
    badge.textContent = 'NO SIGNAL';
    badge.title = `无有效接收信号 · EPL ${live.epl} · Lane ${Number(live.lane)} · PCS ${live.pcs} · ${live.raw || ''}`;
    return;
  }
  const details = [];
  if (live.fault === 'local') details.push('LOCAL FAULT');
  if (live.fault === 'remote') details.push('REMOTE FAULT');
  if (live.high_ber) details.push('HIGH BER');
  badge.className = `link ${live.oper === 'UP' && !details.length ? 'up' : 'warn'}`;
  badge.textContent = [live.oper, ...details].join(' · ');
  badge.title = `EPL ${live.epl} · Lane ${Number(live.lane)} · RxLinkUp ${live.rx_link_up ? '1' : '0'} · PCS ${live.pcs} · ${live.raw || ''}`;
}

function linkBadge(logical) {
  const badge = document.createElement('span');
  badge.dataset.logical = logical === null ? '' : String(logical);
  updateLinkBadge(badge, logical);
  return badge;
}

function renderPortLinks() {
  document.querySelectorAll('#page-ports .link[data-logical]').forEach((badge) => {
    updateLinkBadge(badge, badge.dataset.logical === '' ? null : Number(badge.dataset.logical));
  });
}

function makeSelect(values, selected, onChange) {
  const select = document.createElement('select');
  values.forEach((value) => { const option = document.createElement('option'); option.value = value; option.textContent = speedLabel(value); option.selected = value === selected; select.append(option); });
  select.addEventListener('change', () => onChange(Number(select.value))); return select;
}

function adminSwitch(enabled, label, onClick, disabled = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'toggle-switch';
  button.setAttribute('role', 'switch');
  button.setAttribute('aria-checked', String(enabled));
  button.setAttribute('aria-label', label);
  button.disabled = disabled || ui.busy;
  button.append(document.createElement('i'));
  button.onclick = disabled ? null : onClick;
  return button;
}

function currentEndpoint(group, lane) {
  if (!ui.state || group.layout !== ui.topology[group.key].layout) return null;
  return ui.state.endpoints.find((item) => item.group === group.key && item.lane === lane) || null;
}

function inheritedGroupAdmin(group) {
  const endpoints = ui.state.endpoints.filter((item) => item.group === group.key);
  return endpoints.length === 0 || endpoints.some((item) => item.enabled);
}

function draftEndpointBadge(endpoint) {
  if (endpoint) return linkBadge(endpoint.logical);
  const badge = linkBadge(null);
  badge.textContent = '待应用';
  badge.title = '应用拓扑后可操作';
  return badge;
}

function endpointRow(group, nameText, endpoint, select) {
  const enabled = endpoint ? endpoint.enabled : inheritedGroupAdmin(group);
  const row = document.createElement('div');
  row.className = 'lane-row';
  const name = document.createElement('span');
  const configured = endpoint && ui.l2 && ui.l2.endpoints.find((item) => item.key === endpoint.key);
  name.textContent = configured && configured.name ? configured.name : nameText;
  if (configured && configured.name) name.title = nameText;
  const control = adminSwitch(
    enabled,
    `${nameText} 开关`,
    endpoint ? () => togglePort(endpoint.key, !endpoint.enabled) : null,
    !endpoint,
  );
  row.append(name, draftEndpointBadge(endpoint), select, control);
  return row;
}

function renderGroup(group) {
  const draft = ui.topology[group.key];
  const card = document.createElement('article');
  card.className = 'group-card';
  const head = document.createElement('div');
  head.className = 'group-head';
  const title = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = `第 ${group.position} 组`;
  const small = document.createElement('small');
  small.textContent = `EPL ${group.epl}`;
  title.append(strong, small);
  head.append(title);
  card.append(head);

  const modes = document.createElement('div');
  modes.className = 'segmented';
  [['split', '4× 拆分'], ['bonded', '4× 聚合']].forEach(([value, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.classList.toggle('active', draft.layout === value);
    button.onclick = () => {
      ui.topology[group.key] = value === 'split'
        ? { layout: 'split', speeds: [25000, 25000, 25000, 25000] }
        : { layout: 'bonded', speed: 100000 };
      renderPorts();
    };
    modes.append(button);
  });
  card.append(modes);

  const list = document.createElement('div');
  list.className = 'lane-list';
  if (draft.layout === 'split') {
    draft.speeds.forEach((speed, lane) => {
      const select = makeSelect([25000, 10000], speed, (value) => {
        draft.speeds[lane] = value;
        renderPorts();
      });
      list.append(endpointRow(group, `Lane ${lane}`, currentEndpoint(group, lane), select));
    });
  } else {
    const select = makeSelect([100000, 40000], draft.speed, (value) => {
      draft.speed = value;
      renderPorts();
    });
    list.append(endpointRow(group, '聚合端口', currentEndpoint(group, null), select));
  }
  card.append(list);
  return card;
}

function renderMpoAdmin(mpo) {
  const summary = ui.state.mpo_admin[String(mpo)]; const button = $(`#mpo${mpo}-admin`); const label = $(`#mpo${mpo}-admin-label`);
  button.setAttribute('aria-checked', String(summary.enabled)); button.disabled = ui.busy;
  label.textContent = summary.enabled ? '已开启' : summary.enabled_count === 0 ? '已关闭' : `${summary.enabled_count}/${summary.total} 启用`;
}

function renderPorts() {
  if (!ui.state) return;
  closeCustomSelects();
  destroySelects($('#mpo1-grid')); destroySelects($('#mpo2-grid'));
  $('#mpo1-grid').replaceChildren(...ui.state.groups.filter((group) => group.mpo === 1).map(renderGroup));
  $('#mpo2-grid').replaceChildren(...ui.state.groups.filter((group) => group.mpo === 2).map(renderGroup));
  renderMpoAdmin(1); renderMpoAdmin(2);
  enhanceSelects($('#page-ports'));
  let external = 0; let count = 0;
  Object.values(ui.topology).forEach((item) => { if (item.layout === 'bonded') { external += item.speed; count += 1; } else { external += item.speeds.reduce((a, b) => a + b, 0); count += 4; } });
  const total = external + ui.state.budget.internal; const changes = ui.state.groups.filter((group) => !sameChoice(ui.topology[group.key], choiceFor(group))).length;
  $('#port-count').textContent = count; $('#external-budget').textContent = speedLabel(external); $('#total-budget').textContent = speedLabel(total);
  $('#budget-label').textContent = `${speedLabel(total)} / 600G`; $('#budget-bar').style.width = `${Math.min(100, total / ui.state.budget.guaranteed * 100)}%`;
  $('#topology-change-count').textContent = changes ? `${changes} 个 EPL 待应用` : '无待应用修改'; $('#topology-disruption-hint').hidden = !changes; $('#apply-topology').disabled = !changes || ui.busy;
  if (ui.topologyPreview) closeTopologyPreview();
  $('#topology-apply-row').hidden = Boolean(ui.topologyPreview);
}

function renderStatsPortSelect() {
  if (!ui.state) return;
  const select = $('#stats-port'); const previous = select.value; select.replaceChildren();
  ui.state.endpoints.forEach((endpoint) => { const option = document.createElement('option'); option.value = endpoint.logical; option.textContent = `端口 ${endpoint.logical} · ${endpoint.label} · ${speedLabel(endpoint.speed)}`; option.selected = String(endpoint.logical) === previous; select.append(option); });
  syncSelect(select);
  renderPortStatistics();
}

function vlanNative(key) { const found = ui.vlans.find((vlan) => vlan.untagged.includes(key)); return found ? found.id : null; }
function vlanPortProfile(key) {
  const native = vlanNative(key); const tagged = ui.vlans.filter((vlan) => vlan.tagged.includes(key)).map((vlan) => vlan.id).sort((a, b) => a - b);
  return { native, tagged, mode: native === null ? 'trunk' : tagged.length ? 'hybrid' : 'access' };
}
function normalizedVlans(value) {
  return value.map((vlan) => ({ id: vlan.id, name: vlan.name, mtu: Number(vlan.mtu || 1536), tagged: [...vlan.tagged].sort(), untagged: [...vlan.untagged].sort() })).sort((a, b) => a.id - b.id);
}
function vlanChanged() { return JSON.stringify(normalizedVlans(ui.vlans)) !== JSON.stringify(normalizedVlans(ui.state.vlans)); }

function vlanPreviewRows(before, after) {
  const previous = new Map(before.map((vlan) => [vlan.id, vlan]));
  const next = new Map(after.map((vlan) => [vlan.id, vlan]));
  return [...new Set([...previous.keys(), ...next.keys()])].sort((a, b) => a - b).flatMap((id) => {
    const oldVlan = previous.get(id); const newVlan = next.get(id);
    if (!oldVlan) return [{ title: `VLAN ${id} · ${newVlan.name}`, detail: '新增' }];
    if (!newVlan) return [{ title: `VLAN ${id} · ${oldVlan.name}`, detail: '删除' }];
    const details = [];
    if (oldVlan.name !== newVlan.name) details.push(`${oldVlan.name} → ${newVlan.name}`);
    if (JSON.stringify(oldVlan.untagged) !== JSON.stringify(newVlan.untagged)) details.push(`Native ${oldVlan.untagged.length} → ${newVlan.untagged.length}`);
    if (JSON.stringify(oldVlan.tagged) !== JSON.stringify(newVlan.tagged)) details.push(`Tagged ${oldVlan.tagged.length} → ${newVlan.tagged.length}`);
    return details.length ? [{ title: `VLAN ${id} · ${newVlan.name}`, detail: details.join(' · ') }] : [];
  });
}

function closeTopologyPreview() {
  ui.topologyPreview = null;
  $('#topology-preview').hidden = true;
  $('#topology-apply-row').hidden = false;
}

function closeVlanPreview() {
  ui.vlanPreview = null;
  $('#vlan-preview').hidden = true;
  $('#vlan-apply-row').hidden = false;
}

function removePortFromVlans(key) {
  ui.vlans.forEach((vlan) => { vlan.untagged = vlan.untagged.filter((value) => value !== key); vlan.tagged = vlan.tagged.filter((value) => value !== key); });
}

function addPortMembership(key, id, type) {
  const vlan = ui.vlans.find((item) => item.id === id); if (vlan && !vlan[type].includes(key)) vlan[type].push(key);
}

function setVlanPortMode(key, mode) {
  const profile = vlanPortProfile(key); const fallback = profile.native ?? profile.tagged[0] ?? 1;
  if (mode === 'hybrid' && ui.vlans.length < 2) { showToast('混合模式至少需要两个 VLAN'); renderVlans(); return; }
  removePortFromVlans(key);
  if (mode === 'access') addPortMembership(key, fallback, 'untagged');
  if (mode === 'trunk') (profile.tagged.length ? profile.tagged : [fallback]).forEach((id) => addPortMembership(key, id, 'tagged'));
  if (mode === 'hybrid') {
    addPortMembership(key, fallback, 'untagged');
    const tagged = profile.tagged.filter((id) => id !== fallback);
    if (!tagged.length) tagged.push(ui.vlans.find((vlan) => vlan.id !== fallback).id);
    tagged.forEach((id) => addPortMembership(key, id, 'tagged'));
  }
  renderVlans();
}

function setNativeVlan(key, id) {
  const profile = vlanPortProfile(key); const oldNative = profile.native;
  ui.vlans.forEach((vlan) => { vlan.untagged = vlan.untagged.filter((value) => value !== key); });
  const target = ui.vlans.find((vlan) => vlan.id === id); target.tagged = target.tagged.filter((value) => value !== key); target.untagged.push(key);
  if (profile.mode === 'hybrid' && profile.tagged.includes(id) && oldNative !== null && oldNative !== id) addPortMembership(key, oldNative, 'tagged');
  renderVlans();
}

function openTaggedVlanModal(key) {
  const profile = vlanPortProfile(key); if (profile.mode === 'access') return;
  ui.vlanTaggedPort = key;
  const endpoint = ui.state.endpoints.find((item) => item.key === key);
  $('#vlan-tagged-title').textContent = `${endpoint.label} · Tagged VLAN`;
  const list = $('#vlan-tagged-list'); list.replaceChildren();
  [...ui.vlans].sort((a, b) => a.id - b.id).forEach((vlan) => {
    if (profile.mode === 'hybrid' && vlan.id === profile.native) return;
    const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'vlan-check'; check.value = vlan.id; check.checked = profile.tagged.includes(vlan.id);
    const text = document.createElement('span'); const name = document.createElement('strong'); name.textContent = `VLAN ${vlan.id}`; const description = document.createElement('small'); description.textContent = vlan.name; text.append(name, description); label.append(check, text); list.append(label);
  });
  $('#vlan-tagged-modal').hidden = false;
}

function closeTaggedVlanModal() { ui.vlanTaggedPort = null; $('#vlan-tagged-modal').hidden = true; }

function openAddVlanModal() {
  if (ui.busy) return;
  $('#add-vlan-form').reset();
  $('#add-vlan-modal').hidden = false;
  window.setTimeout(() => $('#new-vlan-id').focus(), 0);
}

function closeAddVlanModal() { $('#add-vlan-modal').hidden = true; }

function saveTaggedVlans() {
  const key = ui.vlanTaggedPort; if (!key) return;
  const selected = [...$('#vlan-tagged-list').querySelectorAll('input:checked')].map((item) => Number(item.value));
  if (!selected.length) { showToast('中继和混合模式至少需要一个 Tagged VLAN'); return; }
  ui.vlans.forEach((vlan) => { vlan.tagged = vlan.tagged.filter((value) => value !== key); if (selected.includes(vlan.id)) vlan.tagged.push(key); });
  closeTaggedVlanModal(); renderVlans();
}

function renderVlans() {
  if (!ui.state) return;
  if (ui.vlanPreview && JSON.stringify(normalizedVlans(ui.vlans)) !== JSON.stringify(ui.vlanPreview)) closeVlanPreview();
  closeCustomSelects();
  destroySelects($('#vlan-body'));
  const vlans = [...ui.vlans].sort((a, b) => a.id - b.id);
  const head = $('#vlan-head'); const header = document.createElement('tr'); ['物理端口', '逻辑端口', '模式', 'Native VLAN', 'Tagged VLAN'].forEach((label) => { const th = document.createElement('th'); th.textContent = label; header.append(th); }); head.replaceChildren(header);
  const body = $('#vlan-body'); body.replaceChildren();
  let previousGroup = null;
  ui.state.endpoints.forEach((endpoint) => {
    const row = document.createElement('tr'); row.classList.toggle('group-start', previousGroup !== null && previousGroup !== endpoint.group); previousGroup = endpoint.group; const port = document.createElement('td'); port.className = 'port-cell'; const title = document.createElement('strong'); const configured = ui.l2 && ui.l2.endpoints.find((item) => item.key === endpoint.key); title.textContent = configured && configured.name ? `${configured.name} · ${endpoint.label}` : endpoint.label; const key = document.createElement('small'); key.textContent = endpoint.key; port.append(title, key);
    const profile = vlanPortProfile(endpoint.key); const logical = document.createElement('td'); logical.textContent = `端口 ${endpoint.logical}`;
    const modeCell = document.createElement('td'); const mode = document.createElement('select'); mode.setAttribute('aria-label', `${endpoint.label} VLAN 模式`); [['access', 'Access'], ['trunk', 'Trunk'], ['hybrid', 'Hybrid']].forEach(([value, label]) => { const option = document.createElement('option'); option.value = value; option.textContent = label; option.selected = profile.mode === value; mode.append(option); }); mode.onchange = () => setVlanPortMode(endpoint.key, mode.value); modeCell.append(mode);
    const nativeCell = document.createElement('td'); if (profile.mode === 'trunk') { const none = document.createElement('span'); none.className = 'muted'; none.textContent = '无'; nativeCell.append(none); } else { const native = document.createElement('select'); native.setAttribute('aria-label', `${endpoint.label} Native VLAN`); vlans.forEach((vlan) => { const option = document.createElement('option'); option.value = vlan.id; option.textContent = `${vlan.id} · ${vlan.name}`; option.selected = profile.native === vlan.id; native.append(option); }); native.onchange = () => setNativeVlan(endpoint.key, Number(native.value)); nativeCell.append(native); }
    const taggedCell = document.createElement('td'); const taggedButton = document.createElement('button'); taggedButton.type = 'button'; taggedButton.className = 'button vlan-tagged-button'; taggedButton.disabled = profile.mode === 'access'; taggedButton.textContent = profile.mode === 'access' ? '无' : profile.tagged.length === 1 ? `VLAN ${profile.tagged[0]}` : `已选 ${profile.tagged.length} 个`; taggedButton.onclick = () => openTaggedVlanModal(endpoint.key); taggedCell.append(taggedButton);
    row.append(port, logical, modeCell, nativeCell, taggedCell); body.append(row);
  });
  const list = $('#vlan-list'); list.replaceChildren();
  vlans.forEach((vlan) => { const item = document.createElement('div'); item.className = 'vlan-item'; const id = document.createElement('span'); id.className = 'vlan-id'; id.textContent = vlan.id; const name = document.createElement('strong'); name.textContent = vlan.name; const access = document.createElement('small'); access.textContent = vlan.untagged.length; const tagged = document.createElement('small'); tagged.textContent = vlan.tagged.length; const action = document.createElement('div'); if (vlan.id !== 1) { const button = document.createElement('button'); button.type = 'button'; button.className = 'button danger'; button.textContent = '删除'; button.onclick = () => deleteVlan(vlan.id); action.append(button); } item.append(id, name, access, tagged, action); list.append(item); });
  const changed = vlanChanged(); $('#vlan-change-count').textContent = changed ? 'VLAN 待应用' : '无待应用修改'; $('#apply-vlans').disabled = !changed || ui.busy; $('#vlan-apply-row').hidden = Boolean(ui.vlanPreview);
  enhanceSelects(body);
}

function setSwitch(button, enabled) {
  button.setAttribute('aria-checked', String(Boolean(enabled)));
}

function endpointName(endpoint) {
  return endpoint.name ? `${endpoint.name} · ${endpoint.label}` : endpoint.label;
}

function renderNeighbors(value) {
  const root = $('#lldp-neighbors');
  const neighbors = (value && value.neighbors) || [];
  root.className = neighbors.length ? 'neighbor-table' : 'diagnostic-empty';
  root.replaceChildren();
  if (!neighbors.length) {
    root.textContent = value && value.state === 'error' ? `LLDP 监听不可用：${value.error}` : '尚未发现支持 LLDP 的设备';
    return;
  }
  const table = document.createElement('table');
  table.innerHTML = '<thead><tr><th>设备</th><th>设备端口</th><th>管理地址</th><th>本机端口</th><th>最后发现</th></tr></thead>';
  const body = document.createElement('tbody');
  neighbors.forEach((neighbor) => {
    const endpoint = ui.l2.endpoints.find((item) => item.key === neighbor.endpoint);
    const row = document.createElement('tr');
    [neighbor.system_name || neighbor.chassis || neighbor.source_mac, neighbor.port || '—', neighbor.management_address || '—', endpoint ? endpointName(endpoint) : '未识别', new Date(neighbor.last_seen * 1000).toLocaleTimeString()].forEach((text) => { const cell = document.createElement('td'); cell.textContent = text; row.append(cell); });
    body.append(row);
  });
  table.append(body); root.append(table);
}

function renderL2() {
  if (!ui.l2) return;
  setSwitch($('#loop-protection-toggle'), ui.l2.loop_protection.enabled);
  setSwitch($('#storm-control-toggle'), ui.l2.storm_control.enabled);
  setSwitch($('#mirror-toggle'), ui.l2.mirror.enabled);
  $('#loop-protection-pps').value = ui.l2.loop_protection.broadcast_pps;
  $('#storm-control-rate').value = ui.l2.storm_control.rate_kbps;
  const options = ui.l2.endpoints.map((endpoint) => `<option value="${endpoint.key}">${escapeHtml(endpointName(endpoint))}</option>`).join('');
  $('#mirror-source').innerHTML = `<option value="">请选择</option>${options}`;
  $('#mirror-destination').innerHTML = `<option value="">请选择</option>${options}`;
  $('#mirror-source').value = ui.l2.mirror.source || '';
  $('#mirror-destination').value = ui.l2.mirror.destination || '';
  $('#mirror-direction').value = ui.l2.mirror.direction || 'both';
  syncSelect($('#mirror-source')); syncSelect($('#mirror-destination')); syncSelect($('#mirror-direction'));
  const labels = $('#port-label-list'); labels.replaceChildren();
  ui.l2.endpoints.forEach((endpoint) => {
    const item = document.createElement('label'); item.className = 'port-label-item';
    const name = document.createElement('span'); const title = document.createElement('strong'); title.textContent = endpoint.label; const detail = document.createElement('small'); detail.textContent = `逻辑端口 ${endpoint.logical}`; name.append(title, detail);
    const input = document.createElement('input'); input.maxLength = 32; input.placeholder = '可选名称'; input.value = ui.l2.labels[endpoint.key] || ''; input.addEventListener('input', () => { ui.l2.labels[endpoint.key] = input.value; endpoint.name = input.value.trim(); });
    item.append(name, input); labels.append(item);
  });
  renderNeighbors(ui.l2.neighbors);
}

function readL2Draft() {
  ui.l2.loop_protection.enabled = $('#loop-protection-toggle').getAttribute('aria-checked') === 'true';
  ui.l2.loop_protection.broadcast_pps = Number($('#loop-protection-pps').value);
  ui.l2.storm_control.enabled = $('#storm-control-toggle').getAttribute('aria-checked') === 'true';
  ui.l2.storm_control.rate_kbps = Number($('#storm-control-rate').value);
  ui.l2.mirror.enabled = $('#mirror-toggle').getAttribute('aria-checked') === 'true';
  ui.l2.mirror.source = $('#mirror-source').value || null;
  ui.l2.mirror.destination = $('#mirror-destination').value || null;
  ui.l2.mirror.direction = $('#mirror-direction').value;
}

async function applyL2() {
  readL2Draft();
  if (ui.l2.mirror.enabled && (!ui.l2.mirror.source || !ui.l2.mirror.destination || ui.l2.mirror.source === ui.l2.mirror.destination)) return showToast('请选择不同的镜像源端口和监控端口');
  const value = clone(ui.l2); delete value.endpoints; delete value.neighbors;
  await runOperation(() => api('/api/l2/apply', { method: 'POST', body: JSON.stringify(value) }), '应用网络功能');
}

async function applyPortLabels() {
  const value = clone(ui.l2Saved || ui.l2);
  value.labels = Object.fromEntries(Object.entries(ui.l2.labels || {}).map(([key, label]) => [key, String(label).trim()]));
  delete value.endpoints;
  delete value.neighbors;
  await runOperation(() => api('/api/l2/apply', { method: 'POST', body: JSON.stringify(value) }), '保存端口名称');
}

async function refreshNeighbors() {
  try {
    const result = await pollJob(await api('/api/l2/neighbors/refresh', { method: 'POST', body: '{}' }), '识别邻居设备');
    ui.l2.neighbors = result.neighbors; renderNeighbors(result.neighbors); showToast(result.message, 'success');
  } catch (error) { showToast(error.message); }
}

function deleteVlan(id) {
  const fallback = ui.vlans.find((vlan) => vlan.id === 1); const target = ui.vlans.find((vlan) => vlan.id === id);
  const affected = new Set([...target.untagged, ...target.tagged]); target.untagged.forEach((key) => { if (!fallback.untagged.includes(key)) fallback.untagged.push(key); }); ui.vlans = ui.vlans.filter((vlan) => vlan.id !== id);
  affected.forEach((key) => { if (!ui.vlans.some((vlan) => vlan.untagged.includes(key) || vlan.tagged.includes(key))) fallback.untagged.push(key); }); renderVlans();
}

function showJob(message) { $('#dialog-title').textContent = message; $('#dialog').hidden = false; }
function hideJob() { $('#dialog').hidden = true; }

function setBusy(value) {
  ui.busy = value;
  $('#open-add-vlan').disabled = value;
  $('#apply-port-labels').disabled = value;
  $('#config-export').disabled = value;
  $('#config-import-file').disabled = value;
  $('#config-import').disabled = value || !ui.importedConfig;
  renderPorts();
  renderVlans();
  renderFanCurve();
}

function renderFanCurve() {
  if (!ui.fan) return;
  $('#fan-idle-temperature').value = ui.fan.idle_temperature_c;
  $('#fan-load-temperature').value = ui.fan.load_temperature_c;
  $('#fan-critical-temperature').value = ui.fan.critical_temperature_c;
  $('#fan-idle-speed').value = ui.fan.idle_speed_percent;
  $('#fan-load-speed').value = ui.fan.load_speed_percent;
  $('#fan-response-time').value = String(ui.fan.response_time_s);
  syncSelect($('#fan-response-time'));
  $('#fan-preview-idle').textContent = `${ui.fan.idle_temperature_c}°C · ${ui.fan.idle_speed_percent}%`;
  $('#fan-preview-load').textContent = `${ui.fan.load_temperature_c}°C · ${ui.fan.load_speed_percent}%`;
  $('#fan-preview-critical').textContent = `${ui.fan.critical_temperature_c}°C · 100%`;
  $('#fan-apply').disabled = ui.busy;
}

function updateFanDraft() {
  if (!ui.fan) return;
  ui.fan.idle_temperature_c = Number($('#fan-idle-temperature').value);
  ui.fan.load_temperature_c = Number($('#fan-load-temperature').value);
  ui.fan.critical_temperature_c = Number($('#fan-critical-temperature').value);
  ui.fan.idle_speed_percent = Number($('#fan-idle-speed').value);
  ui.fan.load_speed_percent = Number($('#fan-load-speed').value);
  ui.fan.response_time_s = Number($('#fan-response-time').value);
  $('#fan-preview-idle').textContent = `${ui.fan.idle_temperature_c}°C · ${ui.fan.idle_speed_percent}%`;
  $('#fan-preview-load').textContent = `${ui.fan.load_temperature_c}°C · ${ui.fan.load_speed_percent}%`;
  $('#fan-preview-critical').textContent = `${ui.fan.critical_temperature_c}°C · 100%`;
}

async function applyFanCurve(event) {
  event.preventDefault();
  if (!event.currentTarget.checkValidity()) return showToast('请检查风扇曲线参数');
  updateFanDraft();
  const value = { sensor: 'fm10840_core', idle_temperature_c: ui.fan.idle_temperature_c, load_temperature_c: ui.fan.load_temperature_c, critical_temperature_c: ui.fan.critical_temperature_c, idle_speed_percent: ui.fan.idle_speed_percent, load_speed_percent: ui.fan.load_speed_percent, response_time_s: ui.fan.response_time_s, hysteresis_c: 4 };
  await runOperation(() => api('/api/fan/apply', { method: 'POST', body: JSON.stringify(value) }), '应用风扇曲线');
}

async function pollJob(job, fallback) {
  setBusy(true);
  showJob(job.message || fallback);
  try {
    return await waitForJob(api, job, (progress) => showJob(progress.message || fallback));
  } finally {
    hideJob();
    setBusy(false);
  }
}

async function runOperation(request, fallback, resetDraft = false) {
  try {
    const current = await pollJob(await request(), fallback);
    if (current.ports) ui.live = current.ports;
    showToast(current.message, 'success');
    await loadState(resetDraft);
    await loadTelemetry();
  } catch (error) {
    showToast(error.message);
  }
}

async function applyTopology() {
  try {
    const preview = await api('/api/topology/preview', { method: 'POST', body: JSON.stringify({ groups: ui.topology }) });
    ui.topologyPreview = preview;
    $('#topology-preview-summary').textContent = `${preview.changes.length} 个 EPL · ${preview.affected_ports} 个现有端口受影响 · 新拓扑 ${preview.external_count} 个端口 · ${speedLabel(preview.total)}`;
    const list = $('#topology-preview-list'); list.replaceChildren();
    preview.changes.forEach((change) => { const row = document.createElement('div'); row.className = 'preview-row'; const title = document.createElement('strong'); title.textContent = `MPO24-${change.mpo} · EPL ${change.epl}`; const values = document.createElement('span'); values.textContent = `${change.before} → ${change.after}`; row.append(title, values); list.append(row); });
    $('#topology-preview-warning').textContent = preview.requires_restart
      ? '首次迁移固定端口模型需要重启交换服务，所有交换端口会中断约 40 秒。之后的修改可在线完成。'
      : '仅变更的 EPL 会短暂断链，其他 EPL 和交换服务保持运行。';
    $('#topology-preview-confirm').disabled = !preview.changes.length; $('#topology-preview').hidden = false; $('#topology-apply-row').hidden = true; $('#topology-preview').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) { showToast(error.message); }
}

async function confirmTopology() {
  if (!ui.topologyPreview) return;
  const accepted = ui.topologyPreview.total > ui.state.budget.guaranteed;
  closeTopologyPreview();
  await runOperation(
    () => api('/api/apply', { method: 'POST', body: JSON.stringify({ groups: ui.topology, accept_over_guaranteed: accepted }) }),
    '应用端口配置',
    true,
  );
}

async function applyVlans() {
  const snapshot = normalizedVlans(ui.vlans);
  const rows = vlanPreviewRows(normalizedVlans(ui.state.vlans), snapshot);
  if (!rows.length) return;
  ui.vlanPreview = snapshot;
  $('#vlan-preview-summary').textContent = `${rows.length} 个 VLAN 发生变更`;
  const list = $('#vlan-preview-list'); list.replaceChildren();
  rows.forEach((change) => { const row = document.createElement('div'); row.className = 'preview-row'; const title = document.createElement('strong'); title.textContent = change.title; const detail = document.createElement('span'); detail.textContent = change.detail; row.append(title, detail); list.append(row); });
  $('#vlan-preview').hidden = false;
  $('#vlan-apply-row').hidden = true;
  $('#vlan-preview').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function confirmVlans() {
  if (!ui.vlanPreview) return;
  const value = ui.vlanPreview;
  closeVlanPreview();
  await runOperation(() => api('/api/vlans/apply', { method: 'POST', body: JSON.stringify({ vlans: value }) }), '应用 VLAN');
}

async function refreshAll() {
  await runOperation(() => api('/api/refresh', { method: 'POST', body: '{}' }), '刷新硬件数据');
}

function closePortAdminModal() {
  $('#port-admin-modal').hidden = true;
  ui.pendingPortAdmin = null;
}

function confirmPortAdmin(action) {
  ui.pendingPortAdmin = action;
  if (action.key) {
    const endpoint = ui.state.endpoints.find((item) => item.key === action.key);
    const configured = ui.l2 && ui.l2.endpoints.find((item) => item.key === action.key);
    const label = configured && configured.name ? `${configured.name}（${endpoint.label}）` : endpoint.label;
    $('#port-admin-title').textContent = '关闭端口？';
    $('#port-admin-description').textContent = `关闭「${label}」后，该端口将停止转发并关闭对应光通道发光。`;
  } else {
    $('#port-admin-title').textContent = `关闭 MPO24-${action.mpo}？`;
    $('#port-admin-description').textContent = `将关闭 MPO24-${action.mpo} 下的全部端口，并关闭对应光通道发光。连接设备会立即断开。`;
  }
  $('#port-admin-modal').hidden = false;
}

async function applyPortAdmin(action) {
  const body = action.key ? { key: action.key, enabled: action.enabled } : { mpo: action.mpo, enabled: action.enabled };
  const target = action.key ? '端口' : `MPO24-${action.mpo}`;
  await runOperation(() => api('/api/ports/admin', { method: 'POST', body: JSON.stringify(body) }), `${action.enabled ? '开启' : '关闭'}${target}`);
}

async function submitPortAdmin() {
  const action = ui.pendingPortAdmin;
  if (!action) return;
  closePortAdminModal();
  await applyPortAdmin(action);
}

async function togglePort(key, enabled) {
  if (!enabled) return confirmPortAdmin({ key, enabled });
  await applyPortAdmin({ key, enabled });
}

async function toggleMpo(mpo) {
  const enabled = !ui.state.mpo_admin[String(mpo)].enabled;
  if (!enabled) return confirmPortAdmin({ mpo, enabled });
  await applyPortAdmin({ mpo, enabled });
}

function renderFdb(result) {
  const root = $('#fdb-result'); root.className = 'diagnostic-content'; root.replaceChildren();
  const meta = document.createElement('p'); meta.className = 'diagnostic-meta'; meta.textContent = `${result.count} 条记录`; root.append(meta);
  const wrap = document.createElement('div'); wrap.className = 'table-scroll'; const table = document.createElement('table'); table.className = 'diagnostic-table'; const head = document.createElement('thead'); head.innerHTML = '<tr><th>MAC</th><th>FID / VLAN</th><th>类型</th><th>目的地</th><th>模式</th></tr>'; const body = document.createElement('tbody');
  result.entries.forEach((entry) => { const row = document.createElement('tr'); [entry.mac, entry.fid, entry.destination_type, entry.destination, entry.mode].forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }); body.append(row); }); table.append(head, body); wrap.append(table); root.append(wrap);
}

function renderLaneDiagnostic(result) {
  const root = $('#lane-diagnostic-result'); root.className = 'diagnostic-content'; root.replaceChildren();
  const meta = document.createElement('p'); meta.className = 'diagnostic-meta'; meta.textContent = `端口 ${result.endpoint.logical} · ${result.port.speed} · ${result.port.state}`; root.append(meta);
  const grid = document.createElement('div'); grid.className = 'lane-diagnostic-grid'; result.lanes.forEach((lane) => { const card = document.createElement('article'); const title = document.createElement('strong'); title.textContent = `Lane ${lane.lane}`; const dl = document.createElement('dl'); const values = [['信号', lane.signal], ['PLL', lane.pll], ['DFE', `${lane.dfe_mode} · ${lane.coarse}/${lane.fine}`], ['眼高', lane.eye_height === null ? '不可用' : `${lane.eye_height} / 64`]]; if (lane.eye_width !== null) values.push(['眼宽', `${lane.eye_width} / 64`]); values.forEach(([name, value]) => { const dt = document.createElement('dt'); dt.textContent = name; const dd = document.createElement('dd'); dd.textContent = value; dl.append(dt, dd); }); card.append(title, dl); grid.append(card); }); root.append(grid);
}

function renderOpticsIdentity(section, identity) {
  if (!identity?.readable) return;
  const fields = [
    ['厂商', identity.vendor],
    ['型号', identity.part_number],
    ['序列号', identity.serial],
    ['生产日期', identity.date_code],
  ].filter(([, value]) => value);
  const list = document.createElement('dl');
  list.className = 'optics-identity';
  fields.forEach(([label, value]) => {
    const item = document.createElement('div');
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = label;
    detail.textContent = value;
    item.append(term, detail);
    list.append(item);
  });
  section.append(list);
}

function renderOptics(result) {
  const root = $('#optics-result');
  root.className = 'diagnostic-content';
  root.replaceChildren();
  result.modules.forEach((module) => {
    const section = document.createElement('section');
    section.className = 'optics-module';
    const head = document.createElement('div');
    const title = document.createElement('strong');
    const badge = document.createElement('span');
    title.textContent = `MPO24-${module.mpo}`;
    badge.className = `link ${module.state === 'ready' ? 'up' : 'off'}`;
    badge.textContent = module.state === 'ready' ? 'RX 功率可读' : '无 RX 功率';
    head.append(title, badge);
    section.append(head);
    renderOpticsIdentity(section, module.identity);
    if (module.state === 'ready') {
      const grid = document.createElement('div');
      grid.className = 'optics-channel-grid';
      module.channels.forEach((channel) => {
        const item = document.createElement('span');
        const label = document.createElement('small');
        const value = document.createElement('strong');
        label.textContent = `CH ${channel.channel}`;
        value.textContent = channel.dbm === null ? '—' : `${channel.dbm.toFixed(2)} dBm`;
        item.append(label, value);
        grid.append(item);
      });
      section.append(grid);
    }
    root.append(section);
  });
}

async function readDiagnostic(path, fallback, field, renderer, body = {}) {
  try {
    const job = await api(path, { method: 'POST', body: JSON.stringify(body) });
    const current = await pollJob(job, fallback);
    renderer(current[field]);
    showToast(current.message, 'success');
  } catch (error) {
    showToast(error.message);
  }
}

async function readLaneDiagnostic() {
  const logical = Number($('#stats-port').value);
  await readDiagnostic('/api/ports/diagnostics', `读取端口 ${logical} Lane`, 'lane_diagnostic', renderLaneDiagnostic, { logical });
}

async function logout() {
  try { await api('/api/logout', { method: 'POST', body: '{}' }); } catch (error) { console.warn(error); }
  window.location.replace('/login');
}

async function loadLogs(source = ui.logSource, { background = false } = {}) {
  const requestId = ++ui.logRequestId;
  const sourceChanged = source !== ui.logSource;
  ui.logSource = source;
  const labels = { system: '系统日志', kernel: '内核日志', switch: '交换服务' };
  document.querySelectorAll('[data-log-source]').forEach((button) => button.classList.toggle('active', button.dataset.logSource === source));
  $('#log-title').textContent = labels[source];
  if (!background) {
    $('#log-meta').textContent = '正在读取…';
    $('#log-refresh').disabled = true;
  }
  try {
    const value = await api(`/api/logs?source=${encodeURIComponent(source)}`);
    if (requestId !== ui.logRequestId) return;
    const content = $('#log-content');
    const follow = sourceChanged || !ui.logsLoaded || ui.logAutoFollow;
    const previousScrollTop = content.scrollTop;
    content.value = value.content || '没有可显示的日志。';
    window.requestAnimationFrame(() => {
      content.scrollTop = follow ? content.scrollHeight : previousScrollTop;
      if (follow) ui.logAutoFollow = true;
    });
    $('#log-meta').textContent = new Date(value.sampled * 1000).toLocaleTimeString('zh-CN', { hour12: false });
    ui.logsLoaded = true;
  } catch (error) {
    if (requestId !== ui.logRequestId) return;
    $('#log-content').value = `读取失败：${error.message}`;
    $('#log-meta').textContent = '读取失败';
  } finally {
    if (requestId === ui.logRequestId) $('#log-refresh').disabled = false;
  }
}

function setLogInterval(seconds = 3) {
  if (ui.logTimer) window.clearInterval(ui.logTimer);
  ui.logTimer = window.setInterval(() => {
    const content = $('#log-content');
    const selecting = document.activeElement === content && content.selectionStart !== content.selectionEnd;
    if ($('#page-logs').classList.contains('active') && document.visibilityState === 'visible' && !selecting) loadLogs(ui.logSource, { background: true });
  }, seconds * 1000);
}

async function saveAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.checkValidity()) return showToast('请检查账户信息');
  const button = $('#account-submit');
  const username = $('#account-username').value.trim();
  const currentPassword = $('#account-current-password').value;
  const newPassword = $('#account-new-password').value;
  const confirmPassword = $('#account-confirm-password').value;
  if (newPassword !== confirmPassword) return showToast('两次输入的新密码不一致');
  button.disabled = true;
  try {
    const result = await api('/api/account', { method: 'POST', body: JSON.stringify({ username, current_password: currentPassword, new_password: newPassword }) });
    form.reset();
    $('#account-username').value = result.username || username;
    if (result.reauthenticate) {
      window.location.replace('/login');
      return;
    }
    showToast(result.message || '账户设置已保存', 'success');
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function saveSystemSettings(event) {
  event.preventDefault();
  if (!event.currentTarget.checkValidity()) return showToast('请输入有效的主机名');
  const button = $('#system-settings-submit'); button.disabled = true;
  try {
    const value = await api('/api/system/settings', { method: 'POST', body: JSON.stringify({ hostname: $('#system-hostname').value.trim() }) });
    $('#system-hostname').value = value.hostname;
    showToast('系统设置已保存', 'success');
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function exportConfiguration() {
  try {
    const value = await api('/api/config/export');
    const stamp = String(value.exported_at || '').replace(/[-:]/g, '').replace('T', '-').replace('Z', '') || 'backup';
    const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `pe31625g24dira-config-${stamp}.json`;
    document.body.append(link); link.click(); link.remove();
    URL.revokeObjectURL(link.href);
    showToast('配置已导出', 'success');
  } catch (error) {
    showToast(error.message);
  }
}

async function selectConfigurationFile(event) {
  const file = event.target.files[0];
  ui.importedConfig = null;
  $('#config-import').disabled = true;
  $('#config-import-name').textContent = file ? file.name : '未选择文件';
  if (!file) return;
  if (file.size > 262144) {
    event.target.value = '';
    return showToast('配置文件过大');
  }
  try {
    const value = JSON.parse(await file.text());
    if (value.format !== 'pe31625g24dira-switch-config') throw new Error('不是 PE31625G24DIRA 配置备份');
    ui.importedConfig = value;
    $('#config-import').disabled = false;
  } catch (error) {
    event.target.value = '';
    $('#config-import-name').textContent = '未选择文件';
    showToast(error.message || '配置文件无效');
  }
}

async function importConfiguration() {
  if (!ui.importedConfig) return;
  const value = ui.importedConfig;
  await runOperation(
    () => api('/api/config/import', { method: 'POST', body: JSON.stringify(value) }),
    '恢复配置',
  );
  ui.importedConfig = null;
  $('#config-import-file').value = '';
  $('#config-import-name').textContent = '未选择文件';
  $('#config-import').disabled = true;
}

async function selectUpgradeFile(event) {
  const file = event.target.files[0];
  ui.upgradeReady = false;
  $('#upgrade-apply').disabled = true;
  $('#upgrade-package').hidden = true;
  $('#upgrade-result').hidden = true;
  if (!file) return;
  if (file.size > 64 * 1024 * 1024 || !/\.tar\.gz$/i.test(file.name)) {
    event.target.value = '';
    return showToast('请选择不超过 64 MiB 的 .tar.gz 部署包');
  }
  try {
    const value = await api('/api/system/upgrade/upload', { method: 'POST', headers: { 'Content-Type': 'application/gzip', 'X-PE31625G24DIRA-Filename': encodeURIComponent(file.name) }, body: file });
    await inspectUpgrade(value);
  } catch (error) {
    event.target.value = '';
    showToast(error.message);
  }
}

function renderUpgradePackage(value) {
  ui.upgradeCandidate = value;
  $('#upgrade-package').hidden = false;
  $('#upgrade-file-name').textContent = value.filename || '—';
  $('#upgrade-version').textContent = `${value.current_version} → ${value.version}`;
  $('#upgrade-sha256').textContent = value.sha256 || '—';
}

async function inspectUpgrade(value) {
  renderUpgradePackage(value);
  const output = $('#upgrade-result'); output.hidden = false; output.className = 'operation-output';
  if (!value.update_available) {
    output.textContent = '已是最新版本，无需更新。';
    output.classList.add('upgrade-current');
    ui.upgradeReady = false; $('#upgrade-apply').disabled = true;
    return;
  }
  output.textContent = '正在检查更新内容…';
  const audit = await api('/api/system/upgrade/audit', { method: 'POST', body: '{}' });
  renderUpgradePackage(audit);
  output.textContent = `发现新版本 ${audit.version}，可以执行更新。`;
  output.classList.add('upgrade-available');
  ui.upgradeReady = true; $('#upgrade-apply').disabled = false;
}

async function selectLatestUpgrade() {
  const button = $('#upgrade-latest');
  button.disabled = true;
  ui.upgradeReady = false;
  ui.upgradeCandidate = null;
  $('#upgrade-apply').disabled = true;
  $('#upgrade-package').hidden = true;
  const output = $('#upgrade-result');
  output.hidden = false;
  output.className = 'operation-output upgrade-pending';
  output.textContent = '正在获取最新正式版本…';
  try {
    const value = await api('/api/system/upgrade/latest', { method: 'POST', body: '{}' });
    await inspectUpgrade(value);
  } catch (error) {
    output.textContent = error.message;
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function applyUpgrade() {
  const button = $('#upgrade-submit'); button.disabled = true;
  try {
    const value = await api('/api/system/upgrade/apply', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    $('#upgrade-modal').hidden = true;
    $('#upgrade-result').hidden = false;
    $('#upgrade-result').className = 'operation-output';
    $('#upgrade-result').textContent = '正在应用更新…';
    $('#upgrade-apply').disabled = true;
    await monitorUpgrade(value.unit);
  } catch (error) {
    showToast(error.message); button.disabled = false;
  }
}

async function monitorUpgrade(unit) {
  const output = $('#upgrade-result');
  const started = Date.now();
  while (Date.now() - started < 10 * 60 * 1000) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
    try {
      const value = await api('/api/system/upgrade/status');
      if (value.unit !== unit) continue;
      const elapsed = Math.max(1, Math.round((Date.now() - started) / 1000));
      output.textContent = value.state === 'running' ? `${value.message} · ${elapsed} 秒` : value.message;
      if (value.state === 'done') {
        output.classList.add('upgrade-current');
        window.setTimeout(() => window.location.replace('/login'), 1500);
        return;
      }
      if (value.state === 'failed') throw new Error(value.message);
    } catch (error) {
      if (Date.now() - started < 4 * 60 * 1000 && /fetch|network|Failed/i.test(error.message)) continue;
      throw error;
    }
  }
  throw new Error('更新等待超时，请重新登录后查看系统日志');
}

function openPoweroffModal() {
  if (ui.busy) return showToast('硬件配置或诊断正在进行，请完成后再关机');
  const modal = $('#poweroff-modal');
  $('#poweroff-confirm-content').hidden = false; $('#poweroff-progress').hidden = true;
  $('#poweroff-submit').disabled = false; modal.hidden = false;
  window.setTimeout(() => $('#poweroff-submit').focus(), 0);
}

function closePoweroffModal() {
  if (!ui.poweringOff) $('#poweroff-modal').hidden = true;
}

function openPowerMenu() {
  if (!ui.busy && !ui.poweringOff && !ui.rebooting) $('#power-menu-modal').hidden = false;
}

function closePowerMenu() {
  $('#power-menu-modal').hidden = true;
}

function chooseReboot() {
  closePowerMenu();
  $('#reboot-submit').disabled = false;
  $('#reboot-modal').hidden = false;
}

function choosePoweroff() {
  closePowerMenu();
  openPoweroffModal();
}

async function poweroff() {
  const submit = $('#poweroff-submit'); submit.disabled = true;
  try {
    await api('/api/system/poweroff', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    ui.poweringOff = true;
    if (ui.telemetryTimer) window.clearInterval(ui.telemetryTimer);
    $('#poweroff-confirm-content').hidden = true; $('#poweroff-progress').hidden = false;
  } catch (error) {
    submit.disabled = false;
    showToast(error.message);
  }
}

function closeRebootModal() {
  if (!ui.rebooting) $('#reboot-modal').hidden = true;
}

async function reboot() {
  const submit = $('#reboot-submit'); submit.disabled = true;
  try {
    await api('/api/system/reboot', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    ui.rebooting = true;
    if (ui.telemetryTimer) window.clearInterval(ui.telemetryTimer);
    if (ui.logTimer) window.clearInterval(ui.logTimer);
    $('#reboot-modal').hidden = true;
    showJob('系统正在重启');
    const started = Date.now();
    let observedDown = false;
    await new Promise((resolve) => window.setTimeout(resolve, 4000));
    while (Date.now() - started < 180000) {
      showJob(`系统正在重启（${Math.floor((Date.now() - started) / 1000)} 秒）`);
      try {
        const response = await fetch('/api/identity', { cache: 'no-store' });
        if (response.ok && (observedDown || Date.now() - started > 12000)) {
          hideJob();
          window.location.replace('/login');
          return;
        }
        if (!response.ok) observedDown = true;
      } catch (_) {
        // The management service is expected to be temporarily unreachable.
        observedDown = true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
    hideJob();
    showToast('系统尚未恢复，请稍后重新打开管理页面');
  } catch (error) {
    submit.disabled = false;
    hideJob();
    showToast(error.message);
  }
}

function closeFactoryResetModal() { $('#factory-reset-modal').hidden = true; }

async function factoryReset() {
  const submit = $('#factory-reset-submit'); submit.disabled = true;
  try {
    const job = await api('/api/system/factory-reset', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    closeFactoryResetModal();
    await pollJob(job, '恢复默认配置');
    window.setTimeout(() => window.location.assign('/setup'), 3300);
  } catch (error) {
    submit.disabled = false;
    showToast(error.message);
  }
}

function renderServiceHealth(health) {
  const status = health.status || 'error';
  const dot = $('#service-dot');
  dot.classList.toggle('up', status === 'healthy');
  dot.classList.toggle('starting', status === 'initializing');
  dot.classList.toggle('down', status === 'error');
  $('#service-text').textContent = status === 'healthy' ? '交换服务' : (status === 'initializing' ? '交换服务 · 启动中' : '交换服务 · 异常');
  $('#system-service-status').textContent = status === 'healthy' ? '正常' : (status === 'initializing' ? '初始化中' : `异常 · ${health.service || 'unknown'}`);
  $('#system-service-uio').textContent = health.uio_ready ? '已就绪' : '不可用';
  $('#system-service-testpoint').textContent = health.testpoint_ready ? '已就绪' : '未就绪';
}

async function loadServiceHealth() {
  try { renderServiceHealth(await api('/api/health')); } catch (error) { renderServiceHealth({ status: 'error', service: 'unreachable' }); }
}

function renderSystemInformation(info) {
  const components = info.components || {};
  const storageInfo = info.storage || {};
  const storage = storageInfo.total ? `${formatBytes(storageInfo.used)} / ${formatBytes(storageInfo.total)} · ${storageInfo.usage_percent}%` : '未知';
  $('#system-info-hostname').textContent = info.hostname || '未知';
  $('#system-info-os').textContent = info.os || '未知';
  $('#system-info-kernel').textContent = info.kernel || '未知';
  $('#system-device-cpu').textContent = info.cpu_model || '未知';
  $('#system-device-bios').textContent = info.bios || '未知';
  $('#system-info-storage').textContent = storage;
  $('#storage').textContent = storage;
  $('#component-manager').textContent = components.manager || '未知';
  $('#component-ies').textContent = components.ies_sdk || '未知';
  $('#component-testpoint').textContent = components.testpoint || '未知';
  const driver = components.fm10k_uio || {};
  $('#component-driver').textContent = driver.version || '未知';
}

async function loadState(resetDraft = true) {
  const state = await api('/api/state'); ui.state = state; ui.csrf = state.csrf;
  if (resetDraft) { ui.topology = Object.fromEntries(state.groups.map((group) => [group.key, clone(choiceFor(group))])); ui.vlans = clone(state.vlans); }
  ui.l2 = clone(state.l2);
  ui.l2Saved = clone(state.l2);
  ui.fan = clone(state.fan_control);
  renderServiceHealth(state.service_health || { status: state.service === 'active' ? 'healthy' : 'error', service: state.service });
  renderSystemInformation(state.system_information || {});
  $('#version-text').textContent = `管理软件 ${state.version}`;
  $('#account-username').value = state.username || '';
  if (state.system_settings) $('#system-hostname').value = state.system_settings.hostname || '';
  renderPorts(); renderStatsPortSelect(); renderVlans(); renderL2(); renderFanCurve();
}

function setMobileNavigation(open) {
  const sidebar = $('#sidebar');
  const toggle = $('#mobile-nav-toggle');
  const backdrop = $('#mobile-nav-backdrop');
  if (!sidebar || !toggle || !backdrop) return;
  sidebar.classList.toggle('mobile-open', open);
  backdrop.classList.toggle('visible', open);
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  document.body.classList.toggle('mobile-nav-open', open);
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => {
  setPage(button.dataset.page);
  setMobileNavigation(false);
}));
document.querySelectorAll('.nav-group-toggle').forEach((button) => button.addEventListener('click', () => {
  const group = button.closest('.nav-group');
  group.classList.toggle('open');
  button.setAttribute('aria-expanded', String(group.classList.contains('open')));
}));
document.querySelectorAll('[data-page-link]').forEach((button) => button.addEventListener('click', () => setPage(button.dataset.pageLink)));
document.querySelectorAll('[data-log-source]').forEach((button) => button.addEventListener('click', () => loadLogs(button.dataset.logSource)));
window.addEventListener('popstate', () => setPage(pageFromLocation(), false));
document.addEventListener('selectionchange', () => {
  if (!hasTextSelection() && ui.pendingTelemetry) renderTelemetry(ui.pendingTelemetry);
});
$('#refresh-all').addEventListener('click', refreshAll); $('#system-power').addEventListener('click', openPowerMenu); $('#logout').addEventListener('click', logout); $('#apply-topology').addEventListener('click', applyTopology); $('#apply-vlans').addEventListener('click', applyVlans);
['loop-protection-toggle', 'storm-control-toggle', 'mirror-toggle'].forEach((id) => $(`#${id}`).addEventListener('click', (event) => setSwitch(event.currentTarget, event.currentTarget.getAttribute('aria-checked') !== 'true')));
$('#apply-l2').addEventListener('click', applyL2); $('#apply-port-labels').addEventListener('click', applyPortLabels); $('#lldp-refresh').addEventListener('click', refreshNeighbors);
$('#log-refresh').addEventListener('click', () => loadLogs());
$('#log-content').addEventListener('scroll', (event) => {
  const content = event.currentTarget;
  ui.logAutoFollow = content.scrollHeight - content.scrollTop - content.clientHeight < 24;
});
$('#topology-preview-cancel').addEventListener('click', closeTopologyPreview); $('#topology-preview-confirm').addEventListener('click', confirmTopology);
$('#vlan-preview-cancel').addEventListener('click', closeVlanPreview); $('#vlan-preview-confirm').addEventListener('click', confirmVlans);
$('#fdb-read').addEventListener('click', () => readDiagnostic('/api/fdb/refresh', '读取 MAC 表', 'fdb', renderFdb)); $('#lane-diagnostic-read').addEventListener('click', readLaneDiagnostic); $('#optics-read').addEventListener('click', () => readDiagnostic('/api/optics/diagnostics', '读取光引擎', 'optics_diagnostic', renderOptics));
$('#poweroff-cancel').addEventListener('click', closePoweroffModal); $('#poweroff-submit').addEventListener('click', poweroff);
$('#poweroff-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closePoweroffModal(); });
$('#power-menu-cancel').addEventListener('click', closePowerMenu); $('#choose-reboot').addEventListener('click', chooseReboot); $('#choose-poweroff').addEventListener('click', choosePoweroff);
$('#power-menu-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closePowerMenu(); });
$('#reboot-cancel').addEventListener('click', closeRebootModal); $('#reboot-submit').addEventListener('click', reboot);
$('#reboot-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closeRebootModal(); });
$('#vlan-tagged-cancel').addEventListener('click', closeTaggedVlanModal); $('#vlan-tagged-save').addEventListener('click', saveTaggedVlans);
$('#vlan-tagged-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closeTaggedVlanModal(); });
$('#port-admin-cancel').addEventListener('click', closePortAdminModal); $('#port-admin-submit').addEventListener('click', submitPortAdmin);
$('#port-admin-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closePortAdminModal(); });
$('#open-add-vlan').addEventListener('click', openAddVlanModal); $('#add-vlan-cancel').addEventListener('click', closeAddVlanModal);
$('#add-vlan-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closeAddVlanModal(); });
$('#factory-reset').addEventListener('click', () => { if (!ui.busy) { $('#factory-reset-submit').disabled = false; $('#factory-reset-modal').hidden = false; } });
$('#factory-reset-cancel').addEventListener('click', closeFactoryResetModal); $('#factory-reset-submit').addEventListener('click', factoryReset);
$('#factory-reset-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) closeFactoryResetModal(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { setMobileNavigation(false); closePowerMenu(); closePoweroffModal(); closeRebootModal(); closeFactoryResetModal(); closeAddVlanModal(); closeTaggedVlanModal(); closePortAdminModal(); $('#upgrade-modal').hidden = true; } });
$('#mobile-nav-toggle').addEventListener('click', () => setMobileNavigation(!$('#sidebar').classList.contains('mobile-open')));
$('#mobile-nav-backdrop').addEventListener('click', () => setMobileNavigation(false));
window.addEventListener('resize', () => { if (window.innerWidth > 700) setMobileNavigation(false); });
$('#stats-port').addEventListener('change', renderPortStatistics);
$('#mpo1-admin').addEventListener('click', () => toggleMpo(1)); $('#mpo2-admin').addEventListener('click', () => toggleMpo(2));
$('#add-vlan-form').addEventListener('submit', (event) => { event.preventDefault(); const form = event.currentTarget; if (!form.checkValidity()) return showToast('VLAN ID 必须为 2–4094，名称不能为空'); const id = Number($('#new-vlan-id').value); const name = $('#new-vlan-name').value.trim(); if (ui.vlans.some((vlan) => vlan.id === id)) return showToast(`VLAN ${id} 已存在`); ui.vlans.push({ id, name, mtu: 1536, tagged: [], untagged: [] }); closeAddVlanModal(); renderVlans(); });
$('#theme-mode').value = window.fmTheme ? window.fmTheme.getMode() : 'system';
$('#theme-mode').addEventListener('change', (event) => window.fmTheme && window.fmTheme.setMode(event.target.value));
$('#traffic-unit').value = ui.trafficUnit;
$('#traffic-unit').addEventListener('change', (event) => {
  ui.trafficUnit = event.target.value === 'bytes' ? 'bytes' : 'bits';
  window.localStorage.setItem(TRAFFIC_UNIT_STORAGE_KEY, ui.trafficUnit);
  if (ui.telemetry) renderTelemetry(ui.telemetry);
});
$('#account-form').addEventListener('submit', saveAccount);
$('#system-settings-form').addEventListener('submit', saveSystemSettings);
$('#config-export').addEventListener('click', exportConfiguration);
$('#config-import-file').addEventListener('change', selectConfigurationFile);
$('#config-import').addEventListener('click', importConfiguration);
$('#upgrade-file').addEventListener('change', selectUpgradeFile);
$('#upgrade-latest').addEventListener('click', selectLatestUpgrade);
$('#upgrade-apply').addEventListener('click', () => { if (ui.upgradeReady) { $('#upgrade-submit').disabled = false; $('#upgrade-modal').hidden = false; } });
$('#upgrade-cancel').addEventListener('click', () => { $('#upgrade-modal').hidden = true; });
$('#upgrade-submit').addEventListener('click', applyUpgrade);
$('#upgrade-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
$('#fan-form').addEventListener('submit', applyFanCurve); document.querySelectorAll('#fan-form input,#fan-form select').forEach((field) => field.addEventListener('input', updateFanDraft));
enhanceSelects(document);
enhanceNumberInputs(document);

(async () => {
  const initialPage = pageFromLocation();
  setPage(initialPage, false);
  if (window.location.pathname !== PAGE_PATHS[initialPage]) window.history.replaceState({ page: initialPage }, '', PAGE_PATHS[initialPage]);
  try { await loadState(); await loadTelemetry(); setTelemetryInterval(TELEMETRY_INTERVAL_SECONDS); ui.healthTimer = window.setInterval(loadServiceHealth, 10000); setLogInterval(); } catch (error) { showToast(`加载失败：${error.message}`); }
})();
