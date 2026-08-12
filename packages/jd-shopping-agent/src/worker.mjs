import { setTimeout as delay } from 'node:timers/promises';
import { JdBrowser } from './jd-browser.mjs';
import { localStatus, processShoppingTask } from './agent.mjs';
import { claimTask, completeTask, sendHeartbeat } from './worker-client.mjs';

export async function runWorker(config, { signal } = {}) {
  const browser = await new JdBrowser(config).start();
  let lastHeartbeat = 0;
  let processed = 0;
  try {
    let loggedIn = await browser.ensureLoggedIn();
    if (!loggedIn) {
      const login = await browser.login();
      if (!login.ok) throw new Error(login.reason || '京东登录没有完成');
      loggedIn = true;
    }
    process.stdout.write('本地京东购物助手已在线。商品和搜索内容不会显示在这里。\n');
    while (!signal?.aborted) {
      if (Date.now() - lastHeartbeat > 10_000) {
        await sendHeartbeat(config, loggedIn, localStatus(config));
        lastHeartbeat = Date.now();
      }
      const claimed = await claimTask(config, loggedIn);
      const task = claimed?.task;
      if (!task) {
        await delay(config.gateway.pollIntervalMs, undefined, signal ? { signal } : undefined).catch(() => {});
        continue;
      }
      try {
        const result = await processShoppingTask(config, browser, task);
        await completeTask(config, task.id, { result });
        processed += 1;
        process.stdout.write(`已完成购物任务 ${processed} 个。\n`);
      } catch (error) {
        await completeTask(config, task.id, { error: error?.message || String(error) });
        process.stderr.write(`购物任务已安全停止：${error?.message || error}\n`);
      }
    }
    return { ok: true, processed };
  } finally {
    await browser.close();
  }
}
