import {expect, test} from '@playwright/test';

test('reviews game versions and the permissions of checked mods without approving them', async ({page}) => {
  const mutations: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/') && request.method() !== 'GET') mutations.push(request.url());
  });
  await page.goto('/');
  await expect(page.getByText('v0.46D', {exact: true})).toHaveAttribute('title', '来自本机游戏日志');
  const library = page.getByRole('region', {name: '未启用模组'});
  await library.getByRole('button', {name: /^测试工坊扩展/}).click();
  const detail = page.getByRole('complementary').filter({has: page.getByRole('heading', {name: '模组详情'})});
  await expect(detail.locator('.mod-version-check.ok')).toHaveText('0.46D匹配');
  await expect(detail.locator('.mod-version-check.warning')).toHaveText('1.0.0不匹配');
  const permissions = detail.getByRole('region', {name: '模组权限'});
  await expect(permissions.getByText('files', {exact: true})).toBeVisible();
  await expect(permissions.getByText('steam', {exact: true})).toBeVisible();
  await expect(permissions.getByText(/游戏日志记录：匹配当时的批准记录/)).toBeVisible();
  await library.getByRole('checkbox', {name: '勾选 测试工坊扩展'}).check();
  await library.getByRole('checkbox', {name: '勾选 本机前置框架'}).check();
  await page.getByRole('button', {name: '批量权限审阅'}).click();
  const dialog = page.getByRole('dialog', {name: /批量权限审阅/});
  await expect(dialog.getByRole('heading', {name: '测试工坊扩展'})).toBeVisible();
  await expect(dialog.getByRole('heading', {name: '本机前置框架'})).toBeVisible();
  await expect(dialog.getByText('读取或写入此电脑上任意位置的文件', {exact: false})).toBeVisible();
  await expect(dialog.getByText('未声明额外权限', {exact: true})).toBeVisible();
  await page.setViewportSize({width: 420, height: 780});
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth)).toBeLessThanOrEqual(420);
  await dialog.getByRole('button', {name: '完成审阅'}).click();
  await expect(dialog).toHaveCount(0);
  expect(mutations).toEqual([]);
});
