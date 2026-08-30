'use strict';

export function createDashboard(ctx) {
  const {
    ui, $, api, speedLabel, formatBytes, formatRate, formatBitRate,
    formatEthernetLink, formatCount, formatUptime, loadPercent,
  } = ctx;
  const renderOpticsTelemetry = (...args) => ctx.renderOpticsTelemetry(...args);

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

  const sdk = data.switch_sensors;
  const switchGrid = $('#switch-sensors');
  switchGrid.replaceChildren();
  $('#voltage-sensors').replaceChildren();
  renderOpticsTelemetry(
    sdk.optics || { state: sdk.state, sampled: sdk.sampled, modules: [] },
    data.optics_diagnostic,
  );
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
    sdk.voltages.forEach((item) => {
      $('#voltage-sensors').append(
        sensorBox(item.name.replace('VOLTAGE SENSOR ', ''), item.volts, ' V', 3),
      );
    });
  } else {
    $('#overview-switch-temp').textContent = sdk.state === 'error' ? '读取失败' : '读取中';
    $('#cooling-switch-temp').textContent = sdk.state === 'error' ? '读取失败' : '读取中';
    $('#sensor-age').textContent = sdk.state === 'error' ? 'SDK 错误' : '等待 SDK';
    const message = document.createElement('span');
    message.className = 'muted';
    message.textContent = sdk.error || '读取中…';
    switchGrid.append(message);
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

  return {
    renderActivePorts,
    renderPortStatistics,
    hasTextSelection,
    renderTelemetry,
    loadTelemetry,
    setTelemetryInterval,
    updateLinkBadge,
    linkBadge,
    renderPortLinks,
  };
}
