import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const BLOCKED_TERMS = [
  '充值', '话费', '流量包', '游戏点卡', '京东e卡', '礼品卡', '会员', '自动续费',
  '彩票', '保险', '理财', '基金', '黄金投资', '处方', '药品', '医疗器械', '保健品',
  '香烟', '电子烟', '白酒', '红酒', '啤酒', '成人用品', '二手', '拍卖', '酒店',
  '机票', '门票', '上门服务', '安装服务', '延保服务', '虚拟商品'
];

export function parseCny(value) {
  const text = String(value ?? '').replace(/[,，\s￥¥]/g, '');
  const match = text.match(/-?\d+(?:\.\d{1,2})?/);
  if (!match) return null;
  const amount = Number(match[0]);
  return Number.isFinite(amount) ? Math.round(amount * 100) / 100 : null;
}

export function normalizeSku(value) {
  const match = String(value ?? '').match(/(?:item\.jd\.com\/)?(\d{5,})/i);
  return match?.[1] ?? '';
}

export function isAllowedJdUrl(value) {
  try {
    const url = new URL(String(value));
    const host = url.hostname.toLowerCase();
    return url.protocol === 'https:' && (host === 'jd.com' || host.endsWith('.jd.com') || host === '3.cn');
  } catch {
    return false;
  }
}

export function inspectCandidate(candidate, budgetCny, extraBlocked = []) {
  const title = String(candidate?.title ?? '').trim();
  const priceCny = parseCny(candidate?.priceCny);
  const sku = normalizeSku(candidate?.sku || candidate?.url);
  const url = String(candidate?.url ?? '');
  const blockedTerms = [...BLOCKED_TERMS, ...extraBlocked.map(String)];
  const blockedTerm = blockedTerms.find((term) => term && title.toLowerCase().includes(term.toLowerCase()));

  if (!title) return { ok: false, reason: '商品没有可核对的标题' };
  if (!sku) return { ok: false, reason: '商品没有可核对的京东 SKU' };
  if (!isAllowedJdUrl(url)) return { ok: false, reason: '商品链接不属于京东' };
  if (priceCny === null || priceCny <= 0) return { ok: false, reason: '商品价格无法确认' };
  if (priceCny > Number(budgetCny)) return { ok: false, reason: '商品价格超过预算' };
  if (blockedTerm) return { ok: false, reason: `商品属于禁止范围：${blockedTerm}` };
  return { ok: true, candidate: { ...candidate, title, priceCny, sku, url } };
}

export function verifySingleSku({ expectedSku, selectedSkus, totalCny, budgetCny }) {
  const expected = normalizeSku(expectedSku);
  const selected = [...new Set((selectedSkus ?? []).map(normalizeSku).filter(Boolean))];
  const total = parseCny(totalCny);
  if (!expected) return { ok: false, reason: '预期商品 SKU 无效' };
  if (selected.length !== 1 || selected[0] !== expected) {
    return { ok: false, reason: '结算页中不是且仅不是目标商品，已停止' };
  }
  if (total === null || total <= 0) return { ok: false, reason: '无法确认结算总额，已停止' };
  if (total > Number(budgetCny)) return { ok: false, reason: '结算总额超过授权预算，已停止' };
  return { ok: true, totalCny: total };
}

function authorizationMac(payload, secret) {
  return crypto.createHmac('sha256', secret).update(JSON.stringify(payload)).digest('hex');
}

export function createAuthorization({ budgetCny, expiresAt, secret }) {
  const budget = parseCny(budgetCny);
  if (budget === null || budget < 10 || budget > 5000) {
    throw new Error('预算必须在 10～5000 元之间');
  }
  const expires = new Date(expiresAt);
  if (Number.isNaN(expires.getTime()) || expires.getTime() <= Date.now()) {
    throw new Error('授权截止时间必须晚于现在');
  }
  const payload = {
    version: 1,
    budgetCny: budget,
    maxOrders: 1,
    quantity: 1,
    autoSubmitOrder: true,
    expiresAt: expires.toISOString(),
    nonce: crypto.randomUUID()
  };
  return { ...payload, mac: authorizationMac(payload, secret) };
}

export function verifyAuthorization(auth, secret) {
  if (!auth || typeof auth !== 'object') return { ok: false, reason: '尚未完成预算授权' };
  const { mac, ...payload } = auth;
  if (!mac || authorizationMac(payload, secret) !== mac) return { ok: false, reason: '预算授权文件已被修改' };
  if (payload.maxOrders !== 1 || payload.quantity !== 1 || payload.autoSubmitOrder !== true) {
    return { ok: false, reason: '预算授权规则无效' };
  }
  if (new Date(payload.expiresAt).getTime() <= Date.now()) return { ok: false, reason: '预算授权已过期' };
  return { ok: true, authorization: payload };
}

