import {execFileSync} from 'node:child_process';
import {expect, test} from '@playwright/test';

function fixtureArchive() {
  const command = [
    'import base64,io,zipfile',
    's=io.BytesIO()',
    'z=zipfile.ZipFile(s,"w")',
    'z.writestr("Release/Unpacked/manifest.xml","<Manifest><ID>fixture.archive.drop</ID><Name>Archive Drop Test</Name></Manifest>")',
    'z.close()',
    'print(base64.b64encode(s.getvalue()).decode())',
  ].join(';');
  return execFileSync('py', ['-3.10', '-B', '-c', command], {encoding: 'utf8'}).trim();
}

async function openTools(page: import('@playwright/test').Page) {
  await page.goto('/#/tools');
  await page.getByRole('button', {name: '安装与迁移', exact: true}).click();
  return page.getByRole('region', {name: '压缩包安装与整合包'});
}

test('dropping a nested ZIP previews, allows cancel, and installs only on confirmation', async ({page}) => {
  const panel = await openTools(page);
  const encoded = fixtureArchive();
  const drop = () => panel.locator('.archive-drop').evaluate((dropzone, data) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([Uint8Array.from(atob(data), c => c.charCodeAt(0))], 'nested.zip', {type: 'application/zip'}));
    dropzone.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
  }, encoded);
  await drop();
  await expect(panel.getByRole('heading', {name: /安装预览/})).toBeVisible();
  await expect(panel.getByText(/Archive Drop Test/)).toBeVisible();
  await panel.getByRole('button', {name: '取消', exact: true}).click();
  await expect(panel.getByRole('status')).toContainText('未改动');
  await drop();
  const confirm = panel.getByRole('button', {name: '确认安装'});
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(panel.getByRole('status')).toContainText('已安装 1 个本地模组');
  await expect(panel.getByRole('button', {name: '选择压缩包'})).toBeEnabled();
  await drop();
  await expect(panel.getByText(/本地已存在同名目录或模组 ID/)).toBeVisible();
  await expect(confirm).toBeDisabled();
  await panel.getByRole('button', {name: '取消', exact: true}).click();
});

test('exports the selected preset and previews reimport without replacing it', async ({page}) => {
  const panel = await openTools(page);
  await page.getByRole('combobox', {name: '当前预设'}).selectOption('Fixture profile');
  await panel.getByRole('checkbox', {name: '包含预设的独立 ModConfigs'}).check();
  await panel.getByRole('button', {name: '预览便携 ZIP'}).click();
  await expect(panel.getByRole('heading', {name: /便携 ZIP 预览/})).toBeVisible();
  await expect(panel.getByText(/独立配置文件/)).toBeVisible();
  const downloading = page.waitForEvent('download');
  await panel.getByRole('button', {name: '确认下载 ZIP'}).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe('Fixture profile-PSMM.zip');
  const path = await download.path();
  expect(path).toBeTruthy();
  // Playwright's internal filename doesn't retain the extension; supply the visible download name.
  const {readFile} = await import('node:fs/promises');
  await panel.locator('input[type=file]').setInputFiles({name: download.suggestedFilename(), mimeType: 'application/zip', buffer: await readFile(path!)});
  await expect(panel.getByRole('heading', {name: /导入预览/})).toBeVisible();
  await expect(panel.getByText(/同名预设已存在/)).toBeVisible();
  await expect(panel.getByRole('button', {name: '确认导入'})).toBeDisabled();
  await panel.getByRole('textbox', {name: '另存预设名'}).fill('Migration fixture');
  await panel.getByRole('button', {name: '重新预览'}).click();
  await expect(panel.locator('code').filter({hasText: /[\\\\/]Migration fixture$/})).toBeVisible();
  await expect(panel.getByText(/同名预设已存在/)).toHaveCount(0);
  await expect(panel.getByRole('button', {name: '确认导入'})).toBeDisabled(); // Same local mod IDs remain.
  await panel.getByRole('button', {name: '取消', exact: true}).click();
});
