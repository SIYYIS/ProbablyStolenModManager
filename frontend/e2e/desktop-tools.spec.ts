import {expect, test} from '@playwright/test';

test('opens management dialogs while their initial requests are still pending', async ({page}) => {
  let release!: () => void;
  const hold = new Promise<void>(resolve => {release = resolve;});
  await page.route(/\/api\/(state|settings|backups)$/, async route => {await hold; await route.continue();});
  try {
    await page.goto('/');
    for (const label of ['路径与设置', '管理预设', '分享码', '备份恢复']) {
      await page.getByRole('button', {name:label, exact:true}).click({timeout:1600});
      await expect(page.getByRole('dialog')).toBeVisible({timeout:1000});
      await page.getByRole('button', {name:'关闭', exact:true}).click();
    }
  } finally {release();}
});

test('previews detected paths and rechecks an unsaved custom path', async ({page}) => {
  await page.goto('/');
  const settings = await page.request.get('/api/settings', {headers:{'X-PSMM-Request':'1'}});
  const game = (await settings.json()).game_dir;
  await page.route('**/api/environment/pick', route => route.fulfill({json:{path:game}}));
  await page.route('**/api/environment/preview', async route => {
    const draft = route.request().postDataJSON();
    if (!draft.game_dir) {
      const response = await route.fetch({postData:JSON.stringify({...draft,game_dir:game})});
      const data = await response.json(); data.paths.game_dir.mode = 'auto';
      await route.fulfill({response,json:data});
    } else await route.continue();
  });
  await page.setViewportSize({width:390,height:730});
  await page.getByRole('button', {name:'路径与设置'}).click();
  const dialog = page.getByRole('dialog', {name:'路径与设置'});
  const field = dialog.getByRole('textbox', {name:'游戏目录', exact:true});
  await expect(field).toHaveValue(game);
  await expect(dialog.getByText('Probably Stolen.exe',{exact:true})).toBeVisible();
  const pathRow = field.locator('../..');
  await field.fill('relative-test-directory');
  await expect(pathRow).toContainText('未检查');
  await expect(pathRow).toContainText('需要有效的本地绝对路径');
  await dialog.getByRole('button',{name:'选择游戏目录',exact:true}).click();
  await expect(field).toHaveValue(game);
  await dialog.getByRole('button',{name:'清空游戏目录',exact:true}).click();
  await expect(field).toHaveValue('');
  await dialog.getByRole('button',{name:'自动识别游戏目录',exact:true}).click();
  await expect(field).toHaveValue(game);
  await expect(pathRow.getByText('自动',{exact:true})).toBeVisible();
  expect(await dialog.evaluate(el => el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(1);
  await page.getByRole('button', {name: '关闭', exact: true}).click();
  await page.getByRole('button', {name: '路径与设置'}).click();
  await expect(field).toHaveValue(game);
  await expect(pathRow.getByText('手动',{exact:true})).toBeVisible();
});

test('separates optional Steam downloader login from search and removes translation settings', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button', {name: '路径与设置'}).click();
  const dialog = page.getByRole('dialog', {name: '路径与设置'});
  await expect(dialog.getByText('公开搜索、按 ID 查询及本机识别：无需密钥。', {exact: false})).toBeVisible();
  const account = dialog.getByRole('textbox', {name: '下载器 Steam 账号'});
  await expect(account).not.toBeVisible();
  await dialog.getByText('外部下载器（可选）', {exact: true}).click();
  await expect(account).toBeVisible();
  await expect(account).toHaveAccessibleDescription(/仅当通过 SteamCMD 或 DepotDownloader.*与搜索无关/);
  await expect(dialog.getByText('翻译服务设置')).toHaveCount(0);
  await page.getByRole('button', {name: '关闭', exact: true}).click();
  await page.getByRole('link', {name: '高级工具'}).click();
  await expect(page.getByRole('button', {name: '机翻汉化', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'AI 分析报错', exact: true})).toBeVisible();
});

test('edits and restores the disposable game slot', async ({page}) => {
  await page.goto('/#/saves');
  await expect(page.getByText('第 106 天')).toBeVisible();
  const cash = page.getByLabel('玩家现金');
  await expect(cash).toHaveValue('22');
  await cash.fill('1234');
  await page.getByRole('button', {name: '审阅并保存'}).click();
  await page.getByRole('dialog', {name: '保存前审阅'}).getByRole('button', {name: '备份并保存'}).click();
  await expect(page.getByText('档位已保存。', {exact: false})).toBeVisible();
  await expect(cash).toHaveValue('1234');
  await page.getByLabel('档位备份').selectOption({index: 1});
  page.once('dialog', dialog => void dialog.accept());
  await page.getByRole('button', {name: '恢复备份', exact: true}).click();
  await expect(page.getByText('档位已恢复', {exact: false})).toBeVisible();
  await expect(cash).toHaveValue('22');
});

test('filters documented slot fields and saves only valid edits', async ({page}) => {
  await page.goto('/#/saves');
  await expect(page.getByLabel('王尔德的恩惠')).toHaveValue('365');
  await expect(page.getByLabel('王尔德的恩惠')).toHaveAccessibleDescription(/先扣恩惠/);
  const search = page.getByRole('searchbox', {name: '搜索存档字段'});
  const save = page.getByRole('button', {name: '审阅并保存'});
  await search.fill('运气');
  const luck = page.getByLabel('基础运气');
  await expect(luck).toHaveValue('-5');
  await expect(page.getByLabel('玩家现金')).toHaveCount(0);
  await luck.fill('-101');
  await expect(luck).toHaveAttribute('aria-invalid', 'true');
  await expect(save).toBeDisabled();
  await luck.fill('-12');
  await search.fill('教程');
  const tutorial = page.getByRole('checkbox', {name: '教程提示'});
  await tutorial.check();
  const sent = page.waitForRequest(req => req.method() === 'PUT' && req.url().endsWith('/api/save-slots/0'));
  await save.click();
  await page.getByRole('dialog', {name: '保存前审阅'}).getByRole('button', {name: '备份并保存'}).click();
  expect((await sent).postDataJSON().changes).toEqual({luck: -12, tutorialEnabled: true});
  await expect(page.getByText('档位已保存。', {exact: false})).toBeVisible();
  await expect(tutorial).toBeChecked();
  await search.fill('运气');
  await expect(luck).toHaveValue('-12');
  await page.getByLabel('档位备份').selectOption({index: 1});
  page.once('dialog', dialog => void dialog.accept());
  await page.getByRole('button', {name: '恢复备份', exact: true}).click();
  await expect(luck).toHaveValue('-5');
});
