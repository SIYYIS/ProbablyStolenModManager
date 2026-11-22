import {defineConfig} from '@playwright/test';

const port = Number(process.env.PSMM_TEST_PORT ?? 8873);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid fixture port');
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false, timeout: 25000,
  use: {baseURL, viewport: {width: 1360, height: 850},
    channel: process.env.PSMM_BROWSER_CHANNEL || undefined},
  webServer: {command: `py -3.10 -B ../tests/webui_fixture.py --port ${port}`, url: baseURL,
    timeout: 15000, reuseExistingServer: false},
});
