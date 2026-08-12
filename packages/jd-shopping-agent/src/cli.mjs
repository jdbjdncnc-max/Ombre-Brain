import fs from 'node:fs';
import { ensureConfigFromExample, loadConfig } from './config.mjs';
import { localStatus } from './agent.mjs';
import { JdBrowser } from './jd-browser.mjs';
import { createStandingAuthorization, ensureLocalSecret } from './safety.mjs';
import { runWorker } from './worker.mjs';

function printSafe(value) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function expiryIso(value) {
  const text = String(value || '').trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? `${text}T23:59:59+08:00` : text;
}

async function main() {
  const command = process.argv[2] || 'status';
  const prepared = ensureConfigFromExample();
  if (prepared.created) {
    process.stdout.write(`已建立配置文件：${prepared.path}\n请填写 Ombre 地址和长期额度，不需要填写任何喜好。\n`);
    return;
  }
  const config = loadConfig();
  if (command === 'authorize') {
    const perOrderCapCny = process.argv[3] || config.policy.perOrderCapCny;
    const monthlyCapCny = process.argv[4] || config.policy.monthlyCapCny;
    const maxOrdersPerMonth = Number(process.argv[5] || config.policy.maxOrdersPerMonth);
    const expiresAt = expiryIso(process.argv[6] || config.policy.expiresAt);
    const secret = ensureLocalSecret(config.paths.dataDir);
    const auth = createStandingAuthorization({
      perOrderCapCny,
      monthlyCapCny,
      maxOrdersPerMonth,
      expiresAt,
      autoSubmitOrder: config.policy.autoSubmitOrder,
      tryJdBalancePayment: config.policy.tryJdBalancePayment,
      secret
    });
    fs.mkdirSync(config.paths.dataDir, { recursive: true });
    fs.writeFileSync(config.paths.authorization, JSON.stringify(auth, null, 2));
    printSafe({
      ok: true,
      perOrderCapCny: auth.perOrderCapCny,
      monthlyCapCny: auth.monthlyCapCny,
      maxOrdersPerMonth: auth.maxOrdersPerMonth,
      expiresAt: auth.expiresAt
    });
    return;
  }
  if (command === 'login') {
    const browser = await new JdBrowser(config).start();
    try {
      if (await browser.ensureLoggedIn()) printSafe({ ok: true, alreadyLoggedIn: true });
      else printSafe(await browser.login());
    } finally {
      await browser.close();
    }
    return;
  }
  if (command === 'worker') {
    const controller = new AbortController();
    process.on('SIGINT', () => controller.abort());
    await runWorker(config, { signal: controller.signal });
    return;
  }
  if (command === 'status') {
    printSafe(localStatus(config));
    return;
  }
  throw new Error(`不认识的命令：${command}`);
}

main().catch((error) => {
  process.stderr.write(`已安全停止：${error?.message || error}\n`);
  process.exitCode = 1;
});
