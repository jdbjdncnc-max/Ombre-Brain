import fs from 'node:fs';
import path from 'node:path';
import { isAllowedJdUrl, normalizeSku, parseCny, verifySingleSku } from './safety.mjs';
import { PlaywrightExtensionBridge } from './playwright-extension-bridge.mjs';

const JD_SEARCH_UNAVAILABLE_MARKERS = [
  '若长时间无法搜索',
  '平台将尽快处理',
  '访问过于频繁',
  '搜索失败',
  '系统繁忙',
  '暂时无法搜索'
];
const MIN_SEARCH_INTERVAL_MS = 12_000;
const SEARCH_RATE_LIMIT_COOLDOWN_MS = 30 * 60_000;
const AUTH_CONFIRMATION_TTL_MS = 5 * 60_000;

function usableCookieValue(value) {
  const text = String(value || '').trim();
  return Boolean(text && text.toLowerCase() !== 'deleted');
}

export function hasAuthenticatedJdSession(cookies) {
  const values = new Map(
    (Array.isArray(cookies) ? cookies : [])
      .filter((cookie) => cookie && usableCookieValue(cookie.value))
      .map((cookie) => [String(cookie.name || ''), String(cookie.value || '')])
  );
  return values.has('pt_key') && (values.has('pt_pin') || values.has('pin'));
}

export function isAuthenticatedJdAccountPage({ url, text } = {}) {
  let hostname = '';
  try {
    hostname = new URL(String(url || '')).hostname.toLowerCase();
  } catch {
    return false;
  }
  if (hostname !== 'order.jd.com') return false;
  const body = String(text || '').replace(/\s+/g, ' ');
  if (/登录京东|账户登录|扫码登录|短信登录|请先登录/.test(body)) return false;
  return /我的订单|订单中心|全部订单|待付款|待收货/.test(body);
}

export function isJdSearchUnavailableText(value) {
  const text = String(value || '').replace(/\s+/g, ' ');
  return JD_SEARCH_UNAVAILABLE_MARKERS.some((marker) => text.includes(marker));
}

function jdSearchUnavailableError() {
  const error = new Error('京东提示“访问过于频繁”，本地助手已停止继续搜索并进入 30 分钟冷却；请稍后再试，不要反复重启撞搜索页');
  error.code = 'JD_SEARCH_UNAVAILABLE';
  return error;
}

function searchCooldownError(blockedUntil) {
  const remainingMinutes = Math.max(1, Math.ceil((blockedUntil - Date.now()) / 60_000));
  const error = new Error(`京东搜索仍在冷却期，约 ${remainingMinutes} 分钟后再试；本地助手不会继续请求，以免延长风控`);
  error.code = 'JD_SEARCH_COOLDOWN';
  return error;
}

function searchDeadlineError() {
  const error = new Error('本次京东搜索接近 MCP 等待上限，已提前安全停止；请稍后换一个更宽泛的搜索词再试');
  error.code = 'JD_SEARCH_DEADLINE';
  return error;
}

