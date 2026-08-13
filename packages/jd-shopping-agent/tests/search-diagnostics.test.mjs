import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {
  hasAuthenticatedJdSession,
  isAuthenticatedJdAccountPage,
  isJdSearchUnavailableText,
  JdBrowser
} from '../src/jd-browser.mjs';

test('recognizes the JD search error page captured in production', () => {
  assert.equal(isJdSearchUnavailableText('若长时间无法搜索可点击反馈，平台将尽快处理'), true);
  assert.equal(isJdSearchUnavailableText('品类齐全 轻松购物 多仓直发'), false);
});

test('does not mistake visitor cookies for a real JD login', () => {
  assert.equal(hasAuthenticatedJdSession([
    { name: 'pin', value: 'remembered-user' },
    { name: 'thor', value: 'visitor-cookie' }
  ]), false);
  assert.equal(hasAuthenticatedJdSession([
    { name: 'pt_key', value: 'session-key' },
    { name: 'pt_pin', value: 'signed-in-user' }
  ]), true);
  assert.equal(hasAuthenticatedJdSession([
    { name: 'pt_key', value: 'deleted' },
    { name: 'pt_pin', value: 'signed-in-user' }
  ]), false);
});

test('accepts a real JD order center page without relying on legacy cookies', () => {
  assert.equal(isAuthenticatedJdAccountPage({
    url: 'https://order.jd.com/center/list.action',
    text: '我的订单 全部订单 待付款 待收货'
  }), true);
  assert.equal(isAuthenticatedJdAccountPage({
    url: 'https://passport.jd.com/new/login.aspx',
    text: '登录京东 账户登录'
  }), false);
  assert.equal(isAuthenticatedJdAccountPage({
    url: 'https://order.jd.com/center/list.action',
    text: '请先登录'
  }), false);
});

test('persists a rate-limit cooldown so restarting cannot hammer JD', async () => {
  const dataDir = path.resolve('tests', '_tmp', crypto.randomUUID());
  fs.mkdirSync(dataDir, { recursive: true });
  try {
    const browser = new JdBrowser({ paths: { dataDir } });
    browser.markSearchRateLimited();

    await assert.rejects(() => browser.waitForSearchSlot(), /仍在冷却期/);
    const state = JSON.parse(fs.readFileSync(path.join(dataDir, 'jd-search-state.json'), 'utf8'));
    assert.equal(state.reason, 'rate_limited');
    assert.ok(state.blockedUntil > Date.now());
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
