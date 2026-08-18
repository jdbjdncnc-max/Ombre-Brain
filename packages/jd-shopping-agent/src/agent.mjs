import fs from 'node:fs';
import path from 'node:path';
import { inspectCandidate, ensureLocalSecret, verifyStandingAuthorization, checkStandingAllowance, monthKey } from './safety.mjs';

const SEARCH_TASK_BUDGET_MS = 90_000;

export function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2));
  fs.renameSync(temporary, file);
}

export function loadJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

function safePublicResult(value) {
  return {
    ok: value.ok !== false,
    status: String(value.status || 'completed'),
    orderSubmitted: value.orderSubmitted === true,
    paid: value.paid === true,
    needsVerification: value.needsVerification === true,
    totalCny: value.totalCny ?? null,
    message: String(value.message || '')
  };
}

function currentAuthorization(config) {
  const secret = ensureLocalSecret(config.paths.dataDir);
  return verifyStandingAuthorization(loadJson(config.paths.authorization), secret);
}

function readLedger(config) {
  const value = loadJson(config.paths.ledger, { version: 2, orders: [] });
  return value && Array.isArray(value.orders) ? value : { version: 2, orders: [] };
}

function findExistingTask(ledger, taskId) {
  const found = ledger.orders.find((order) => order?.taskId === taskId);
  if (!found) return null;
  if (found.publicResult) return { completed: true, result: safePublicResult(found.publicResult) };
  return { completed: false, result: null };
}

export async function searchGiftCandidates(config, browser, task) {
  const payload = task?.payload || {};
  const budgetCny = Number(payload.budgetCny);
  const queryKeys = new Set();
  const queries = Array.isArray(payload.queries) ? payload.queries
    .map((value) => String(value).trim())
    .filter((value) => {
      const key = value.toLocaleLowerCase('zh-CN');
      if (!value || queryKeys.has(key)) return false;
      queryKeys.add(key);
      return true;
    })
    .slice(0, 4) : [];
  const maxCandidates = Math.min(30, Math.max(4, Number(payload.maxCandidates || 20)));
  if (!Number.isFinite(budgetCny) || budgetCny <= 0 || !queries.length) throw new Error('搜索任务参数无效');
  if (!await browser.ensureLoggedIn()) throw new Error('京东尚未登录，请先运行登录步骤');

  const candidates = [];
  const deadlineEpoch = Date.now() + SEARCH_TASK_BUDGET_MS;
  let parsedCount = 0;
  const rejectedCounts = new Map();
  for (const query of queries) {
    const found = await browser.search(
      query,
      Math.max(4, Math.ceil(maxCandidates / queries.length)),
      { deadlineEpoch }
    );
    parsedCount += found.length;
    for (const item of found) {
      const inspected = inspectCandidate(item, budgetCny, config.policy.extraBlockedTerms);
      if (inspected.ok && !candidates.some((old) => old.sku === inspected.candidate.sku)) {
        candidates.push(inspected.candidate);
      } else if (!inspected.ok) {
        rejectedCounts.set(inspected.reason, (rejectedCounts.get(inspected.reason) || 0) + 1);
      }
      if (candidates.length >= maxCandidates) break;
    }
    // Two verified choices are enough for the model to choose safely. Avoid
    // hammering JD with every suggested query after a usable page succeeds.
    if (candidates.length >= 2) break;
  }
  if (candidates.length < 2) {
    if (parsedCount === 0) {
      throw new Error('京东搜索页没有返回可解析的商品，可能是页面结构变化；未进入下单');
    }
    const overBudget = rejectedCounts.get('商品价格超过预算') || 0;
    const blocked = [...rejectedCounts.entries()]
      .filter(([reason]) => reason.startsWith('商品属于禁止范围'))
      .reduce((sum, [, count]) => sum + count, 0);
    const other = Math.max(0, parsedCount - candidates.length - overBudget - blocked);
    const details = [
      overBudget ? `${overBudget} 件超过 ${budgetCny} 元预算` : '',
      blocked ? `${blocked} 件属于禁止范围` : '',
      other ? `${other} 件缺少可核对信息` : ''
    ].filter(Boolean).join('，');
    const guidance = overBudget
      ? '请改用更便宜的宽泛品类词，不要继续搜索同一个具体型号'
      : '请换用两到四个不同的宽泛品类词';
    throw new Error(`京东返回了 ${parsedCount} 件可解析商品，但只有 ${candidates.length} 件符合条件${details ? `（${details}）` : ''}；${guidance}`);
  }
  const searchRecord = {
    taskId: task.id,
    createdAt: new Date().toISOString(),
    budgetCny,
    candidates
  };
  writeJson(path.join(config.paths.searches, `${task.id}.json`), searchRecord);
  return { candidates };
}

