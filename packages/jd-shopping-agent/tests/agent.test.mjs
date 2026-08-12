import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { purchaseGiftCandidate, searchGiftCandidates } from '../src/agent.mjs';
import { createStandingAuthorization, ensureLocalSecret } from '../src/safety.mjs';

function makeConfig() {
  const root = path.resolve('tests', '_tmp', crypto.randomUUID());
  const dataDir = path.join(root, 'data');
  const config = {
    policy: {
      perOrderCapCny: 300,
      monthlyCapCny: 600,
      maxOrdersPerMonth: 2,
      autoSubmitOrder: true,
      tryJdBalancePayment: true,
      extraBlockedTerms: []
    },
    paths: {
      dataDir,
      authorization: path.join(dataDir, 'standing-authorization.json'),
      ledger: path.join(dataDir, 'ledger.json'),
      searches: path.join(dataDir, 'searches')
    }
  };
  const secret = ensureLocalSecret(dataDir);
  const auth = createStandingAuthorization({
    ...config.policy,
    expiresAt: '2099-01-01T00:00:00Z',
    secret
  });
  fs.writeFileSync(config.paths.authorization, JSON.stringify(auth));
  return config;
}

function candidate(sku = '100123456789') {
  return {
    title: '京东自营桌面氛围灯',
    priceCny: 199,
    sku,
    url: `https://item.jd.com/${sku}.html`,
    shop: '京东自营'
  };
}

test('search uses model-provided queries and requires no preference form', async () => {
  const config = makeConfig();
  const seen = [];
  const browser = {
    ensureLoggedIn: async () => true,
    search: async (query) => {
      seen.push(query);
      return [candidate(`10012345678${seen.length}`), candidate(`10012345679${seen.length}`)];
    }
  };
  const result = await searchGiftCandidates(config, browser, {
    id: 'jd_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    payload: { queries: ['她会喜欢的桌面礼物'], budgetCny: 300, maxCandidates: 10 }
  });
  assert.deepEqual(seen, ['她会喜欢的桌面礼物']);
  assert.equal(result.candidates.length, 2);
});

test('a completed purchase task is idempotent', async () => {
  const config = makeConfig();
  let submits = 0;
  const browser = {
    ensureLoggedIn: async () => true,
    addToCart: async () => {},
    prepareCheckout: async () => ({ totalCny: 199 }),
    submitOrder: async () => { submits += 1; },
    tryPayWithBalance: async () => ({ ok: true })
  };
  const task = {
    id: 'jd_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    payload: { budgetCny: 300, searchTaskId: 'jd_search', candidate: candidate() }
  };
  const first = await purchaseGiftCandidate(config, browser, task);
  const second = await purchaseGiftCandidate(config, browser, task);
  assert.equal(first.paid, true);
  assert.deepEqual(second, first);
  assert.equal(submits, 1);
});

test('an ambiguous submit failure is locked against a duplicate order', async () => {
  const config = makeConfig();
  let submits = 0;
  const browser = {
    ensureLoggedIn: async () => true,
    addToCart: async () => {},
    prepareCheckout: async () => ({ totalCny: 199 }),
    submitOrder: async () => { submits += 1; throw new Error('页面在提交瞬间断开'); },
    tryPayWithBalance: async () => ({ ok: false })
  };
  const task = {
    id: 'jd_cccccccccccccccccccccccccccccccc',
    payload: { budgetCny: 300, searchTaskId: 'jd_search', candidate: candidate() }
  };
  await assert.rejects(() => purchaseGiftCandidate(config, browser, task), /提交瞬间断开/);
  await assert.rejects(() => purchaseGiftCandidate(config, browser, task), /防止重复下单/);
  assert.equal(submits, 1);
});
