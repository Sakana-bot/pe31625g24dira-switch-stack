'use strict';

import { createApiClient, waitForJob } from '/api-client.js';
import { closeCustomSelects, destroySelects, enhanceNumberInputs, enhanceSelects, syncSelect } from '/controls.js';
import { createDashboard } from '/dashboard.js';
import { createDiagnostics } from '/diagnostics.js';
import { createMaintenance } from '/maintenance.js';

const TRAFFIC_UNIT_STORAGE_KEY = 'pe31625g24dira-traffic-unit';
const PRERELEASE_STORAGE_KEY = 'pe31625g24dira-include-prerelease';
const ui = {
  state: null,
  csrf: null,
  topology: {},
  vlans: [],
  l2: null,
  l2Saved: null,
  pendingPortAdmin: null,
  vlanTaggedPort: null,
  vlanPreview: null,
  fan: null,
  importedConfig: null,
  upgradeReady: false,
  upgradeCandidate: null,
  upgradeIncludePrerelease: window.localStorage.getItem(PRERELEASE_STORAGE_KEY) === 'true',
  upgradeAllowDowngrade: false,
  live: {},
  telemetry: null,
  pendingTelemetry: null,
  opticsRenderKey: null,
  busy: false,
  poweringOff: false,
  rebooting: false,
  telemetryTimer: null,
  healthTimer: null,
  logTimer: null,
  toastTimer: null,
  topologyPreview: null,
  logSource: 'system',
  logsLoaded: false,
  logAutoFollow: true,
  logRequestId: 0,
  trafficUnit: window.localStorage.getItem(TRAFFIC_UNIT_STORAGE_KEY) === 'bytes' ? 'bytes' : 'bits',
};
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
  await runOperation(() => api('/api/sensors/refresh', { method: 'POST', body: '{}' }), '刷新板卡传感器');
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

const featureContext = {
  ui, $, api, clone, escapeHtml, speedLabel, showToast,
  formatBytes, formatRate, formatBitRate, formatEthernetLink,
  formatCount, formatUptime, loadPercent, syncSelect,
  pollJob, runOperation, showJob, hideJob,
};
const diagnostics = createDiagnostics(featureContext);
Object.assign(featureContext, diagnostics);
const dashboard = createDashboard(featureContext);
Object.assign(featureContext, dashboard);
const maintenance = createMaintenance(featureContext);
Object.assign(featureContext, maintenance);

const {
  renderPortStatistics, hasTextSelection, renderTelemetry, loadTelemetry, setTelemetryInterval,
  linkBadge, renderPortLinks, readDiagnostic, readLaneDiagnostic, renderFdb,
  logout, loadLogs, setLogInterval, saveAccount, saveSystemSettings,
  exportConfiguration, selectConfigurationFile, importConfiguration,
  selectUpgradeFile, selectLatestUpgrade, applyUpgrade, openPoweroffModal,
  closePoweroffModal, openPowerMenu, closePowerMenu, chooseReboot,
  choosePoweroff, poweroff, closeRebootModal, reboot, closeFactoryResetModal,
  factoryReset, renderServiceHealth, loadServiceHealth, renderSystemInformation,
} = featureContext;

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
  if (state.system_settings) {
    $('#system-hostname').value = state.system_settings.hostname || '';
    $('#system-timezone').value = state.system_settings.timezone || 'UTC';
  }
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
$('#fdb-read').addEventListener('click', () => readDiagnostic('/api/fdb/refresh', '读取 MAC 表', 'fdb', renderFdb)); $('#lane-diagnostic-read').addEventListener('click', readLaneDiagnostic);
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
$('#upgrade-prerelease').addEventListener('click', (event) => { ui.upgradeIncludePrerelease = event.currentTarget.getAttribute('aria-checked') !== 'true'; setSwitch(event.currentTarget, ui.upgradeIncludePrerelease); window.localStorage.setItem(PRERELEASE_STORAGE_KEY, String(ui.upgradeIncludePrerelease)); });
$('#upgrade-apply').addEventListener('click', () => { if (ui.upgradeReady) { const downgrade = ui.upgradeCandidate?.version_relation === 'downgrade'; $('#upgrade-description').textContent = downgrade ? `将从 ${ui.upgradeCandidate.current_version} 降级到 ${ui.upgradeCandidate.version}。管理页面和交换端口可能中断，请勿断电。` : '更新期间管理页面会暂时断开；如果包含交换服务或驱动变更，交换端口也会短暂中断。请勿断电或重复操作。'; $('#upgrade-submit').textContent = downgrade ? '确认降级' : '确认更新'; $('#upgrade-submit').disabled = false; $('#upgrade-modal').hidden = false; } });
$('#upgrade-cancel').addEventListener('click', () => { $('#upgrade-modal').hidden = true; });
$('#upgrade-submit').addEventListener('click', applyUpgrade);
$('#upgrade-modal').addEventListener('click', (event) => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
$('#fan-form').addEventListener('submit', applyFanCurve); document.querySelectorAll('#fan-form input,#fan-form select').forEach((field) => field.addEventListener('input', updateFanDraft));
enhanceSelects(document);
enhanceNumberInputs(document);

(async () => {
  setSwitch($('#upgrade-prerelease'), ui.upgradeIncludePrerelease);
  const initialPage = pageFromLocation();
  setPage(initialPage, false);
  if (window.location.pathname !== PAGE_PATHS[initialPage]) window.history.replaceState({ page: initialPage }, '', PAGE_PATHS[initialPage]);
  try { await loadState(); await loadTelemetry(); setTelemetryInterval(TELEMETRY_INTERVAL_SECONDS); ui.healthTimer = window.setInterval(loadServiceHealth, 10000); setLogInterval(); } catch (error) { showToast(`加载失败：${error.message}`); }
})();
