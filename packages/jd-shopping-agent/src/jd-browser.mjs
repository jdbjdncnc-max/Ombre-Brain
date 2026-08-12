import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { isAllowedJdUrl, normalizeSku, parseCny, verifySingleSku } from './safety.mjs';

function browserExecutable(channel) {
  const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const edge64 = 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe';
  const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  if (String(channel).toLowerCase() === 'chrome' && fs.existsSync(chrome)) return chrome;
  if (fs.existsSync(edge)) return edge;
  if (fs.existsSync(edge64)) return edge64;
  if (fs.existsSync(chrome)) return chrome;
  throw new Error('没有找到 Microsoft Edge 或 Google Chrome');
}

async function firstVisible(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible().catch(() => false)) return locator;
  }
  return null;
}

async function clickByText(page, patterns) {
  for (const pattern of patterns) {
    const matches = page.getByText(pattern, { exact: false });
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

export class JdBrowser {
  constructor(config) {
    this.config = config;
    this.context = null;
    this.page = null;
    this.checkoutVerifiedSku = '';
    this.checkoutVerifiedTotalCny = null;
  }

  async start() {
    fs.mkdirSync(this.config.browser.profileDir, { recursive: true });
    fs.mkdirSync(this.config.paths.screenshots, { recursive: true });
    this.context = await chromium.launchPersistentContext(this.config.browser.profileDir, {
      executablePath: browserExecutable(this.config.browser.channel),
      headless: this.config.browser.headless,
      slowMo: this.config.browser.slowMoMs,
      viewport: { width: 1280, height: 840 },
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai'
    });
    this.page = this.context.pages()[0] || await this.context.newPage();
    this.page.setDefaultTimeout(15000);
    return this;
  }

  async close() {
    await this.context?.close();
  }

  async screenshot(name) {
    const safe = String(name).replace(/[^a-z0-9_-]+/gi, '-');
    const target = path.join(this.config.paths.screenshots, `${Date.now()}-${safe}.png`);
    await this.page.screenshot({ path: target, fullPage: false }).catch(() => {});
    return target;
  }

  async login() {
    await this.page.goto('https://passport.jd.com/new/login.aspx', { waitUntil: 'domcontentloaded' });
    process.stdout.write('请在打开的 Edge 中登录京东。登录完成后，程序会自动保存登录状态。\n');
    const deadline = Date.now() + 10 * 60_000;
    while (Date.now() < deadline) {
      const cookies = await this.context.cookies('https://jd.com');
      if (cookies.some((cookie) => ['pin', 'pt_key', 'thor'].includes(cookie.name) && cookie.value)) {
        await this.screenshot('login-ok');
        return { ok: true };
      }
      await this.page.waitForTimeout(1500);
    }
    return { ok: false, reason: '十分钟内没有检测到登录完成' };
  }

  async ensureLoggedIn() {
    const cookies = await this.context.cookies('https://jd.com');
    return cookies.some((cookie) => ['pin', 'pt_key', 'thor'].includes(cookie.name) && cookie.value);
  }

  async search(query, limit = 8) {
    const url = `https://search.jd.com/Search?keyword=${encodeURIComponent(query)}&enc=utf-8`;
    await this.page.goto(url, { waitUntil: 'domcontentloaded' });
    await this.page.waitForTimeout(1800);
    await this.page.mouse.wheel(0, 900);
    await this.page.waitForTimeout(900);
    if (/passport\.jd\.com|verify|captcha/i.test(this.page.url())) {
      throw new Error('京东要求登录或验证码，请先运行 npm run login');
    }
    const raw = await this.page.evaluate((maxItems) => {
      const roots = [
        ...document.querySelectorAll('li.gl-item[data-sku], .gl-item[data-sku], [class*="goods"] [data-sku]')
      ];
      const fallback = roots.length ? [] : [...document.querySelectorAll('a[href*="item.jd.com/"]')]
        .map((link) => link.closest('li, article, [data-sku], div') || link);
      const items = [...new Set([...roots, ...fallback])].slice(0, maxItems * 3);
      return items.map((root) => {
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
      });
    }, limit);
    const seen = new Set();
    const results = [];
    for (const item of raw) {
      const sku = normalizeSku(item.sku || item.url);
      const href = item.url?.startsWith('//') ? `https:${item.url}` : item.url;
      if (!sku || seen.has(sku) || !isAllowedJdUrl(href)) continue;
      const priceCny = parseCny(item.priceCny);
      if (!item.title || priceCny === null) continue;
      seen.add(sku);
      results.push({ ...item, sku, url: href, priceCny });
      if (results.length >= limit) break;
    }
    await this.screenshot('search');
    return results;
  }

  async addToCart(candidate) {
    await this.page.goto(candidate.url, { waitUntil: 'domcontentloaded' });
    await this.page.waitForTimeout(1300);
    if (normalizeSku(this.page.url()) !== candidate.sku) throw new Error('打开后的商品 SKU 与选择结果不一致');
    const button = await firstVisible(this.page, [
      '#InitCartUrl', '#btn-reservation', '.btn-add', '[class*="addcart"]', 'a:has-text("加入购物车")', 'button:has-text("加入购物车")'
    ]);
    if (!button) throw new Error('找不到“加入购物车”按钮，可能需要选择规格或页面已改变');
    await button.click();
    await this.page.waitForTimeout(1200);
    await this.screenshot('added-to-cart');
  }

  async prepareCheckout(candidate, budgetCny) {
    await this.page.goto('https://cart.jd.com/cart_index', { waitUntil: 'domcontentloaded' });
    await this.page.waitForTimeout(1500);
    const items = this.page.locator('[data-sku].item-item, .item-item[data-sku], [class*="item"] [data-sku]');
    const count = await items.count();
    let targetFound = false;
    for (let i = 0; i < count; i += 1) {
      const item = items.nth(i);
      const sku = normalizeSku(await item.getAttribute('data-sku'));
      const checkbox = item.locator('input[type="checkbox"], .jdcheckbox').first();
      if (!await checkbox.isVisible().catch(() => false)) continue;
      const checked = await checkbox.isChecked().catch(() => item.getAttribute('class').then((v) => /checked|selected/.test(v || '')));
      if (sku === candidate.sku) {
        targetFound = true;
        if (!checked) await checkbox.click();
      } else if (checked) {
        await checkbox.click();
      }
    }
    if (!targetFound) throw new Error('购物车中没有找到刚才选择的商品，已停止');
    const checkoutClicked = await clickByText(this.page, [/去结算/]);
    if (!checkoutClicked) throw new Error('找不到“去结算”按钮，已停止');
    await this.page.waitForLoadState('domcontentloaded').catch(() => {});
    await this.page.waitForTimeout(1400);
    return this.verifyCheckout(candidate.sku, budgetCny);
  }

  async verifyCheckout(expectedSku, budgetCny) {
    let productLinks = this.page.locator([
      '.goods-list a[href*="item.jd.com/"]', '.goods-item a[href*="item.jd.com/"]',
      '.p-list a[href*="item.jd.com/"]', '.order-summary a[href*="item.jd.com/"]'
    ].join(','));
    if (await productLinks.count() === 0) productLinks = this.page.locator('a[href*="item.jd.com/"]');
    const selectedSkus = await productLinks.evaluateAll((links) =>
      links.map((link) => link.href.match(/\/(\d{5,})\.html/)?.[1]).filter(Boolean)
    );
    const totalEl = await firstVisible(this.page, [
      '.price-num', '#sumPayPriceId', '.payment-price strong', '[class*="pay"] [class*="price"]', '[class*="total"] [class*="price"]'
    ]);
    const totalText = totalEl ? await totalEl.textContent() : '';
    const verified = verifySingleSku({ expectedSku, selectedSkus, totalCny: totalText, budgetCny });
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
    const host = new URL(this.page.url()).hostname.toLowerCase();
    if (host !== 'trade.jd.com' && host !== 'order.jd.com') {
      throw new Error('当前页面不是京东官方结算页，不能提交订单');
    }
    const clicked = await clickByText(this.page, [/提交订单/]);
    if (!clicked) throw new Error('找不到“提交订单”按钮，已停止');
    await this.page.waitForLoadState('domcontentloaded').catch(() => {});
    await this.page.waitForTimeout(1500);
    await this.screenshot('order-submitted');
    return { ok: true, url: this.page.url() };
  }

  async tryPayWithBalance(budgetCny, expectedTotalCny) {
    if (/passport\.jd\.com|verify|captcha/i.test(this.page.url())) {
      return { ok: false, needsVerification: true, reason: '京东要求登录或验证码' };
    }
    await clickByText(this.page, [/京东余额/, /^余额$/]);
    const password = await firstVisible(this.page, ['input[type="password"]', 'iframe[title*="支付"]']);
    if (password) return { ok: false, needsVerification: true, reason: '京东要求支付密码或额外验证' };
    const totalEl = await firstVisible(this.page, [
      '.pay-price', '.payment-price', '#payPrice', '[class*="payAmount"]', '[class*="pay-amount"]'
    ]);
    const total = totalEl ? parseCny(await totalEl.textContent()) : null;
    const expected = parseCny(expectedTotalCny);
    if (total === null || expected === null || total <= 0 || total > Number(budgetCny) || Math.abs(total - expected) > 0.01) {
      return { ok: false, reason: '支付页金额无法安全确认，未点击支付' };
    }
    const paid = await clickByText(this.page, [/立即支付/, /确认支付/]);
    if (!paid) return { ok: false, needsVerification: true, reason: '京东没有提供可自动点击的余额支付按钮' };
    await this.page.waitForTimeout(1800);
    await this.screenshot('payment-result');
    if (/success|done|paySuccess|支付成功/i.test(this.page.url()) || await this.page.getByText(/支付成功|下单成功/).isVisible().catch(() => false)) {
      return { ok: true };
    }
    return { ok: false, needsVerification: true, reason: '已发起余额支付，但京东仍要求完成验证' };
  }
}
