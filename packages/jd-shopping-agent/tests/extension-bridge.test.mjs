import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePlaywrightToolResult, PlaywrightExtensionBridge } from '../src/playwright-extension-bridge.mjs';
import { JdBrowser } from '../src/jd-browser.mjs';

test('parses JSON results while ignoring Playwright page metadata', () => {
  const result = parsePlaywrightToolResult({
    content: [{ type: 'text', text: '### Result\n{"ok":true,"answer":42}\n### Page\n- URL: https://www.jd.com/' }]
  });
  assert.deepEqual(result, { ok: true, answer: 42 });
});

test('turns extension connection failures into a beginner-friendly message', () => {
  assert.throws(() => parsePlaywrightToolResult({
    isError: true,
    content: [{ type: 'text', text: '### Error\nExtension connection timeout' }]
  }), /在 Playwright 授权页选择已经登录京东的标签页/);
});

test('closes both sides of the extension relay', async () => {
  const closed = [];
  const bridge = new PlaywrightExtensionBridge();
  bridge.client = { close: async () => closed.push('client') };
  bridge.server = { close: async () => closed.push('server') };
  await bridge.close();
  assert.deepEqual(closed, ['client', 'server']);
  assert.equal(bridge.client, null);
  assert.equal(bridge.server, null);
});

test('allows short per-operation timeouts for slow shopping pages', async () => {
  const bridge = new PlaywrightExtensionBridge();
  let receivedOptions;
  bridge.client = {
    callTool: async (_request, _schema, options) => {
      receivedOptions = options;
      return { content: [{ type: 'text', text: '### Result\n{"ok":true}' }] };
    }
  };
  await bridge.runPageCode('async () => ({ ok: true })', { timeoutMs: 6_000 });
  assert.equal(receivedOptions.timeout, 6_000);
  assert.equal(receivedOptions.maxTotalTimeout, 6_000);
});

test('generated page code preserves SKU regular expressions', async () => {
  const calls = [];
  const bridge = {
    runPageCode: async (code) => {
      calls.push(code);
      assert.doesNotThrow(() => new Function(`return (${code});`));
      return { ok: true, url: 'https://item.jd.com/100123.html' };
    },
    close: async () => {}
  };
  const browser = new JdBrowser({ paths: { screenshots: '.' } }, { bridge });
  await browser.addToCart({ url: 'https://item.jd.com/100123.html', sku: '100123' });
  assert.match(calls[0], /match\(\/\\\/\(\\d\{5,\}\)\\\.html\/\)/);
});
