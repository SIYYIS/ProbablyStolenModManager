import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: '.', workers: 1, timeout: 20_000,
  outputDir: '../test-results/diagnostics',
  use: {baseURL: 'http://127.0.0.1:8876', channel: process.env.PSMM_BROWSER_CHANNEL || undefined},
  webServer: {command: 'py -3.10 -B ../tests/diagnostic_fixture.py', url: 'http://127.0.0.1:8876', cwd: '..'},
});
