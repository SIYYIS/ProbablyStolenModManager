import {expect, test} from '@playwright/test';

test.beforeEach(async ({page}) => {
  await page.goto('/diagnostics-e2e/harness.html');
  await expect(page.getByText('游戏审计 · 明确模组 ID')).toBeVisible();
});

test('uses exact ID only where logged, and separates observed loads from current order', async ({page}) => {
  const errors = page.getByRole('region', {name: '报错日志分析'});
  await expect(errors.getByText('fixture.window', {exact: true})).toBeVisible();
  const patch = errors.locator('details').filter({has: page.locator('summary').filter({hasText: 'Failed to patch Store.Open'})});
  await patch.locator('summary').click();
  await expect(patch.getByText('堆栈和 Harmony 报错不能单独确定责任模组')).toBeVisible();
  await errors.getByRole('button', {name: '日志中的加载记录'}).click();
  await expect(errors.getByText('fixture.widget', {exact: true})).toBeVisible();
  await expect(errors.getByText('fixture.current-only')).toHaveCount(0);
  await errors.getByRole('button', {name: '当前启用列表'}).click();
  await expect(errors.getByText('fixture.current-only')).toBeVisible();
  await expect(errors.getByText('fixture.widget')).toHaveCount(0);
});

test('requires an opt-in preview before downloading fixture configs', async ({page}) => {
  const support = page.getByRole('region', {name: '排错包', exact: true});
  const download = support.getByRole('button', {name: '下载 ZIP'});
  const preview = support.getByRole('button', {name: '预览排错包'});
  await expect(download).toBeDisabled();
  await preview.click();
  await expect(support.getByText('Player.log', {exact: true})).toBeVisible();
  await expect(support.getByText('ModConfigs/fixture.json')).toHaveCount(0);
  await support.getByLabel('附加 ModConfigs 配置').check();
  await expect(download).toBeDisabled();
  await preview.click();
  await support.getByText('ModConfigs/fixture.json').click();
  await expect(support.getByText('"extraCustomers": 4', {exact: false})).toBeVisible();
  await expect(support.getByText('unique-config-secret')).toHaveCount(0);
  const pending = page.waitForEvent('download');
  await download.click();
  expect((await pending).suggestedFilename()).toBe('psmm-support.zip');
});

test('toggles the fixture marker and remains usable at a narrow width', async ({page}) => {
  test.skip(process.platform !== 'win32', 'The verified launcher build is Windows-only');
  await page.setViewportSize({width: 400, height: 800});
  await expect(page.locator('.diag-layout').getByRole('switch')).toHaveCount(0);
  const toggle = page.locator('.fixture-settings').getByRole('switch', {name: '开发者模式标记'});
  await expect(toggle).toBeEnabled();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await expect(page.getByText('已创建空的 dev.txt', {exact: false})).toBeVisible();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByText('已移除空的 dev.txt', {exact: false})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
});
