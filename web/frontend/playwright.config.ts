import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', timeout: 180000, workers: 1, retries: 0,
  projects: [
    { name: 'viewer', testMatch: /viewer\.spec\.ts/ },
    { name: 'playback', testMatch: /full-playback\.spec\.ts/, dependencies: ['viewer'] },
    { name: 'density', testMatch: /density\.spec\.ts/ },
  ],
  use: { baseURL: 'http://127.0.0.1:8765', browserName: 'chromium', viewport: { width: 1440, height: 960 }, screenshot: 'only-on-failure', trace: 'retain-on-failure', launchOptions: { args: ['--use-angle=d3d11'] } },
  outputDir: '../test-results/browser', reporter: [['list'], ['json', { outputFile: '../test-results/playwright.json' }]],
});
