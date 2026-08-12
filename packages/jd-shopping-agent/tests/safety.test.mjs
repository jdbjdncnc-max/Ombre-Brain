import test from 'node:test';
import assert from 'node:assert/strict';
import {
  checkStandingAllowance,
  createAuthorization,
  createStandingAuthorization,
  inspectCandidate,
  isAllowedJdUrl,
  normalizeSku,
  parseCny,
  verifyAuthorization,
  verifySingleSku,
  verifyStandingAuthorization
} from '../src/safety.mjs';

test('parses Chinese currency text', () => {
  assert.equal(parseCny('￥1,299.50'), 1299.5);
  assert.equal(parseCny('到手价 ¥ 88'), 88);
  assert.equal(parseCny('未知'), null);
});

test('only allows JD HTTPS URLs', () => {
  assert.equal(isAllowedJdUrl('https://item.jd.com/123456.html'), true);
  assert.equal(isAllowedJdUrl('https://cart.jd.com/cart_index'), true);
  assert.equal(isAllowedJdUrl('http://item.jd.com/123456.html'), false);
  assert.equal(isAllowedJdUrl('https://jd.com.evil.test/123456.html'), false);
});

test('normalizes SKU and blocks unsafe or over-budget products', () => {
  assert.equal(normalizeSku('https://item.jd.com/100123456789.html'), '100123456789');
  const safe = inspectCandidate({
    title: '京东自营桌面氛围灯', priceCny: '199.00', sku: '100123456789',
    url: 'https://item.jd.com/100123456789.html'
  }, 300);
  assert.equal(safe.ok, true);
  assert.equal(inspectCandidate({ ...safe.candidate, title: '视频网站自动续费会员' }, 300).ok, false);
  assert.equal(inspectCandidate({ ...safe.candidate, priceCny: 301 }, 300).ok, false);
});

test('checkout must contain exactly the chosen SKU and stay within budget', () => {
  assert.equal(verifySingleSku({ expectedSku: '100001', selectedSkus: ['100001'], totalCny: '¥299', budgetCny: 300 }).ok, true);
  assert.equal(verifySingleSku({ expectedSku: '100001', selectedSkus: ['100001', '100002'], totalCny: '¥299', budgetCny: 300 }).ok, false);
  assert.equal(verifySingleSku({ expectedSku: '100001', selectedSkus: ['100001'], totalCny: '¥301', budgetCny: 300 }).ok, false);
});

test('authorization is single-order, expires, and detects edits', () => {
  const secret = 'local-secret';
  const auth = createAuthorization({ budgetCny: 300, expiresAt: '2099-01-01T00:00:00Z', secret });
  assert.equal(verifyAuthorization(auth, secret).ok, true);
  assert.equal(auth.maxOrders, 1);
  assert.equal(auth.quantity, 1);
  assert.equal(verifyAuthorization({ ...auth, budgetCny: 999 }, secret).ok, false);
});

test('standing authorization enforces per-order, monthly, and order-count limits', () => {
  const secret = 'local-standing-secret';
  const signed = createStandingAuthorization({
    perOrderCapCny: 300,
    monthlyCapCny: 500,
    maxOrdersPerMonth: 2,
    expiresAt: '2099-01-01T00:00:00Z',
    secret
  });
  const verified = verifyStandingAuthorization(signed, secret);
  assert.equal(verified.ok, true);
  const month = new Date().toISOString().slice(0, 7);
  const ledger = { orders: [{ month, orderSubmitted: true, totalCny: 250 }] };
  assert.equal(checkStandingAllowance({ authorization: verified.authorization, ledger, requestedBudgetCny: 200 }).ok, true);
  assert.equal(checkStandingAllowance({ authorization: verified.authorization, ledger, requestedBudgetCny: 300 }).ok, false);
  assert.equal(verifyStandingAuthorization({ ...signed, monthlyCapCny: 9999 }, secret).ok, false);
});
