import { expect, test } from '@playwright/test';

// 本机双流接收页：全 mock /api/local-capture/**，不起真实后端与设备。

test('本机双流接收页可以启动、停止并看到会话列表', async ({ page }) => {
  let recording = false;
  const status = () => ({
    state: recording ? 'recording' : 'idle',
    error: '',
    task: recording ? 'MANUAL' : null,
    session_id: recording ? '20261104-000000' : null,
    session_path: recording ? 'D:/sessions/20261104-000000' : null,
    duration_s: 1800,
    remaining_s: 1799,
    devices: recording ? {
      imu: { state: 'live', error: '', frames: 240, hz: 200.0, port: 'COM3', baud: 460800 },
      lidar: { state: 'live', error: '', pcap_bytes: 4096, captured: 120,
               packets_per_sec: 60.0, local_ip: '192.168.1.129' },
    } : null,
    sessions: [{ session_id: '20261104-000000', status: 'ok', task: 'MANUAL',
                 duration_s: 12.3, failed_checks: [] }],
    local_ips: ['10.12.150.7', '192.168.1.129'],
  });
  await page.route('**/api/local-capture/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/start')) {
      recording = true;
      return route.fulfill({ status: 202, json: { session_id: '20261104-000000', state: 'starting' } });
    }
    if (url.pathname.endsWith('/stop')) {
      recording = false;
      return route.fulfill({ json: { state: 'idle' } });
    }
    if (url.pathname.endsWith('/status')) return route.fulfill({ json: status() });
    return route.fulfill({ json: {} });
  });
  await page.goto('/local-capture.html');
  await expect(page.getByRole('navigation')).toContainText('本机双流接收');
  await expect(page.getByTestId('lidar-ip-hint')).toHaveText(/192\.168\.1\.129/);
  await expect(page.getByTestId('lidar-ip-hint')).toHaveText(/10\.12\.150\.7/);

  const startButton = page.getByTestId('start');
  await expect(startButton).toBeDisabled();                      // 未填 IMU 串口
  await page.locator('input[placeholder="COM3"]').fill('COM3');
  await expect(startButton).toBeEnabled();
  await startButton.click();
  await expect(page.getByTestId('state')).toHaveText('录制中');
  await expect(page.locator('ul')).toContainText('IMU：live，240 帧，200 Hz');
  await expect(page.locator('ul')).toContainText('NA70：live，120 包');
  await page.getByTestId('stop').click();
  await expect(page.getByTestId('state')).toHaveText('空闲');
  await expect(page.getByTestId('session-list')).toContainText('20261104-000000');
});

test('启动错误在状态轮询后仍可见', async ({ page }) => {
  await page.route('**/api/local-capture/**', async route => {
    if (route.request().url().endsWith('/start')) {
      return route.fulfill({ status: 409, json: { detail: '磁盘剩余空间不足' } });
    }
    return route.fulfill({ json: {
      state: 'idle', error: '', task: null, session_id: null, session_path: null,
      duration_s: 0, remaining_s: 0, devices: null, sessions: [], local_ips: [],
    } });
  });
  await page.goto('/local-capture.html');
  await page.locator('input[placeholder="COM3"]').fill('COM3');
  await page.getByTestId('start').click();
  await expect(page.getByRole('alert')).toHaveText('磁盘剩余空间不足');
  await page.waitForTimeout(3500);
  await expect(page.getByRole('alert')).toHaveText('磁盘剩余空间不足');
});
