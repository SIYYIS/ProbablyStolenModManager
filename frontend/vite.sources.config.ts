import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const outDir = process.env.PSMM_SOURCE_TEST_BUILD;
if (!outDir) throw new Error('Run via playwright.sources.config.ts');

export default defineConfig({
  plugins: [react()],
  build: { outDir, emptyOutDir: false, rollupOptions: { input: 'source-tools-harness.html' } },
});
