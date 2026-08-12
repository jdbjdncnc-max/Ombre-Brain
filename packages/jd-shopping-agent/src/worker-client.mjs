function requireWorkerToken() {
  const token = String(process.env.OMBRE_JD_WORKER_TOKEN || '').trim();
  if (!token) throw new Error('没有找到 OMBRE_JD_WORKER_TOKEN 环境变量');
  return token;
}

async function request(config, path, body) {
  if (!/^https:\/\//i.test(config.gateway.url) && !/^http:\/\/127\.0\.0\.1(?::\d+)?$/i.test(config.gateway.url)) {
    throw new Error('gateway.url 必须是 HTTPS 地址；只有 127.0.0.1 可以使用 HTTP');
  }
  const response = await fetch(`${config.gateway.url}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${requireWorkerToken()}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body || {})
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value?.error || `Ombre 网关返回 ${response.status}`);
  return value;
}

export function sendHeartbeat(config, loggedIn, localLimits = {}) {
  return request(config, '/api/jd-shopping/worker/heartbeat', {
    workerId: config.gateway.workerId,
    version: '0.2.0',
    loggedIn: loggedIn === true,
    perOrderCapCny: Number(localLimits.perOrderCapCny) || undefined
  });
}

export function claimTask(config, loggedIn) {
  return request(config, '/api/jd-shopping/worker/claim', {
    workerId: config.gateway.workerId,
    version: '0.2.0',
    loggedIn: loggedIn === true
  });
}

export function completeTask(config, taskId, { result = null, error = '' } = {}) {
  return request(config, `/api/jd-shopping/worker/tasks/${encodeURIComponent(taskId)}/complete`, {
    workerId: config.gateway.workerId,
    result: result && typeof result === 'object' ? result : {},
    error: String(error || '').slice(0, 500)
  });
}
