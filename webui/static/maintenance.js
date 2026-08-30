'use strict';

export function createMaintenance(ctx) {
  const { ui, $, api, showToast, formatBytes } = ctx;
  const pollJob = (...args) => ctx.pollJob(...args);
  const runOperation = (...args) => ctx.runOperation(...args);
  const showJob = (...args) => ctx.showJob(...args);
  const hideJob = (...args) => ctx.hideJob(...args);

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
  $('#upgrade-apply').textContent = '执行更新';
  if (value.version_relation === 'current') {
    output.textContent = '已是最新版本，无需更新。';
    output.classList.add('upgrade-current');
    ui.upgradeReady = false; $('#upgrade-apply').disabled = true;
    return;
  }
  if (value.version_relation === 'downgrade' && !ui.upgradeAllowDowngrade) {
    output.textContent = `检测到较旧版本 ${value.version}。`;
    output.classList.add('upgrade-available');
    ui.upgradeReady = true; $('#upgrade-apply').disabled = false;
    $('#upgrade-apply').textContent = `降级到 ${value.version}`;
    return;
  }
  if (value.version_relation === 'unknown') {
    output.textContent = '无法比较更新包版本。';
    ui.upgradeReady = false; $('#upgrade-apply').disabled = true;
    return;
  }
  output.textContent = '正在检查更新内容…';
  output.classList.add('upgrade-pending');
  const audit = await api('/api/system/upgrade/audit', { method: 'POST', body: JSON.stringify({ allow_downgrade: ui.upgradeAllowDowngrade }) });
  renderUpgradePackage(audit);
  output.className = 'operation-output upgrade-available';
  output.textContent = audit.version_relation === 'downgrade' ? `可以降级到 ${audit.version}。` : `发现新版本 ${audit.version}，可以执行更新。`;
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
  output.textContent = ui.upgradeIncludePrerelease ? '正在获取最新版本（包含预发布）…' : '正在获取最新正式版本…';
  try {
    const value = await api('/api/system/upgrade/latest', { method: 'POST', body: JSON.stringify({ include_prerelease: ui.upgradeIncludePrerelease, allow_downgrade: ui.upgradeAllowDowngrade }) });
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
  const downgrade = ui.upgradeCandidate?.version_relation === 'downgrade';
  try {
    if (downgrade) {
      ui.upgradeAllowDowngrade = true;
      $('#upgrade-modal').hidden = true;
      const output = $('#upgrade-result'); output.hidden = false; output.className = 'operation-output upgrade-pending'; output.textContent = '正在检查降级包…';
      if (ui.upgradeCandidate.staged === false) {
        const value = await api('/api/system/upgrade/latest', { method: 'POST', body: JSON.stringify({ include_prerelease: ui.upgradeIncludePrerelease, allow_downgrade: true }) });
        await inspectUpgrade(value);
      } else {
        await inspectUpgrade(ui.upgradeCandidate);
      }
      if (!ui.upgradeReady) throw new Error('降级包未通过检查');
    }
    const value = await api('/api/system/upgrade/apply', { method: 'POST', body: JSON.stringify({ confirm: true, allow_downgrade: ui.upgradeAllowDowngrade }) });
    $('#upgrade-modal').hidden = true;
    $('#upgrade-result').hidden = false;
    $('#upgrade-result').className = 'operation-output';
    $('#upgrade-result').textContent = '正在应用更新…';
    $('#upgrade-apply').disabled = true;
    await monitorUpgrade(value.unit);
  } catch (error) {
    ui.upgradeAllowDowngrade = false;
    if (downgrade && ui.upgradeCandidate) { ui.upgradeReady = true; $('#upgrade-apply').disabled = false; $('#upgrade-apply').textContent = `降级到 ${ui.upgradeCandidate.version}`; }
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

  return {
    logout,
    loadLogs,
    setLogInterval,
    saveAccount,
    saveSystemSettings,
    exportConfiguration,
    selectConfigurationFile,
    importConfiguration,
    selectUpgradeFile,
    selectLatestUpgrade,
    applyUpgrade,
    openPoweroffModal,
    closePoweroffModal,
    openPowerMenu,
    closePowerMenu,
    chooseReboot,
    choosePoweroff,
    poweroff,
    closeRebootModal,
    reboot,
    closeFactoryResetModal,
    factoryReset,
    renderServiceHealth,
    loadServiceHealth,
    renderSystemInformation,
  };
}
