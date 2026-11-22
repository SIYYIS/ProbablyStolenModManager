import {test, expect} from '@playwright/test';

test('replaces the startup fallback when React mounts', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {name: '本地模组'})).toBeVisible();
  await expect(page.getByRole('region', {name: '加载顺序'})).toContainText('Alpha Config');
  await expect(page.locator('#startup-fallback')).toHaveCount(0);
});

test('keeps a usable startup fallback if the entry bundle is blocked', async ({page}) => {
  const entry = '**/assets/index-*.js';
  await page.route(entry, route => route.abort('blockedbyclient'));
  const failedEntry = page.waitForEvent('requestfailed', {predicate: request =>
    /\/assets\/index-[^/]+\.js$/.test(request.url()), timeout: 5000});
  await Promise.all([failedEntry, page.goto('/')]);

  const fallback = page.getByRole('main', {name: '正在打开模组工作台'});
  await expect(fallback).toBeVisible();
  await expect(fallback.getByText('如果长时间没有进入工作台，请关闭程序后重新打开。')).toBeVisible();
  await expect(fallback).toHaveCSS('display', 'flex');
  await expect(page.getByRole('heading', {name: '本地模组'})).toHaveCount(0);
});
