import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function getPackageRoot() {
  return packageRoot;
}

export function loadConfig() {
  const localPath = path.join(packageRoot, 'config.local.json');
  if (!fs.existsSync(localPath)) {
    throw new Error('还没有 config.local.json。请先运行“开始设置.ps1”。');
  }
  const config = JSON.parse(fs.readFileSync(localPath, 'utf8'));
  config.gateway ??= {};
  config.policy ??= {};
  config.browser ??= {};
  config.gateway.url = String(config.gateway.url || '').replace(/\/+$/, '');
  config.gateway.workerId = String(config.gateway.workerId || 'windows-worker').slice(0, 80);
  config.gateway.pollIntervalMs = Math.min(10000, Math.max(500, Number(config.gateway.pollIntervalMs || 1500)));
  config.policy.perOrderCapCny = Number(config.policy.perOrderCapCny);
  config.policy.monthlyCapCny = Number(config.policy.monthlyCapCny);
  config.policy.maxOrdersPerMonth = Number(config.policy.maxOrdersPerMonth);
  config.policy.autoSubmitOrder = config.policy.autoSubmitOrder === true;
  config.policy.tryJdBalancePayment = config.policy.tryJdBalancePayment === true;
  config.policy.extraBlockedTerms = Array.isArray(config.policy.extraBlockedTerms) ? config.policy.extraBlockedTerms.map(String) : [];
  config.browser.headless = config.browser.headless === true;
  config.browser.slowMoMs = Math.min(1000, Math.max(0, Number(config.browser.slowMoMs || 120)));
  config.browser.profileDir = path.resolve(packageRoot, config.browser.profileDir || './data/jd-profile');
  config.paths = {
    packageRoot,
    dataDir: path.join(packageRoot, 'data'),
    authorization: path.join(packageRoot, 'data', 'standing-authorization.json'),
    ledger: path.join(packageRoot, 'data', 'ledger.json'),
    searches: path.join(packageRoot, 'data', 'searches'),
    screenshots: path.join(packageRoot, 'screenshots')
  };
  return config;
}

export function ensureConfigFromExample() {
  const localPath = path.join(packageRoot, 'config.local.json');
  if (!fs.existsSync(localPath)) {
    fs.copyFileSync(path.join(packageRoot, 'config.example.json'), localPath);
    return { created: true, path: localPath };
  }
  return { created: false, path: localPath };
}
