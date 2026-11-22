import {expect, test} from '@playwright/test';

test('opens Steam API settings, identifies disk downloads by Workshop ID, and refreshes existing results', async ({page}) => {
  const steamId = '180019999';
  const modsId = '180019998';
  const arrivingId = '180018888';
  let searches = 0;
  const items = [steamId, modsId, arrivingId].map((id, index) => ({id,
    title: ['线上工坊条目', '线上本地副本', '测试工坊扩展'][index], description: '', tags: [], subscriptions: 0, file_size: 0}));
  await page.route('**/api/workshop/search?**', route => {
    searches++;
    return route.fulfill({json: {source: 'steam_public', items, page: 1, total: items.length, has_next: false}});
  });
  await page.route('**/api/workshop/lookup?**', route => route.fulfill({json: {source: 'steam_details', items: [items[1]]}}));
  await page.route('**/api/refresh', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.workshop_downloads.push({workshop_id: arrivingId, id: 'arrived', name: '刚下载的模组',
      description: '', author: '', source: 'Steam 工坊目录'});
    await route.fulfill({response, json: data});
  });

  await page.setViewportSize({width: 390, height: 820});
  await page.goto('/#/workshop');
  const settingsLink = page.getByRole('button', {name: '去设置'});
  await expect(page.getByText('Steam Web API Key · 未配置')).toBeVisible();
  await settingsLink.click();
  const settings = page.getByRole('dialog', {name: '路径与设置'});
  await expect(settings.getByLabel('Steam Web API Key')).toBeFocused();
  await expect(settings.getByRole('button', {name: 'Steam 密钥申请页'})).toBeVisible();
  await settings.getByRole('button', {name: '关闭', exact: true}).click();
  await expect(settingsLink).toBeFocused();

  await page.getByRole('button', {name: '搜索工坊', exact: true}).click();
  const results = page.getByRole('region', {name: '工坊结果'});
  const details = page.getByRole('complementary', {name: '模组详情'});
  await expect(results.getByRole('button', {name: /线上工坊条目.*已下载/})).toBeVisible();
  await expect(results.getByRole('button', {name: /线上本地副本.*已下载/})).toBeVisible();
  const arriving = results.getByRole('button', {name: /测试工坊扩展/});
  await arriving.click();
  await expect(details.getByRole('button', {name: '下载到 Mods'})).toBeEnabled();
  await results.getByRole('button', {name: '刷新已下载'}).click();
  await expect(arriving).toContainText('已下载');
  await expect(details.getByRole('button', {name: '已下载', exact: true})).toBeDisabled();
  await expect(details).toContainText('Steam 工坊目录');
  expect(searches).toBe(1);

  await page.getByRole('button', {name: '已下载 (3)', exact: true}).click();
  const downloaded = page.getByRole('region', {name: '已下载模组'});
  await expect(downloaded.getByRole('button', {name: /刚下载的模组/})).toBeVisible();
  await expect(downloaded.getByRole('button', {name: /测试下载副本/})).toBeVisible();
  await expect(downloaded.getByRole('button', {name: /本机前置框架/})).toHaveCount(0);
  await page.getByRole('searchbox', {name: '筛选已下载模组'}).fill(modsId);
  await expect(downloaded.getByRole('listitem')).toHaveCount(1);
  expect(searches).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole('button', {name: '按 ID 查询'}).click();
  await page.getByRole('textbox', {name: '工坊链接或 ID'}).fill(modsId);
  await page.getByRole('button', {name: '查询 ID', exact: true}).click();
  await results.getByRole('button', {name: /线上本地副本.*已下载/}).click();
  await expect(details).toContainText('游戏 Mods 目录');
  await expect(details.getByRole('button', {name: '已下载', exact: true})).toBeDisabled();
});

test('pages through ranked online results and does not replace them with local mods when a later page fails', async ({page}) => {
  let failOnce = true;
  await page.route('**/api/workshop/search?**', route => {
    const params = new URL(route.request().url()).searchParams;
    const number = Number(params.get('page') ?? 1);
    expect(params.get('q')).toBe('fixture');
    if (number === 2) expect(params.get('source')).toBe('steam_public');
    if (number === 2 && failOnce) {
      failOnce = false;
      return route.fulfill({status: 502, json: {detail: '网络超时'}});
    }
    return route.fulfill({json: {source: 'steam_public', page: number, total: 31, has_next: number === 1,
      items: [{id: `18001000${number}`, title: `Online result ${number}`, description: '', tags: [], subscriptions: 0, file_size: 0}]}});
  });
  await page.setViewportSize({width: 430, height: 850});
  await page.goto('/#/workshop');
  await page.getByRole('searchbox', {name: '搜索关键词'}).fill('fixture');
  await page.getByRole('button', {name: '搜索工坊', exact: true}).click();
  const results = page.getByRole('region', {name: '工坊结果'});
  const next = results.getByRole('button', {name: '下一页'});
  const previous = results.getByRole('button', {name: '上一页'});
  await expect(results).toContainText('第 1 页 · 共 31 项');
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(results).toContainText('保留上一页结果');
  await expect(results).toContainText('Online result 1');
  await next.click();
  await expect(results).toContainText('第 2 页 · 共 31 项');
  await expect(results).toContainText('Online result 2');
  await expect(next).toBeDisabled();
  await previous.click();
  await expect(results).toContainText('Online result 1');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(430);
});

