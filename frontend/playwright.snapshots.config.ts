import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './e2e-snapshots', workers: 1, timeout: 25000,
  use: {baseURL: 'http://127.0.0.1:8878', viewport: {width: 1360, height: 850},
    channel: process.env.PSMM_BROWSER_CHANNEL || undefined},
  webServer: {command: 'py -3.10 -B ../tests/snapshot_ui_fixture.py --port 8878', url: 'http://127.0.0.1:8878',
    timeout: 15000, reuseExistingServer: false},
});
