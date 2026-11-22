import { test, expect, type Page } from '@playwright/test';

async function mount(page: Page) {
  await page.goto('/source-tools-harness.html');
}

const github = 'https://github.com/owner/repo';
const nexus = 'https://www.nexusmods.com/game/mods/5';

test('collection confirms only selected missing rows and stops on an error', async ({page}) => {
  const requests: {id: string; confirm: boolean; confirm_login: boolean}[] = [];
  await page.route('**/api/sources', route => route.fulfill({json: {links: []}}));
  await page.route('**/api/workshop/collections/preview', route => route.fulfill({json: {
    id: '111', title: '模拟合集', preview_token: 'test-token', require_login: true,
    members: [{id: '1', title: '甲', state: 'missing'}, {id: '2', title: '乙', state: 'missing'},
      {id: '3', title: '巧', state: 'missing'}, {id: '4', title: '丙', state: 'installed'},
      {id: '5', title: '丁', state: 'wrong_game'}, {id: '6', title: '戊', state: 'missing'}],
  }}));
  await page.route('**/api/workshop/collections/download', route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    return route.fulfill(body.id === '2' ? {status: 409, json: {detail: '模拟下载失败'}} :
      {json: {status: 'installed', message: '已安装完成'}});
  });
  await mount(page);
  await page.getByLabel('合集链接或数字 ID').fill('111');
  await page.getByRole('button', {name: '解析合集'}).click();
  await expect(page.getByText('已安装 · 不覆盖')).toBeVisible();
  await expect(page.getByRole('checkbox', {name: /丁/})).toHaveCount(0);
  await page.getByRole('checkbox', {name: /巧/}).uncheck();
  await page.getByRole('button', {name: '审阅下载清单'}).click();
  const confirm = page.getByRole('button', {name: '确认并开始下载'});
  await expect(confirm).toBeDisabled();
  expect(requests).toHaveLength(0);
  await page.getByRole('checkbox', {name: /登录 Steam/}).check();
  await confirm.click();
  await expect(page.getByRole('alert')).toContainText('模拟下载失败');
  await expect(page.getByText('1 项已安装，0 项已跳过，其余未完成。')).toBeVisible();
  expect(requests.map(({id, confirm, confirm_login}) => [id, confirm, confirm_login])).toEqual([['1', true, true], ['2', true, true]]);
  await expect(page.locator('body')).toHaveAttribute('data-refreshed', 'true');
});

test('maintains source cards and only offers release checks for GitHub', async ({page}) => {
  let links: {name: string; url: string; platform: string; origin: string}[] = [];
  await page.route('**/api/sources', route => {
    if (route.request().method() === 'PUT') {
      const {expected, links: next} = route.request().postDataJSON();
      expect(expected).toEqual(links.map(({name, url}) => ({name, url})));
      links = next.map((row: {name: string; url: string}) => ({...row, platform: row.url === github ? 'github' : 'nexus', origin: 'user'}));
    }
    return route.fulfill({json: {links}});
  });
  await page.route('**/api/sources/github/latest?*', route => {
    expect(new URL(route.request().url()).searchParams.get('url')).toBe(github);
    return route.fulfill({json: {release: {tag: 'v2', published_at: '2026-02-01T12:00:00Z'}}});
  });
  await mount(page);
  const name = page.getByLabel('名称（可留空）');
  const url = page.getByLabel('GitHub 或 Nexus Mods 页面地址');
  await name.fill('GitHub 模组'); await url.fill(github);
  await page.getByRole('button', {name: '添加来源'}).click();
  await expect(page.getByText('GitHub · 用户维护')).toBeVisible();
  await name.fill('Nexus 模组'); await url.fill(nexus);
  await page.getByRole('button', {name: '添加来源'}).click();
  await expect(page.getByText('Nexus Mods · 用户维护')).toBeVisible();
  await expect(page.getByRole('button', {name: '检查正式发布'})).toHaveCount(1);
  await page.getByRole('button', {name: '检查正式发布'}).click();
  await expect(page.getByText('正式发布：v2 · 2026-02-01 (UTC)')).toBeVisible();
  const card = page.getByRole('article').filter({hasText: 'Nexus 模组'});
  await card.getByRole('button', {name: '编辑'}).click();
  await name.fill('Nexus 新名称');
  await page.getByRole('button', {name: '保存修改'}).click();
  await expect(page.getByText('Nexus 新名称')).toBeVisible();
  await page.getByRole('article').filter({hasText: 'Nexus 新名称'}).getByRole('button', {name: '移除来源'}).click();
  await expect(page.getByText('Nexus 新名称')).toHaveCount(0);
});
