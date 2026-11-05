const { chromium } = require('../../../..//web/frontend/node_modules/playwright');
const { pathToFileURL } = require('url');
const path = require('path');
const assert = require('assert');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(pathToFileURL(path.join(__dirname, 'index.html')).href);
  assert((await page.locator('#imuInfo').textContent()).includes('无 IMU 数据'));
  assert((await page.locator('#poseInfo').textContent()).includes('无历史 LIO'));
  await page.locator('#seek').evaluate(e => { e.value = '60'; e.dispatchEvent(new Event('input')); });
  assert((await page.locator('#imuInfo').textContent()).includes('IMU 批次'));
  assert((await page.locator('#poseInfo').textContent()).includes('诊断 LIO 当前位姿'));
  assert((await page.locator('#cloudInfo').textContent()).includes('源帧 60'));
  await page.screenshot({path: path.join(__dirname, 'preview.png'), fullPage: true});
  await page.locator('#seek').evaluate(e => { e.value = e.max; e.dispatchEvent(new Event('input')); });
  assert((await page.locator('#imuInfo').textContent()).includes('无 IMU 数据'));
  assert((await page.locator('#poseInfo').textContent()).includes('无历史 LIO'));
  await page.locator('#play').click();
  await page.waitForTimeout(650);
  assert(Number(await page.locator('#seek').inputValue()) > 0, 'playback did not advance');
  assert.deepStrictEqual(errors, []);
  console.log('PASS: real-input offline page, IMU and LIO coverage boundaries, frame 60, playback, no JS errors');
  await browser.close();
})().catch(e => { console.error(e); process.exitCode = 1; });
