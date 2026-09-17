import { test, expect } from '@playwright/test';

test('turntable workbench states estimated-angle development boundary', async ({ page }) => {
  await page.goto('/');
  const panel = page.locator('.turntable-workbench');
  await expect(panel).toContainText('固定点转台多视角建图');
  await expect(panel).toContainText('当前角度为估计值，结果仅供开发验证');
  await expect(panel).toContainText('不是完整 SLAM');
  await expect(panel.getByLabel('转台角度日志')).toBeVisible();
  await expect(panel.getByLabel('转台标定文件')).toBeVisible();
  await expect(panel.getByRole('button', { name: '开始转台建图' })).toBeDisabled();
});