export async function purchaseGiftCandidate(config, browser, task) {
  const payload = task?.payload || {};
  const taskId = String(task?.id || '');
  const candidate = payload.candidate;
  const budgetCny = Number(payload.budgetCny);
  const auth = currentAuthorization(config);
  if (!auth.ok) throw new Error(auth.reason);
  if (!auth.authorization.autoSubmitOrder) throw new Error('本地长期授权没有允许自动提交订单');
  const ledger = readLedger(config);
  const previous = findExistingTask(ledger, taskId);
  if (previous?.completed) return previous.result;
  if (previous) throw new Error('这项购买已有未完成记录；为防止重复下单，请先人工查看京东订单');

  const initialAllowance = checkStandingAllowance({
    authorization: auth.authorization,
    ledger,
    requestedBudgetCny: budgetCny
  });
  if (!initialAllowance.ok) throw new Error(initialAllowance.reason);
  const inspected = inspectCandidate(candidate, budgetCny, config.policy.extraBlockedTerms);
  if (!inspected.ok) throw new Error(inspected.reason);
  if (!await browser.ensureLoggedIn()) throw new Error('京东尚未登录，请先运行登录步骤');

  const ledgerEntry = {
    taskId,
    searchTaskId: String(payload.searchTaskId || ''),
    sku: inspected.candidate.sku,
    totalCny: null,
    month: monthKey(),
    status: 'in-progress',
    orderSubmitted: false,
    paid: false,
    createdAt: new Date().toISOString()
  };
  ledger.orders.push(ledgerEntry);
  writeJson(config.paths.ledger, ledger);

  let checkout;
  try {
    await browser.addToCart(inspected.candidate);
    checkout = await browser.prepareCheckout(inspected.candidate, budgetCny);
    const finalAllowance = checkStandingAllowance({
      authorization: auth.authorization,
      ledger,
      requestedBudgetCny: budgetCny,
      finalTotalCny: checkout.totalCny
    });
    if (!finalAllowance.ok) throw new Error(finalAllowance.reason);
  } catch (error) {
    ledger.orders = ledger.orders.filter((entry) => entry !== ledgerEntry);
    writeJson(config.paths.ledger, ledger);
    throw error;
  }

  await browser.submitOrder();
  let result = {
    ok: true,
    status: 'order-submitted',
    orderSubmitted: true,
    paid: false,
    needsVerification: false,
    totalCny: checkout.totalCny,
    message: '订单已经提交'
  };
  ledgerEntry.totalCny = checkout.totalCny;
  ledgerEntry.status = 'order-submitted';
  ledgerEntry.orderSubmitted = true;
  ledgerEntry.publicResult = safePublicResult(result);
  writeJson(config.paths.ledger, ledger);

  if (auth.authorization.tryJdBalancePayment && config.policy.tryJdBalancePayment) {
    const payment = await browser.tryPayWithBalance(budgetCny, checkout.totalCny);
    result = {
      ...result,
      status: payment.ok ? 'paid' : payment.needsVerification ? 'payment-verification-needed' : 'payment-stopped',
      paid: payment.ok === true,
      needsVerification: payment.needsVerification === true,
      message: payment.ok ? '京东余额支付完成' : payment.reason || '支付没有完成'
    };
  }

  const publicResult = safePublicResult(result);
  ledgerEntry.status = publicResult.status;
  ledgerEntry.paid = publicResult.paid;
  ledgerEntry.publicResult = publicResult;
  writeJson(config.paths.ledger, ledger);
  return publicResult;
}

export async function processShoppingTask(config, browser, task) {
  if (task?.kind === 'search') return searchGiftCandidates(config, browser, task);
  if (task?.kind === 'purchase') return purchaseGiftCandidate(config, browser, task);
  throw new Error('本地购物助手收到未知任务');
}

export function localStatus(config) {
  const auth = currentAuthorization(config);
  const ledger = readLedger(config);
  const allowance = auth.ok ? checkStandingAllowance({
    authorization: auth.authorization,
    ledger,
    requestedBudgetCny: Math.min(auth.authorization.perOrderCapCny, 10)
  }) : null;
  return {
    authorizationOk: auth.ok,
    authorizationReason: auth.ok ? '' : auth.reason,
    expiresAt: auth.authorization?.expiresAt || '',
    perOrderCapCny: auth.authorization?.perOrderCapCny || null,
    monthlyCapCny: auth.authorization?.monthlyCapCny || null,
    maxOrdersPerMonth: auth.authorization?.maxOrdersPerMonth || null,
    ordersThisMonth: ledger.orders.filter((order) => order.month === monthKey() && order.orderSubmitted).length,
    spentThisMonthCny: ledger.orders
      .filter((order) => order.month === monthKey() && order.orderSubmitted)
      .reduce((sum, order) => sum + Number(order.totalCny || 0), 0),
    allowanceOk: allowance?.ok ?? false
  };
}
