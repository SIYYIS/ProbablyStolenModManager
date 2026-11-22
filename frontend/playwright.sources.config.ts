import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export default defineConfig({
  testMatch: 'source-tools.spec.ts',
  outputDir: join(tmpdir(), 'psmm-source-tools-playwright'), timeout: 15000,
  use: { baseURL: 'http://127.0.0.1:5179', channel: process.env.PSMM_BROWSER_CHANNEL || undefined },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js build --config vite.sources.config.ts && node node_modules/vite/bin/vite.js preview --config vite.sources.config.ts --host 127.0.0.1 --port 5179 --strictPort',
    env: { PSMM_SOURCE_TEST_BUILD: mkdtempSync(join(tmpdir(), 'psmm-source-tools-vite-')) },
    url: 'http://127.0.0.1:5179/source-tools-harness.html', reuseExistingServer: false,
  },
});
