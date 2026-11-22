import {expect, test} from '@playwright/test';

test('checks workshop versions manually and confirms each reversible change', async ({page}) => {
  const item = {id: '1234567890', name: 'Version Fixture', mod_id: 'Fixture.Version', version: '2.1',
    installed: true, installed_time: 1700000000, steam_manifest: '1234', lock_mod_id: 'Fixture.Version',
    local_conflict: false};
  let state = 'none';
  let checked = 0;
  const writes: string[] = [];
  await page.route('**/api/workshop/versions**', async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      const data = request.postDataJSON();
      expect(data).toEqual({id: item.id, mod_id: item.mod_id, action: state === 'none' ? 'lock' :
        state === 'locked' ? 'unlock' : 'restore', confirm: true});
      writes.push(data.action);
      state = data.action === 'unlock' ? 'quarantined' : 'locked';
      return route.fulfill({json: {message: '版本切换完成。', path: 'C:/Fixture/Version'}});
    }
    const online = new URL(request.url()).searchParams.get('online') === 'true';
    if (online) checked += 1;
    return route.fulfill({json: {online, game_running: false, notes: [], rows: [{...item,
      public_time: online ? 1700000050 : null, status: online ? 'update' : 'unknown', lock_state: state,
      locked_time: state === 'none' ? null : 1700000000, locked_version: state === 'none' ? null : '2.1',
      local_path: state === 'none' ? null : 'C:/Fixture/Version',
      action: state === 'none' ? 'lock' : state === 'locked' ? 'unlock' : 'restore',
    }]}});
  });

  await page.goto('/');
  await page.getByRole('link', {name: '高级工具'}).click();
  await page.getByRole('button', {name: '工坊版本', exact: true}).click();
  const versions = page.getByRole('region', {name: 'Steam 工坊版本'});
  await expect(versions.getByText('未知', {exact: true})).toBeVisible();
  expect(checked).toBe(0);
  await versions.getByRole('button', {name: '检查更新'}).click();
  await expect(versions.getByText('有更新', {exact: true})).toBeVisible();

  await versions.getByRole('button', {name: '锁定本地版本：Version Fixture'}).click();
  await page.getByRole('dialog').getByRole('button', {name: '返回'}).click();
  expect(writes).toHaveLength(0);

  for (const label of ['锁定本地版本', '解锁并隔离', '恢复锁定版本']) {
    await versions.getByRole('button', {name: `${label}：Version Fixture`}).click();
    await page.getByRole('dialog').getByRole('button', {name: '确认切换'}).click();
  }
  await expect(versions.getByRole('button', {name: '解锁并隔离：Version Fixture'})).toBeEnabled();
  expect(writes).toEqual(['lock', 'unlock', 'restore']);
  expect(checked).toBe(1);
  await expect(versions.getByText('有更新', {exact: true})).toBeVisible();
});

test('shows the Steam date in both local mod lists and preserves draft order', async ({page}) => {
  let checked = 0;
  await page.route('**/api/workshop/versions?online=true', async route => {
    checked += 1;
    if (checked === 1) return route.continue();
    return route.fulfill({status: 503, json: {detail: '测试：工坊暂时无法连接。'}});
  });

  await page.goto('/');
  const active = page.getByRole('region', {name: '加载顺序'});
  const inactive = page.getByRole('region', {name: '未启用模组'});
  const detail = page.getByRole('complementary').filter({has: page.getByRole('heading', {name: '模组详情'})});
  const inList = (section: typeof active) => section.getByRole('button')
    .filter({has: page.getByText('测试工坊扩展', {exact: true})});
  const dateTime = new Date(1700000050 * 1000).toISOString();
  const localTab = page.getByRole('link', {name: '本地模组'});
  const toolsTab = page.getByRole('link', {name: '高级工具'});
  const versions = page.getByRole('region', {name: 'Steam 工坊版本'});

  await inList(inactive).click();
  await expect(detail.getByText('未获取', {exact: true})).toBeVisible();
  await expect(inList(inactive).locator('time')).toHaveCount(0);
  expect(checked).toBe(0);
  await inactive.getByRole('checkbox',{name:'勾选 Spare Module'}).check();
  await page.getByRole('button',{name:'启用 1 项',exact:true}).click();
  await expect(active.getByText('未保存', {exact: true})).toBeVisible();

  await toolsTab.click();
  await page.getByRole('button', {name: '工坊版本', exact: true}).click();
  await versions.getByRole('button', {name: '检查更新'}).click();
  await expect(versions.locator(`time[datetime="${dateTime}"]`)).toBeVisible();
  await localTab.click();
  await expect(active.getByText('未保存', {exact: true})).toBeVisible();
  await expect(inList(inactive).locator('time')).toHaveAttribute('datetime', dateTime);
  await expect(detail.locator('dl time')).toHaveAttribute('datetime', dateTime);
  await expect(inList(inactive).locator('time')).toHaveAttribute('datetime', dateTime);

  await toolsTab.click();
  await versions.getByRole('button', {name: '检查更新'}).click();
  await expect(versions.getByRole('alert')).toContainText('工坊暂时无法连接');
  await localTab.click();
  await expect(inList(inactive).locator('time')).toHaveAttribute('datetime', dateTime);
  await page.reload();
  await expect(inList(inactive).locator('time')).toHaveAttribute('datetime', dateTime);
  await inList(inactive).getByText('测试工坊扩展', {exact: true}).click();
  await expect(detail.locator('dl time')).toHaveAttribute('datetime', dateTime);
  await inactive.getByRole('button', {name: /^本机前置框架/}).click();
  await expect(detail.getByText('工坊最后更新')).toHaveCount(0);
  expect(checked).toBe(2);
});
