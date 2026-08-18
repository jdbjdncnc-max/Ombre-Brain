import crypto from 'node:crypto';
import path from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { loadConfig } from './config.mjs';
import { loadJson, processShoppingTask } from './agent.mjs';
import { JdBrowser } from './jd-browser.mjs';

const config = loadConfig();
let browserPromise;

function taskId() {
  return `jd_${crypto.randomUUID().replaceAll('-', '')}`;
}

function purchaseTaskId(searchTaskId, sku) {
  return `jd_${crypto.createHash('sha256').update(`${searchTaskId}:${sku}`).digest('hex').slice(0, 32)}`;
}

async function browser() {
  browserPromise ??= new JdBrowser(config).start();
  return browserPromise;
}

function toolResult(value, isError = false) {
  return {
    content: [{ type: 'text', text: JSON.stringify(value) }],
    structuredContent: value,
    isError
  };
}

const server = new McpServer({ name: 'ombre-jd-shopping', version: '0.4.0' });

server.registerTool('search_surprise_gift', {
  title: '搜索京东商品',
  description: '根据当前对话生成两到四个互不重复、预算内可能买到的宽泛品类词，并返回带实时价格的京东真实候选。不要只搜索可能明显超预算的具体型号；若提示超预算，应主动改搜更便宜的品类。提交订单前必须先调用此工具。',
  inputSchema: {
    queries: z.array(z.string().min(1).max(40)).min(1).max(4),
    budgetCny: z.number().min(10).max(5000),
    maxCandidates: z.number().int().min(4).max(30).optional()
  }
}, async (args) => {
  try {
    const id = taskId();
    const result = await processShoppingTask(config, await browser(), {
      id,
      kind: 'search',
      payload: args
    });
    return toolResult({
      ok: true,
      searchTaskId: id,
      candidates: result.candidates
    });
  } catch (error) {
    return toolResult({ ok: false, message: error?.message || String(error) }, true);
  }
});

server.registerTool('submit_authorized_jd_order', {
  title: '提交授权内的京东订单',
  description: '从本次真实搜索候选中选择一个 SKU，并按本地已有额度、品类和防重复规则处理。',
  inputSchema: {
    searchTaskId: z.string().regex(/^jd_[a-f0-9]{32}$/),
    sku: z.string().regex(/^\d{5,24}$/),
    budgetCny: z.number().min(10).max(5000)
  }
}, async (args) => {
  try {
    const search = loadJson(path.join(config.paths.searches, `${args.searchTaskId}.json`), null);
    const candidate = search?.candidates?.find((item) => item?.sku === args.sku);
    if (!candidate) throw new Error('只能购买刚才真实搜索返回的候选商品');
    if (Number(args.budgetCny) > Number(search.budgetCny)) throw new Error('购买预算不能高于搜索时的授权上限');
    const result = await processShoppingTask(config, await browser(), {
      id: purchaseTaskId(args.searchTaskId, args.sku),
      kind: 'purchase',
      payload: { ...args, candidate }
    });
    return toolResult(result, result.ok === false);
  } catch (error) {
    return toolResult({ ok: false, message: error?.message || String(error) }, true);
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
console.error('Ombre JD shopping MCP is running on stdio');

process.on('SIGINT', async () => {
  if (browserPromise) await (await browserPromise).close();
  await server.close();
  process.exit(0);
});