function codeValue(value) {
  return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

const PAGE_HELPERS = `
  async function firstVisible(page, selectors) {
    for (const selector of selectors) {
      const locator = page.locator(selector).first();
      if (await locator.isVisible().catch(() => false)) return locator;
    }
    return null;
  }
  async function clickByTexts(page, texts) {
    for (const text of texts) {
      const matches = page.getByText(text, { exact: false });
      for (let index = (await matches.count()) - 1; index >= 0; index -= 1) {
        const locator = matches.nth(index);
        if (await locator.isVisible().catch(() => false)) {
          await locator.click({ timeout: 8000 });
          return true;
        }
      }
    }
    return false;
  }
`;

export class JdBrowser {
  constructor(config, { bridge } = {}) {
    this.config = config;
    this.bridge = bridge || null;
    this.checkoutVerifiedSku = '';
    this.checkoutVerifiedTotalCny = null;
    this.authConfirmedUntil = 0;
  }

  async start() {
    fs.mkdirSync(this.config.paths.screenshots, { recursive: true });
    this.bridge ||= new PlaywrightExtensionBridge({
      channel: this.config.browser.channel,
      outputDir: this.config.paths.screenshots
    });
    await this.bridge.start();
    return this;
  }

  async close() {
    await this.bridge?.close();
  }

  async runPage(body, input = {}, { timeoutMs } = {}) {
    if (!this.bridge) throw new Error('浏览器扩展尚未启动');
    const code = `async (page) => { const input = ${codeValue(input)}; ${body} }`;
    return this.bridge.runPageCode(code, { timeoutMs });
  }

  async pause(milliseconds) {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  async screenshot(name) {
    const safe = String(name).replace(/[^a-z0-9_-]+/gi, '-');
    const target = path.join(this.config.paths.screenshots, `${Date.now()}-${safe}.png`);
    await this.runPage(
      'await page.screenshot({ path: input.target, fullPage: false }); return { ok: true };',
      { target },
      { timeoutMs: 5_000 }
    ).catch(() => {});
    return target;
  }

  async readAuthEvidence({ probeAccountPage = false } = {}) {
    return this.runPage(`
      if (input.probeAccountPage) {
        await page.goto('https://order.jd.com/center/list.action', { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1200);
      }
      const text = await page.locator('body').innerText().catch(() => '');
      return {
        cookies: await page.context().cookies('https://jd.com'),
        url: page.url(),
        text: text.slice(0, 6000)
      };
    `, { probeAccountPage });
  }

  rememberAuthenticated() {
    this.authConfirmedUntil = Date.now() + AUTH_CONFIRMATION_TTL_MS;
  }

  authEvidenceIsValid(evidence) {
    return hasAuthenticatedJdSession(evidence?.cookies)
      || isAuthenticatedJdAccountPage(evidence);
  }

  async login() {
    const initial = await this.readAuthEvidence({ probeAccountPage: true });
    if (this.authEvidenceIsValid(initial)) {
      this.rememberAuthenticated();
      await this.screenshot('login-ok');
      return { ok: true };
    }
    process.stdout.write('请在当前 Edge 的京东页面中正常登录。登录完成后，程序会自动识别；不要反复刷新或切换账号。\n');
    const deadline = Date.now() + 10 * 60_000;
    while (Date.now() < deadline) {
      const result = await this.readAuthEvidence();
      if (this.authEvidenceIsValid(result)) {
        this.rememberAuthenticated();
        await this.screenshot('login-ok');
        return { ok: true };
      }
      await this.pause(1500);
    }
    return { ok: false, reason: '十分钟内没有检测到登录完成' };
  }

  async ensureLoggedIn() {
    if (this.authConfirmedUntil > Date.now()) return true;
    const result = await this.readAuthEvidence({ probeAccountPage: true });
    const authenticated = this.authEvidenceIsValid(result);
    if (authenticated) this.rememberAuthenticated();
    return authenticated;
  }

  searchStatePath() {
    return path.join(this.config.paths.dataDir, 'jd-search-state.json');
  }

  readSearchState() {
    try {
      const value = JSON.parse(fs.readFileSync(this.searchStatePath(), 'utf8'));
      return value && typeof value === 'object' ? value : {};
    } catch {
      return {};
    }
  }

  writeSearchState(value) {
    fs.mkdirSync(this.config.paths.dataDir, { recursive: true });
    const target = this.searchStatePath();
    const temporary = `${target}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2));
    fs.renameSync(temporary, target);
  }

  async waitForSearchSlot(deadlineEpoch = 0) {
    const state = this.readSearchState();
    const blockedUntil = Number(state.blockedUntil || 0);
    if (blockedUntil > Date.now()) throw searchCooldownError(blockedUntil);
    const lastSearchAt = Number(state.lastSearchAt || 0);
    const waitMs = Math.max(0, lastSearchAt + MIN_SEARCH_INTERVAL_MS - Date.now());
    if (deadlineEpoch && Date.now() + waitMs + 8_000 >= deadlineEpoch) throw searchDeadlineError();
    if (waitMs > 0) await this.pause(waitMs);
    this.writeSearchState({
      lastSearchAt: Date.now(),
      blockedUntil: 0,
      reason: '',
      updatedAt: new Date().toISOString()
    });
  }

  markSearchRateLimited() {
    const now = Date.now();
    this.writeSearchState({
      lastSearchAt: now,
      blockedUntil: now + SEARCH_RATE_LIMIT_COOLDOWN_MS,
      reason: 'rate_limited',
      updatedAt: new Date(now).toISOString()
    });
  }

  async search(query, limit = 8, { deadlineEpoch = 0 } = {}) {
    if (!await this.ensureLoggedIn()) {
      const error = new Error('京东登录状态已过期；旧脚本曾把访客 Cookie 误当成登录，请重新运行“开始惊喜购物.ps1”并完成京东登录');
      error.code = 'JD_LOGIN_REQUIRED';
      throw error;
    }
    await this.waitForSearchSlot(deadlineEpoch);
    const remainingMs = deadlineEpoch ? deadlineEpoch - Date.now() : 25_000;
    if (remainingMs < 8_000) throw searchDeadlineError();
    const navigationTimeoutMs = Math.max(5_000, Math.min(35_000, remainingMs - 5_000));
    const encoded = encodeURIComponent(query);
    const url = `https://search.jd.com/Search?keyword=${encoded}&enc=utf-8&wq=${encoded}`;
    const pageResult = await this.runPage(`
      await page.goto(input.url, { waitUntil: 'domcontentloaded', timeout: input.navigationTimeoutMs });
      await page.waitForTimeout(1800);
      await page.mouse.wheel(0, 900);
      await page.waitForTimeout(700);
      const parsed = await page.evaluate((maxItems) => {
        const roots = [
          ...document.querySelectorAll('li.gl-item[data-sku], .gl-item[data-sku], [class*="goods"] [data-sku]')
        ];
        const fallback = roots.length ? [] : [...document.querySelectorAll('a[href*="item.jd.com/"]')]
          .map((link) => link.closest('li, article, [data-sku], div') || link);
        const items = [...new Set([...roots, ...fallback])].slice(0, maxItems * 3);
        return {
          pageText: String(document.body?.innerText || '').slice(0, 20_000),
          items: items.map((root) => {
            const link = root.matches?.('a[href*="item.jd.com/"]') ? root : root.querySelector?.('a[href*="item.jd.com/"]');
            const titleEl = root.querySelector?.('.p-name em, .p-name a, [class*="name"] em, [class*="name"] a, [title]');
            const priceEl = root.querySelector?.('.p-price i, .J_price, [class*="price"] i, [class*="price"]');
            const shopEl = root.querySelector?.('.p-shop, [class*="shop"]');
            const reviewEl = root.querySelector?.('.p-commit, [class*="comment"], [class*="review"]');
            return {
              sku: root.getAttribute?.('data-sku') || '',
              url: link?.href || '',
              title: (titleEl?.textContent || link?.getAttribute?.('title') || link?.textContent || '').trim(),
              priceCny: (priceEl?.textContent || '').trim(),
              shop: (shopEl?.textContent || '').trim(),
              reviews: (reviewEl?.textContent || '').trim()
            };
          })
        };
      }, input.limit);
      return { ...parsed, url: page.url() };
    `, { url, limit, navigationTimeoutMs }, { timeoutMs: navigationTimeoutMs + 5_000 });
    if (/passport\.jd\.com|verify|captcha/i.test(pageResult?.url || '')) {
      throw new Error('京东要求登录或验证码，请先运行 npm run login');
    }
    const seen = new Set();
    const results = [];
    for (const item of pageResult?.items || []) {
      const sku = normalizeSku(item.sku || item.url);
      const href = item.url?.startsWith('//') ? `https:${item.url}` : item.url;
      if (!sku || seen.has(sku) || !isAllowedJdUrl(href)) continue;
      const priceCny = parseCny(item.priceCny);
      if (!item.title || priceCny === null) continue;
      seen.add(sku);
      results.push({ ...item, sku, url: href, priceCny });
      if (results.length >= limit) break;
    }
    await this.screenshot(results.length ? 'search' : 'search-empty');
    if (!results.length && isJdSearchUnavailableText(pageResult?.pageText)) {
      this.markSearchRateLimited();
      throw jdSearchUnavailableError();
    }
    return results;
  }

  async addToCart(candidate) {
    if (!isAllowedJdUrl(candidate?.url)) throw new Error('候选商品链接不是京东官方 HTTPS 地址');
    await this.runPage(`
      ${PAGE_HELPERS}
      await page.goto(input.url, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1300);
      const match = page.url().match(/\\\/(\\d{5,})\\.html/);
      if (!match || match[1] !== input.sku) throw new Error('打开后的商品 SKU 与选择结果不一致');
      const button = await firstVisible(page, [
        '#InitCartUrl', '#btn-reservation', '.btn-add', '[class*="addcart"]',
        'a:has-text("加入购物车")', 'button:has-text("加入购物车")'
      ]);
      if (!button) throw new Error('找不到“加入购物车”按钮，可能需要选择规格或页面已改变');
      await button.click();
      await page.waitForTimeout(1200);
      return { ok: true, url: page.url() };
    `, { url: candidate.url, sku: candidate.sku });
    await this.screenshot('added-to-cart');
  }

  async prepareCheckout(candidate, budgetCny) {
    await this.runPage(`
      ${PAGE_HELPERS}
      await page.goto('https://cart.jd.com/cart_index', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1500);
      const items = page.locator('[data-sku].item-item, .item-item[data-sku], [class*="item"] [data-sku]');
      const count = await items.count();
      let targetFound = false;
      for (let index = 0; index < count; index += 1) {
        const item = items.nth(index);
        const rawSku = String(await item.getAttribute('data-sku') || '');
        const sku = rawSku.match(/(\\d{5,})/)?.[1] || '';
        const checkbox = item.locator('input[type="checkbox"], .jdcheckbox').first();
        if (!await checkbox.isVisible().catch(() => false)) continue;
        const checked = await checkbox.isChecked().catch(() => item.getAttribute('class').then((value) => /checked|selected/.test(value || '')));
        if (sku === input.sku) {
          targetFound = true;
          if (!checked) await checkbox.click();
        } else if (checked) {
          await checkbox.click();
        }
      }
      if (!targetFound) throw new Error('购物车中没有找到刚才选择的商品，已停止');
      if (!await clickByTexts(page, ['去结算'])) throw new Error('找不到“去结算”按钮，已停止');
      await page.waitForLoadState('domcontentloaded').catch(() => {});
      await page.waitForTimeout(1400);
      return { ok: true, url: page.url() };
    `, { sku: candidate.sku });
    return this.verifyCheckout(candidate.sku, budgetCny);
  }

  async verifyCheckout(expectedSku, budgetCny) {
    const pageResult = await this.runPage(`
      ${PAGE_HELPERS}
      let productLinks = page.locator([
        '.goods-list a[href*="item.jd.com/"]', '.goods-item a[href*="item.jd.com/"]',
        '.p-list a[href*="item.jd.com/"]', '.order-summary a[href*="item.jd.com/"]'
      ].join(','));
      if (await productLinks.count() === 0) productLinks = page.locator('a[href*="item.jd.com/"]');
      const selectedSkus = await productLinks.evaluateAll((links) =>
        links.map((link) => link.href.match(/\\\/(\\d{5,})\\.html/)?.[1]).filter(Boolean)
      );
      const totalEl = await firstVisible(page, [
        '.price-num', '#sumPayPriceId', '.payment-price strong',
        '[class*="pay"] [class*="price"]', '[class*="total"] [class*="price"]'
      ]);
      return { selectedSkus, totalText: totalEl ? await totalEl.textContent() : '', url: page.url() };
    `);
    const verified = verifySingleSku({
      expectedSku,
      selectedSkus: pageResult?.selectedSkus || [],
      totalCny: pageResult?.totalText || '',
      budgetCny
    });
    await this.screenshot('checkout-verified');
    if (!verified.ok) throw new Error(verified.reason);
    this.checkoutVerifiedSku = normalizeSku(expectedSku);
    this.checkoutVerifiedTotalCny = verified.totalCny;
    return verified;
  }

  async submitOrder() {
    if (!this.checkoutVerifiedSku || this.checkoutVerifiedTotalCny === null) {
      throw new Error('结算页尚未通过 SKU 和总额核对，不能提交订单');
    }
    const result = await this.runPage(`
      ${PAGE_HELPERS}
      const host = new URL(page.url()).hostname.toLowerCase();
      if (host !== 'trade.jd.com' && host !== 'order.jd.com') {
        throw new Error('当前页面不是京东官方结算页，不能提交订单');
      }
      if (!await clickByTexts(page, ['提交订单'])) throw new Error('找不到“提交订单”按钮，已停止');
      await page.waitForLoadState('domcontentloaded').catch(() => {});
      await page.waitForTimeout(1500);
      return { ok: true, url: page.url() };
    `);
    await this.screenshot('order-submitted');
    return { ok: true, url: result?.url || '' };
  }

  async tryPayWithBalance(budgetCny, expectedTotalCny) {
    const pageResult = await this.runPage(`
      ${PAGE_HELPERS}
      if (/passport\\.jd\\.com|verify|captcha/i.test(page.url())) {
        return { needsVerification: true, reason: '京东要求登录或验证码', url: page.url() };
      }
      await clickByTexts(page, ['京东余额', '余额']);
      const password = await firstVisible(page, ['input[type="password"]', 'iframe[title*="支付"]']);
      if (password) return { needsVerification: true, reason: '京东要求支付密码或额外验证', url: page.url() };
      const totalEl = await firstVisible(page, [
        '.pay-price', '.payment-price', '#payPrice', '[class*="payAmount"]', '[class*="pay-amount"]'
      ]);
      return { totalText: totalEl ? await totalEl.textContent() : '', url: page.url() };
    `);
    if (pageResult?.needsVerification) return { ok: false, needsVerification: true, reason: pageResult.reason };
    const total = parseCny(pageResult?.totalText);
    const expected = parseCny(expectedTotalCny);
    if (total === null || expected === null || total <= 0 || total > Number(budgetCny) || Math.abs(total - expected) > 0.01) {
      return { ok: false, reason: '支付页金额无法安全确认，未点击支付' };
    }
    const paymentResult = await this.runPage(`
      ${PAGE_HELPERS}
      const text = String(input.totalText || '').replace(/,/g, '');
      const match = text.match(/(?:¥|￥)?\\s*(-?\\d+(?:\\.\\d{1,2})?)/);
      const total = match ? Number(match[1]) : NaN;
      if (!Number.isFinite(total) || total <= 0 || total > input.budgetCny || Math.abs(total - input.expectedTotalCny) > 0.01) {
        throw new Error('支付页金额在点击前发生变化，未点击支付');
      }
      if (!await clickByTexts(page, ['立即支付', '确认支付'])) {
        return { clicked: false, url: page.url() };
      }
      await page.waitForTimeout(1800);
      return {
        clicked: true,
        url: page.url(),
        successText: /支付成功|下单成功/.test(String(await page.locator('body').innerText().catch(() => '')))
      };
    `, { totalText: pageResult?.totalText || '', budgetCny: Number(budgetCny), expectedTotalCny: expected });
    if (!paymentResult?.clicked) return { ok: false, needsVerification: true, reason: '京东没有提供可自动点击的余额支付按钮' };
    await this.screenshot('payment-result');
    if (/success|done|paySuccess|支付成功/i.test(paymentResult?.url || '') || paymentResult?.successText) {
      return { ok: true };
    }
    return { ok: false, needsVerification: true, reason: '已发起余额支付，但京东仍要求完成验证' };
  }
}
