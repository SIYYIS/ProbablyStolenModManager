import {expect, test} from '@playwright/test';

const endpoint = '/api/diagnostics/ai';
const saved = {revision: 'a'.repeat(32), base_url: 'https://proxy.example/v1', model: 'example-model', has_key: true, configured: true};
const excerpt = '第 7 行 · exception\nSystem.Exception: [redacted]\n  at ExampleMod.Init()';
const preview = {revision: 'b'.repeat(64), target: `${saved.base_url}/chat/completions`, model: saved.model,
  uses_key: true, text: excerpt, bytes: Buffer.byteLength(excerpt), included: 1, event_count: 3, tail_only: false};

test('previews exact text and destination before opt-in, and displays the result as plain text', async ({page}) => {
  const sent: unknown[] = [];
  await page.route(`**${endpoint}/**`, async route => {
    if (route.request().url().endsWith('/settings')) return route.fulfill({json: saved});
    if (route.request().url().endsWith('/preview')) return route.fulfill({json: preview});
    sent.push(route.request().postDataJSON());
    return route.fulfill({json: {text: '摘要：<img src=x onerror="alert(1)">\n下一步检查：核对兼容版本'}});
  });
  await page.setViewportSize({width: 400, height: 800});
  await page.goto('/diagnostics-e2e/harness.html');
  const ai = page.getByRole('region', {name: 'AI 日志诊断'});
  await ai.getByRole('button', {name: 'AI 连接设置'}).click();
  await expect(ai.getByText('明文（未加密）', {exact: false})).toBeVisible();
  await expect(ai.getByLabel('API Key')).toHaveAttribute('type', 'password');
  await expect(ai.getByLabel('API Key')).toHaveValue('');
  await ai.getByRole('button', {name: '关闭连接设置'}).click();
  const start = ai.getByRole('button', {name: 'AI 分析报错'});
  await start.click();
  const confirm = ai.getByRole('button', {name: '确认发送并分析'});
  await expect(confirm).toBeDisabled();
  await expect(ai.getByText(preview.target, {exact: true})).toBeVisible();
  expect(await ai.getByLabel('将发送的日志片段').textContent()).toBe(excerpt);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await ai.getByRole('button', {name: '取消'}).click();
  await expect(confirm).toHaveCount(0);
  expect(sent).toEqual([]);
  await start.click();
  await ai.getByRole('checkbox', {name: '我已核对接收地址和全部片段，同意本次发送'}).check();
  await confirm.click();
  await expect(ai.getByLabel('AI 分析文本')).toContainText('<img src=x onerror="alert(1)">');
  await expect(ai.locator('img')).toHaveCount(0);
  expect(sent).toEqual([{revision: preview.revision, confirmed: true}]);
});

test('a stale or failed request requires a new preview and consent', async ({page}) => {
  let previews = 0;
  let sent = 0;
  await page.route(`**${endpoint}/**`, async route => {
    if (route.request().url().endsWith('/settings')) return route.fulfill({json: saved});
    if (route.request().url().endsWith('/preview')) {
      previews++;
      return route.fulfill({json: preview});
    }
    sent++;
    return route.fulfill({status: 409, json: {detail: '日志已变化。'}});
  });
  await page.goto('/diagnostics-e2e/harness.html');
  const ai = page.getByRole('region', {name: 'AI 日志诊断'});
  const start = ai.getByRole('button', {name: 'AI 分析报错'});
  const check = ai.getByRole('checkbox', {name: '我已核对接收地址和全部片段，同意本次发送'});
  const confirm = ai.getByRole('button', {name: '确认发送并分析'});
  await start.click();
  await check.check();
  await confirm.click();
  await expect(ai.getByRole('alert')).toContainText('再试前请重新预览');
  await expect(confirm).toHaveCount(0);
  expect(sent).toBe(1);
  await start.click();
  await expect(confirm).toBeDisabled();
  await expect(check).not.toBeChecked();
  expect(previews).toBe(2);
});
