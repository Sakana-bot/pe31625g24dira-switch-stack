export function createApiClient(getCsrf) {
  return async function api(path, options = {}) {
    const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const csrf = getCsrf();
    if (csrf && options.method === 'POST') headers['X-PE31625G24DIRA-CSRF'] = csrf;
    const response = await fetch(path, Object.assign({}, options, { headers, cache: 'no-store' }));
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.replace('/login');
      throw new Error('登录已失效');
    }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };
}

export async function waitForJob(api, job, onProgress, options = {}) {
  const intervalMs = options.intervalMs || 1200;
  const timeoutMs = options.timeoutMs || 180000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    const current = await api(`/api/jobs/${job.id}`);
    onProgress(current);
    if (current.state === 'done') return current;
    if (current.state === 'failed') {
      throw new Error(`${current.message}: ${current.error || '未知错误'}`);
    }
  }
  throw new Error('操作等待超时；后台任务可能仍在执行，请检查维护日志后再重试');
}