export function createStandingAuthorization({
  perOrderCapCny,
  monthlyCapCny,
  maxOrdersPerMonth,
  expiresAt,
  autoSubmitOrder = true,
  tryJdBalancePayment = true,
  secret
}) {
  const perOrder = parseCny(perOrderCapCny);
  const monthly = parseCny(monthlyCapCny);
  const maxOrders = Number(maxOrdersPerMonth);
  if (perOrder === null || perOrder < 10 || perOrder > 5000) {
    throw new Error('单笔额度必须在 10～5000 元之间');
  }
  if (monthly === null || monthly < perOrder || monthly > 20000) {
    throw new Error('每月额度必须不低于单笔额度，并且不超过 20000 元');
  }
  if (!Number.isInteger(maxOrders) || maxOrders < 1 || maxOrders > 20) {
    throw new Error('每月订单数必须在 1～20 之间');
  }
  const expires = new Date(expiresAt);
  if (Number.isNaN(expires.getTime()) || expires.getTime() <= Date.now()) {
    throw new Error('授权截止时间必须晚于现在');
  }
  const payload = {
    version: 2,
    perOrderCapCny: perOrder,
    monthlyCapCny: monthly,
    maxOrdersPerMonth: maxOrders,
    quantityPerOrder: 1,
    physicalGoodsOnly: true,
    autoSubmitOrder: autoSubmitOrder === true,
    tryJdBalancePayment: tryJdBalancePayment === true,
    expiresAt: expires.toISOString(),
    nonce: crypto.randomUUID()
  };
  return { ...payload, mac: authorizationMac(payload, secret) };
}

export function verifyStandingAuthorization(auth, secret) {
  if (!auth || typeof auth !== 'object') return { ok: false, reason: '尚未完成长期购物额度授权' };
  const { mac, ...payload } = auth;
  if (!mac || authorizationMac(payload, secret) !== mac) return { ok: false, reason: '长期购物授权文件已被修改' };
  if (payload.version !== 2 || payload.quantityPerOrder !== 1 || payload.physicalGoodsOnly !== true) {
    return { ok: false, reason: '长期购物授权规则无效' };
  }
  if (new Date(payload.expiresAt).getTime() <= Date.now()) return { ok: false, reason: '长期购物授权已过期' };
  if (parseCny(payload.perOrderCapCny) === null || parseCny(payload.monthlyCapCny) === null) {
    return { ok: false, reason: '长期购物额度无效' };
  }
  return { ok: true, authorization: payload };
}

export function monthKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

export function checkStandingAllowance({ authorization, ledger, requestedBudgetCny, finalTotalCny = null, now = new Date() }) {
  const requested = parseCny(requestedBudgetCny);
  const total = finalTotalCny === null ? null : parseCny(finalTotalCny);
  if (requested === null || requested <= 0 || requested > authorization.perOrderCapCny) {
    return { ok: false, reason: '本次预算超过本地单笔授权额度' };
  }
  if (total !== null && (total <= 0 || total > requested || total > authorization.perOrderCapCny)) {
    return { ok: false, reason: '结算总额超过本次或本地单笔授权额度' };
  }
  const key = monthKey(now);
  const orders = Array.isArray(ledger?.orders) ? ledger.orders : [];
  const counted = orders.filter((order) => order?.month === key && order?.orderSubmitted === true);
  const spent = counted.reduce((sum, order) => sum + (parseCny(order.totalCny) || 0), 0);
  if (counted.length >= authorization.maxOrdersPerMonth) {
    return { ok: false, reason: '本月已达到授权订单数量上限' };
  }
  const amountToReserve = total ?? requested;
  if (spent + amountToReserve > authorization.monthlyCapCny) {
    return { ok: false, reason: '本次购买会超过本地每月总额度' };
  }
  return {
    ok: true,
    month: key,
    ordersUsed: counted.length,
    spentCny: Math.round(spent * 100) / 100,
    remainingCny: Math.round((authorization.monthlyCapCny - spent) * 100) / 100
  };
}

export function ensureLocalSecret(dataDir) {
  fs.mkdirSync(dataDir, { recursive: true });
  const secretPath = path.join(dataDir, '.authorization-secret');
  if (!fs.existsSync(secretPath)) fs.writeFileSync(secretPath, crypto.randomBytes(32).toString('hex'), { mode: 0o600 });
  return fs.readFileSync(secretPath, 'utf8').trim();
}
