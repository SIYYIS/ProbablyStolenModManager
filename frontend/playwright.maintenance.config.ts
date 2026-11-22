import {defineConfig} from '@playwright/test';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

export default defineConfig({
  testDir: './maintenance-e2e', workers: 1,
  outputDir: mkdtempSync(join(tmpdir(), 'psmm-maintenance-browser-')),
  use: {baseURL: 'http://127.0.0.1:8894', viewport: {width: 480, height: 850},
    channel: process.env.PSMM_BROWSER_CHANNEL || undefined},
  webServer: {command: 'npm run dev -- --port 8894 --strictPort', url: 'http://127.0.0.1:8894/maintenance-e2e/',
    timeout: 15000, reuseExistingServer: false},
});
