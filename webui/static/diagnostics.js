'use strict';

export function createDiagnostics(ctx) {
  const { ui, $, api, clone, showToast } = ctx;
  const pollJob = (...args) => ctx.pollJob(...args);

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

function renderOpticsIdentity(section, identity, temperatureC) {
  const fields = [
    ['厂商', identity?.vendor],
    ['型号', identity?.part_number],
    ['序列号', identity?.serial],
    ['生产日期', identity?.date_code],
    ['光引擎内部温度', Number.isFinite(temperatureC) ? `${temperatureC.toFixed(2)} °C` : '—'],
  ].filter(([, value]) => value);
  if (!fields.length) return;
  const list = document.createElement('div');
  list.className = 'sensor-list optics-identity';
  fields.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'sensor';
    const term = document.createElement('small');
    const detail = document.createElement('strong');
    term.textContent = label;
    detail.textContent = value;
    item.append(term, detail);
    list.append(item);
  });
  section.append(list);
}

function mergeOpticsTemperatures(result, optics) {
  const merged = clone(result);
  const temperatures = new Map((optics?.modules || []).map((module) => [module.mpo, module]));
  merged.sampled = optics?.sampled || merged.sampled;
  merged.modules.forEach((module) => {
    const current = temperatures.get(module.mpo);
    if (current) {
      module.temperature_c = current.temperature_c;
      module.temperature_raw = current.temperature_raw;
      module.temperature_status = current.temperature_status;
    }
  });
  return merged;
}

function setOverviewTemperature(selector, value) {
  const target = $(selector);
  target.textContent = Number.isFinite(value) ? `${value.toFixed(1)}°C` : '—';
  target.parentElement.classList.toggle('warning', Number.isFinite(value) && value >= 85 && value < 100);
  target.parentElement.classList.toggle('danger', Number.isFinite(value) && value >= 100);
}

function renderOpticsTelemetry(optics, details) {
  const modules = new Map((optics.modules || []).map((module) => [module.mpo, module]));
  setOverviewTemperature('#overview-optics-1-temp', modules.get(1)?.temperature_c);
  setOverviewTemperature('#overview-optics-2-temp', modules.get(2)?.temperature_c);
  const renderKey = `${optics.sampled || 0}:${details?.sampled || 0}:${details?.state || 'pending'}`;
  if (renderKey === ui.opticsRenderKey) return;
  ui.opticsRenderKey = renderKey;
  if (details?.state === 'ready') renderOptics(mergeOpticsTemperatures(details, optics), true);
  else renderOptics({ sampled: optics.sampled, modules: optics.modules || [] }, false, details);
}

function renderOptics(result, detailed = true, details = null) {
  const root = $('#optics-result');
  root.className = 'diagnostic-content';
  root.replaceChildren();
  if (!result.modules.length) {
    root.className = 'diagnostic-empty';
    root.textContent = details?.state === 'error' ? '光引擎诊断读取失败，后台将自动重试' : '等待硬件采样…';
    return;
  }
  const moduleGrid = document.createElement('div');
  moduleGrid.className = 'optics-module-grid';
  root.append(moduleGrid);
  result.modules.forEach((module) => {
    const section = document.createElement('section');
    section.className = 'optics-module';
    const head = document.createElement('div');
    const title = document.createElement('strong');
    const badge = document.createElement('span');
    title.textContent = `MPO24-${module.mpo}`;
    badge.className = `link ${module.state === 'ready' ? 'up' : 'off'}`;
    badge.textContent = module.state === 'ready' ? 'RX 功率可读' : 'RX 功率不可用';
    head.append(title);
    if (detailed) head.append(badge);
    section.append(head);
    renderOpticsIdentity(section, module.identity, module.temperature_c);
    if (detailed && module.state === 'ready') {
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
    moduleGrid.append(section);
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

  return {
    renderFdb,
    renderLaneDiagnostic,
    renderOpticsTelemetry,
    renderOptics,
    readDiagnostic,
    readLaneDiagnostic,
  };
}
