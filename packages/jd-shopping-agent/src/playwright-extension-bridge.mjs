import dns from 'node:dns';
import { createConnection } from '@playwright/mcp';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';

function textContent(result) {
  return (Array.isArray(result?.content) ? result.content : [])
    .filter((item) => item?.type === 'text')
    .map((item) => String(item.text || ''))
    .join('\n');
}

function section(text, name) {
  const marker = `### ${name}\n`;
  const start = text.indexOf(marker);
  if (start < 0) return '';
  const bodyStart = start + marker.length;
  const next = text.indexOf('\n### ', bodyStart);
  return text.slice(bodyStart, next < 0 ? text.length : next).trim();
}

function friendlyExtensionError(message) {
  const text = String(message || '').trim();
  if (/Playwright Extension not found|Make sure the .*Extension.* installed/i.test(text)) {
    return new Error('没有在当前浏览器个人资料中检测到 Playwright Extension。请确认扩展安装在你平时登录京东所用的 Edge 个人资料里，并保持 Edge 打开。');
  }
  if (/Extension connection timeout|extension.*timeout/i.test(text)) {
    return new Error('连接浏览器扩展超时。请回到 Edge，在 Playwright 授权页选择已经登录京东的标签页，然后点击“Allow & select（允许并选择）”。');
  }
  if (/Extension not connected|WebSocket closed|Server stopped/i.test(text)) {
    return new Error('Playwright 扩展连接已经断开。请保持 Edge 和已授权的京东标签页打开，然后重新启动本地助手。');
  }
  return new Error(text || 'Playwright 扩展调用失败');
}

export function parsePlaywrightToolResult(result) {
  const text = textContent(result);
  if (result?.isError) throw friendlyExtensionError(section(text, 'Error') || text);
  const payload = section(text, 'Result');
  if (!payload) return undefined;
  try {
    return JSON.parse(payload);
  } catch {
    throw new Error('浏览器扩展返回了无法识别的结果，本地助手已停止，未继续操作京东。');
  }
}

function extensionChannel(channel) {
  return String(channel || '').toLowerCase() === 'chrome' ? 'chrome' : 'msedge';
}

export class PlaywrightExtensionBridge {
  constructor({ channel = 'edge', outputDir } = {}) {
    this.channel = extensionChannel(channel);
    this.outputDir = outputDir;
    this.server = null;
    this.client = null;
    this.previousBrowser = undefined;
    this.previousDnsOrder = undefined;
    this.firstToolCall = true;
  }

  async start() {
    if (this.client) return this;
    this.previousBrowser = process.env.PLAYWRIGHT_MCP_BROWSER;
    this.previousDnsOrder = dns.getDefaultResultOrder?.();
    // Playwright's extension relay binds to localhost. Prefer IPv4 because
    // some Windows TUN/proxy setups do not pass extension WebSockets to ::1.
    dns.setDefaultResultOrder('ipv4first');
    process.env.PLAYWRIGHT_MCP_BROWSER = this.channel;
    this.server = await createConnection({
      extension: true,
      capabilities: ['core'],
      outputDir: this.outputDir,
      imageResponses: 'omit',
      snapshot: { mode: 'none' },
      codegen: 'none',
      timeouts: { action: 15_000, navigation: 60_000, settle: 500 }
    });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    this.client = new Client({ name: 'ombre-jd-shopping', version: '0.4.0' }, { capabilities: {} });
    await this.server.connect(serverTransport);
    await this.client.connect(clientTransport);
    const { tools } = await this.client.listTools();
    if (!tools.some((tool) => tool.name === 'browser_run_code_unsafe')) {
      throw new Error('Playwright 扩展缺少本地助手需要的浏览器工具。');
    }
    return this;
  }

  async runPageCode(code, { timeoutMs } = {}) {
    if (!this.client) await this.start();
    // The first call opens the extension's tab-selection page. Give the user
    // enough time to open/login to JD and explicitly approve the correct tab.
    const timeout = Number.isFinite(timeoutMs)
      ? Math.max(1_000, Number(timeoutMs))
      : (this.firstToolCall ? 10 * 60_000 : 90_000);
    const result = await this.client.callTool({
      name: 'browser_run_code_unsafe',
      arguments: { code }
    }, undefined, { timeout, maxTotalTimeout: timeout });
    this.firstToolCall = false;
    return parsePlaywrightToolResult(result);
  }

  async close() {
    const client = this.client;
    const server = this.server;
    try {
      await client?.close();
    } finally {
      try {
        await server?.close();
      } finally {
        this.client = null;
        this.server = null;
        this.firstToolCall = true;
        if (this.previousBrowser === undefined) delete process.env.PLAYWRIGHT_MCP_BROWSER;
        else process.env.PLAYWRIGHT_MCP_BROWSER = this.previousBrowser;
        if (this.previousDnsOrder) dns.setDefaultResultOrder(this.previousDnsOrder);
      }
    }
  }
}