test('distinguishes offline results from Steam and can still look up an ID', async ({page}) => {
  let searches = 0;
  await page.route('**/api/workshop/search?**', route => {
    searches++;
    return route.fulfill({status: 502, json: {detail: 'Steam 社区搜索暂不可用'}});
  });
  await page.goto('/#/workshop');
  await page.getByRole('searchbox', {name: '搜索关键词'}).fill('alpha');
  await page.getByRole('button', {name: '搜索工坊', exact: true}).click();
  const local = page.getByRole('region', {name: '本机结果'});
  await expect(local).toContainText('仅显示本机已安装模组');
  await local.getByRole('button', {name: /Alpha Config/}).click();
  await expect(page.getByRole('button', {name: '下载到 Mods'})).toBeDisabled();

  await page.getByRole('button', {name: '本机', exact: true}).click();
  await page.getByRole('searchbox', {name: '搜索关键词'}).fill('unused');
  await page.getByRole('button', {name: '搜索本机'}).click();
  await expect(local.getByRole('button', {name: /Spare Module/})).toBeVisible();
  expect(searches).toBe(1);

  await page.getByRole('button', {name: '按 ID 查询'}).click();
  await page.getByRole('textbox', {name: '工坊链接或 ID'}).fill('180010001');
  await page.getByRole('button', {name: '查询 ID', exact: true}).click();
  await expect(page.getByRole('region', {name: '工坊结果'})).toContainText('Warehouse Assistant');
  await expect(page.getByRole('region', {name: '工坊结果'})).toContainText('Steam 条目');
});

test('shows covers with a default fallback and names each asset owner', async ({page}) => {
  const coverEndpoint = (id: string) => `/api/workshop/cover?id=${id}&v=${'a'.repeat(64)}`;
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg==', 'base64');
  const externalRequests: string[] = [];
  await page.route('https://images.steamusercontent.com/**', route => {
    externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.route('**/api/state', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.mods.find((m: {id: string}) => m.id === 'alpha').workshop_id = '180010001';
    data.mods.find((m: {id: string}) => m.id === 'alpha').local_cover = true;
    data.mods.find((m: {id: string}) => m.id === 'beta').workshop_id = '180010002';
    await route.fulfill({response, json: data});
  });
  await page.route('**/api/workshop/covers?**', route => route.fulfill({json: {covers: {
    '180010001': coverEndpoint('180010001'), '180010002': coverEndpoint('180010002'),
  }}}));
  let cacheAvailable = true;
  await page.route('**/api/workshop/cover?**', route => cacheAvailable && new URL(route.request().url()).searchParams.get('id') === '180010001'
    ? route.fulfill({contentType: 'image/png', body: png}) : route.fulfill({status: 404, json: {detail: 'Fixture cover unavailable'}}));
  await page.route('**/api/mods/cover?**', route => route.fulfill({contentType: 'image/png', body: png}));
  await page.route('**/api/order/diagnose', route => route.fulfill({json: {issues: [
    {severity: 'info', message: 'legacy', asset: {kind: '物品贴图', key: 'sample', comparison: 'same', owners: [
      {mod_id: 'alpha', name: 'Alpha Config', path: 'Textures/Items/sample.png'},
      {mod_id: 'beta', name: 'Beta Tools', path: 'Textures/Items/sample.png'},
    ]}},
  ]}}));
  await page.goto('/');
  const alpha = page.getByRole('button', {name: /Alpha Config/});
  const beta = page.getByRole('button', {name: /Beta Tools/});
  await expect(alpha.locator('.mod-cover img')).toHaveAttribute('src', /^blob:/);
  await expect.poll(async () => await alpha.locator('.mod-cover img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1);
  await expect(beta.locator('.mod-cover svg')).toBeVisible();
  cacheAvailable = false;
  const fallback = page.waitForResponse(response => new URL(response.url()).pathname === '/api/mods/cover');
  await page.reload();
  expect((await fallback).status()).toBe(200);
  await expect(alpha.locator('.mod-cover img')).toHaveAttribute('src', /^blob:/);
  expect(externalRequests).toEqual([]);
  await page.getByRole('button', {name: '诊断', exact: true}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('相同资源');
  await dialog.locator('.diagnosis-fold > summary').click();
  await expect(dialog).toContainText('Alpha Config');
  await expect(dialog).toContainText('Beta Tools');
  await expect(dialog).toContainText('Textures/Items/sample.png');
});
