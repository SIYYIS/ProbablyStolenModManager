import {test, expect} from '@playwright/test';
import {join} from 'node:path';

test('saves an edited order and maintains it after a reload', async ({page}) => {
  await page.goto('/');
  const order = page.getByRole('region', {name: '加载顺序'});
  await expect(order.getByRole('listitem').first()).toContainText('Alpha Config');
  const handler = order.getByRole('button', {name: '调整 Alpha Config 的顺序'});
  await handler.focus();
  await page.keyboard.press('ArrowDown');
  await expect(order.getByRole('listitem').first()).toContainText('Beta Tools');
  await expect(order).toContainText('未保存');
  const persisted = page.waitForResponse(response => response.url().endsWith('/api/order') && response.request().method() === 'POST');
  await page.getByRole('button', {name: '保存顺序'}).click();
  const preview = page.getByRole('dialog', {name: '应用前预览'});
  await expect(preview).toContainText('相对顺序变化 2');
  await preview.getByRole('button', {name: '确认保存顺序'}).click();
  expect((await persisted).ok()).toBeTruthy();
  await expect(page.getByRole('status').filter({hasText: '已更新加载顺序'})).toBeVisible();
  await page.reload();
  await expect(order.getByRole('listitem').first()).toContainText('Beta Tools');
  if (process.env.PSMM_SCREENSHOTS) {
    await order.getByRole('button', {name: /01 Beta Tools/}).click();
    await page.screenshot({path: join(process.env.PSMM_SCREENSHOTS, 'webui-local.png')});
  }
  await page.getByRole('searchbox', {name: '搜索模组'}).fill('unused');
  await expect(order).toContainText('没有匹配的模组');
  await expect(page.getByRole('region', {name: '未启用模组'})).toContainText('Spare Module');
});

test('moves a row by dragging its handle without writing the draft', async ({page}) => {
  await page.goto('/');
  const order = page.getByRole('region', {name: '加载顺序'});
  const handles = order.getByRole('button', {name: /^调整 /});
  await expect(handles).toHaveCount(2);
  const prior = await handles.first().getAttribute('aria-label');
  const from = (await handles.last().boundingBox())!;
  const to = (await handles.first().boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {steps: 12});
  await page.mouse.up();
  await expect(handles.last()).toHaveAttribute('aria-label', prior!);
  await expect(order).toContainText('未保存');
});

test('toggles undocumented parameters persistently without changing the current draft or JSON view', async ({page}) => {
  await page.goto('/#/config');
  await page.getByRole('button', {name: /Alpha Config/}).click();
  const field = page.getByLabel('multiplier', {exact: true});
  const saveConfig = page.getByRole('button', {name: '保存配置', exact: true});
  const headers = {'X-PSMM-Request': '1'};
  const initial = await (await page.request.get('/api/settings', {headers})).json();
  const {has_steam_api_key: _masked, ...settings} = initial;
  async function setVisibility(previous: boolean, next: boolean) {
    await page.getByRole('button', {name: '路径与设置'}).click();
    const dialog = page.getByRole('dialog');
    const control = dialog.getByRole('checkbox', {name: '显示无注释参数'});
    if (previous) await expect(control).toBeChecked();
    else await expect(control).not.toBeChecked();
    await control.setChecked(next);
    await dialog.getByRole('button', {name: '保存设置', exact: true}).click();
    await expect(dialog).toBeHidden();
  }
  try {
    await expect(page.getByText('此配置没有带注释的参数')).toBeVisible();
    await expect(field).toHaveCount(0);
    await setVisibility(false, true);
    await expect(field).toHaveAccessibleDescription('未找到参数说明');
    await page.reload();
    await page.getByRole('button', {name: /Alpha Config/}).click();
    await expect(field).toHaveValue('2');
    await field.fill('5');
    await setVisibility(true, false);
    await expect(field).toHaveValue('5');
    await expect(page.getByRole('switch', {name: 'enabled'})).toHaveCount(0);
    await saveConfig.click();
    await expect(page.getByRole('status').filter({hasText: '已保存配置'})).toBeVisible();
    await expect(field).toHaveCount(0);
    await setVisibility(false, true);
    await expect(field).toHaveValue('5');
    await setVisibility(true, false);
    await page.getByRole('combobox', {name: '配置文件'}).selectOption({label: '掉落表: fixture.json'});
    await expect(page.getByLabel('原始 JSON 编辑器')).toContainText('fixtureLoot');
    if (process.env.PSMM_SCREENSHOTS) await page.screenshot({path: join(process.env.PSMM_SCREENSHOTS, 'webui-config.png')});
  } finally {
    await page.request.put('/api/settings', {headers, data: settings});
  }
});

