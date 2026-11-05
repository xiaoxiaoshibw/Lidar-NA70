import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', timeout: 180000, workers: 1, retries: 0,
  projects: [
    { name: 'viewer', testMatch: /viewer\.spec\.ts/ },
    { name: 'playback', testMatch: /full-playback\.spec\.ts/, dependencies: ['viewer'] },
    { name: 'density', testMatch: /density\.spec\.ts/ },
    { name: 'mapping', testMatch: /mapping\.spec\.ts/ },
    { name: 'motion', testMatch: /motion\.spec\.ts/ },
    { name: 'turntable', testMatch: /turntable\.spec\.ts/ },
    { name: 'demo-observability', testMatch: /demo-observability\.spec\.ts/ },
    { name: 'demo-identity', testMatch: /demo-identity\.spec\.ts/ },
    { name: 'wire', testMatch: /wire-workbench\.spec\.ts/ },
    { name: 'cloud-binary', testMatch: /cloud-binary\.spec\.ts/ },
    { name: 'capture', testMatch: /capture\.spec\.ts/ },
    { name: 'capture-hardware', testMatch: /capture-hardware\.spec\.ts/ },
    { name: 'local-capture', testMatch: /local-capture\.spec\.ts/ },
  ],
  use: { baseURL: process.env.NA70_E2E_BASE_URL || 'http://127.0.0.1:8765', browserName: 'chromium', viewport: { width: 1440, height: 960 }, screenshot: 'only-on-failure', trace: 'retain-on-failure', launchOptions: { args: ['--use-angle=d3d11'] } },
  outputDir: '../test-results/browser', reporter: [['list'], ['json', { outputFile: '../test-results/playwright.json' }]],
});
