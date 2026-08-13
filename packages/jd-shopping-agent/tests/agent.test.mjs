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
    search: async (query, _limit, options) => {
      seen.push(query);
      assert.ok(options.deadlineEpoch > Date.now());
      assert.ok(options.deadlineEpoch <= Date.now() + 90_000);
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

test('search deduplicates repeated model queries', async () => {
  const config = makeConfig();
  const seen = [];
  const browser = {
    ensureLoggedIn: async () => true,
    search: async (query) => {
      seen.push(query);
      return [candidate('100123450001'), candidate('100123450002')];
    }
  };
  const result = await searchGiftCandidates(config, browser, {
    id: 'jd_dddddddddddddddddddddddddddddddd',
    payload: { queries: ['桌面礼物', ' 桌面礼物 ', '桌面礼物'], budgetCny: 300, maxCandidates: 10 }
  });
  assert.deepEqual(seen, ['桌面礼物']);
  assert.equal(result.candidates.length, 2);
});

test('search stops immediately on a JD rate-limit page instead of retrying', async () => {
  const config = makeConfig();
  let searches = 0;
  const browser = {
    ensureLoggedIn: async () => true,
    search: async () => {
      searches += 1;
      const error = new Error('京东空页');
      error.code = 'JD_SEARCH_UNAVAILABLE';
      throw error;
    }
  };
  await assert.rejects(() => searchGiftCandidates(config, browser, {
    id: 'jd_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
    payload: { queries: ['桌面礼物', '随身礼物', '宿舍礼物'], budgetCny: 300, maxCandidates: 10 }
  }), /京东空页/);
  assert.equal(searches, 1);
});

test('search stops after the first query once two safe candidates exist', async () => {
  const config = makeConfig();
  const seen = [];
  const browser = {
    ensureLoggedIn: async () => true,
    search: async (query) => {
      seen.push(query);
      return [candidate('100123450011'), candidate('100123450012')];
    }
  };
  const result = await searchGiftCandidates(config, browser, {
    id: 'jd_gggggggggggggggggggggggggggggggg',
    payload: { queries: ['桌面礼物', '随身礼物', '宿舍礼物'], budgetCny: 300, maxCandidates: 10 }
  });
  assert.equal(result.candidates.length, 2);
  assert.deepEqual(seen, ['桌面礼物']);
});

test('search explains when parsed products fail budget or safety rules', async () => {
  const config = makeConfig();
  const browser = {
    ensureLoggedIn: async () => true,
    search: async () => [{ ...candidate('100123450003'), priceCny: 999 }]
  };
  await assert.rejects(() => searchGiftCandidates(config, browser, {
    id: 'jd_ffffffffffffffffffffffffffffffff',
    payload: { queries: ['桌面礼物'], budgetCny: 300, maxCandidates: 10 }
  }), /1 件超过 300 元预算.*不要继续搜索同一个具体型号/);
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