test('opens the selected mod file and saves a documented parameter without affecting other files', async ({page}) => {
  await page.goto('/');
  await page.getByRole('link', {name: '模组修改'}).click();
  await expect(page.getByRole('heading', {name: '模组修改'})).toBeVisible();
  const files = page.getByRole('combobox', {name: '配置文件'});
  const alpha = page.getByRole('button', {name: /Alpha Config/});
  const dependent = page.getByRole('button', {name: /测试工坊扩展/});
  const headers = {'X-PSMM-Request': '1'};
  const read = async (mod: string, id: string) => {
    const query = new URLSearchParams({mod_id: mod, id});
    const path = `/api/config/file?${query}`;
    let data: unknown;
    await expect.poll(async () => {
      const response = await page.request.get(path, {headers});
      if (response.ok()) data = await response.json();
      return response.status();
    }).toBe(200);
    return data as {revision: string; entries: {key: string; value: unknown}[]};
  };

  await alpha.click();
  await expect(files.locator('option:checked')).toContainText('alpha.json');
  const alphaFile = await files.inputValue();
  const original = await read('alpha', alphaFile);
  await dependent.click();
  await expect(files.locator('option:checked')).toContainText('fixture.dependent.json');
  const budget = page.getByRole('textbox', {name: '测试预算'});
  await expect(budget).toHaveValue('50');
  await expect(budget).toHaveAccessibleDescription('模拟预算上限。 · 配置文件');
  await expect(page.getByRole('switch', {name: 'opaque'})).toHaveCount(0);
  await page.getByText('模组说明', {exact: true}).click();
  await expect(page.getByText('测试作者指南：仅用于临时测试。')).toBeVisible();

  const dependentFile = await files.inputValue();
  try {
    await budget.fill('83');
    await page.getByRole('button', {name: '保存配置', exact: true}).click();
    await expect(page.getByRole('status').filter({hasText: '已保存配置'})).toBeVisible();
    const updated = await read('fixture.dependent', dependentFile);
    expect(updated.entries).toEqual(expect.arrayContaining([
      expect.objectContaining({key: 'budget', value: 83, description: '模拟预算上限。'}),
      expect.objectContaining({key: 'opaque', value: true}),
    ]));
    await alpha.click();
    await expect(files.locator('option:checked')).toContainText('alpha.json');
    expect(await read('alpha', alphaFile)).toEqual(original);
  } finally {
    const {revision, entries} = await read('fixture.dependent', dependentFile);
    if (entries.find((entry: {key: string}) => entry.key === 'budget')?.value !== 50) {
      const restored = await page.request.put('/api/config/file', {headers, data: {
        mod_id: 'fixture.dependent', id: dependentFile, revision, values: {budget: 50},
      }});
      expect(restored.ok()).toBeTruthy();
    }
  }
});

test('retries busy configuration reads after opening a mod', async ({page}) => {
  const reads = new Map<string, number>();
  await page.route(/\/api\/config\/files?\?mod_id=fixture\.dependent(?:&|$)/, async route => {
    const url = new URL(route.request().url());
    const count = (reads.get(url.pathname) ?? 0) + 1;
    reads.set(url.pathname, count);
    if (count === 1) await route.fulfill({status: 409, json: {detail: '管理器正在处理另一项本地操作，请稍后重试。'}});
    else await route.continue();
  });
  await page.goto('/#/config');
  await page.getByRole('button', {name: /测试工坊扩展/}).click();
  await expect(page.getByRole('textbox', {name: '测试预算'})).toHaveValue('50');
  expect(reads.get('/api/config/files')).toBeGreaterThanOrEqual(2);
  expect(reads.get('/api/config/file')).toBeGreaterThanOrEqual(2);
});

test('selects a Steam result and fits a narrow browser view', async ({page}) => {
  await page.setViewportSize({width: 430, height: 850});
  await page.goto('/#/workshop');
  await page.getByRole('button', {name: '按 ID 查询'}).click();
  await page.getByRole('textbox', {name: '工坊链接或 ID'}).fill('180010001');
  await page.getByRole('form', {name: '搜索创意工坊'}).locator('button[type=submit]').click();
  await page.getByRole('button', {name: /Warehouse Assistant/}).click();
  await expect(page.getByRole('heading', {name: 'Warehouse Assistant'})).toBeVisible();
  await expect(page.getByRole('link', {name: '浏览器查看'})).toHaveAttribute('href', /steamcommunity\.com.*180010001/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(430);
  if (process.env.PSMM_SCREENSHOTS) {
    await page.screenshot({path: join(process.env.PSMM_SCREENSHOTS, 'webui-workshop-narrow.png'), fullPage: true});
    await page.setViewportSize({width: 1360, height: 850});
    await page.screenshot({path: join(process.env.PSMM_SCREENSHOTS, 'webui-workshop.png')});
  }
});
