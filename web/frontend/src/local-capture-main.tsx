import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api } from './api';
import { PageNav } from './PageNav';

// 本机最小双流接收页：只负责同时录 IMU（USB 串口）+ NA70（网口 UDP）到磁盘，
// 不做任何处理；处理/标定由既有离线工具完成（接收完再处理）。

type FailedCheck = { name: string; detail: string };

type StatusPayload = {
  state: 'idle' | 'starting' | 'recording' | 'stopping' | 'failed';
  error: string;
  task: string | null;
  session_id: string | null;
  session_path: string | null;
  duration_s: number;
  remaining_s: number;
  devices: {
    imu: { state: string; error: string; frames: number; hz: number; port: string; baud: number } | null;
    lidar: { state: string; error: string; pcap_bytes: number; captured: number;
             packets_per_sec: number; local_ip: string | null } | null;
  } | null;
  sessions: Array<{ session_id: string; status: string; task: string | null;
                    duration_s: number | null; failed_checks: FailedCheck[] }>;
  local_ips: string[];
};

const ACTIVE = new Set(['starting', 'recording', 'stopping']);
const STATE_LABEL: Record<string, string> = {
  idle: '空闲', starting: '启动中', recording: '录制中', stopping: '停止中', failed: '失败',
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} B`;
}

export default function LocalCapturePage() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    task: '', imu_port: '', imu_baud: '460800', duration: '',
  });

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await api<StatusPayload>('/local-capture/status');
        if (!live) return;
        setStatus(result);
        setConnectionError(null);
        timer = setTimeout(poll, ACTIVE.has(result.state) ? 1200 : 3000);
      } catch (caught) {
        if (!live) return;
        setConnectionError(errorText(caught));
        timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, []);

  const active = status != null && ACTIVE.has(status.state);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ state: StatusPayload['state'] }>('/local-capture/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task: form.task.trim() || undefined,
          duration_s: form.duration.trim() ? Number(form.duration) : null,
          imu_port: form.imu_port.trim(),
          imu_baud: Number(form.imu_baud) || 460800,
        }),
      });
      setStatus(previous => previous ? { ...previous, state: result.state } : previous);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ state: StatusPayload['state'] }>('/local-capture/stop', { method: 'POST' });
      setStatus(previous => previous ? { ...previous, state: result.state } : previous);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const imu = status?.devices?.imu ?? null;
  const lidar = status?.devices?.lidar ?? null;

  return (
    <div style={{ fontFamily: 'sans-serif', margin: '0 auto', maxWidth: 860, padding: 16 }}>
      <PageNav />
      <h1>本机双流接收</h1>
      <p style={{ color: '#555' }}>
        IMU 经 USB 串口、NA70 经网口 UDP 2368 端口，同时录到 <code>radar_capture_data/na70_sessions/</code>。
        只收数据，处理后续再做。雷达 UDP 目的 IP 须配为与雷达相连的本机网卡 IP；候选：
        <strong data-testid="lidar-ip-hint"> {status?.local_ips?.join('、') || '未获取到，请检查网卡'}</strong>。
      </p>
      <fieldset style={{ border: '1px solid #ccc', padding: 12 }} disabled={active || busy}>
        <label>任务标签 <input style={{ width: 160 }} value={form.task} placeholder="默认 MANUAL"
          onChange={event => setForm({ ...form, task: event.target.value })} /></label>{' '}
        <label>IMU 串口 <input style={{ width: 90 }} value={form.imu_port} placeholder="COM3"
          onChange={event => setForm({ ...form, imu_port: event.target.value })} /></label>{' '}
        <label>波特率 <input style={{ width: 80 }} type="number" value={form.imu_baud}
          onChange={event => setForm({ ...form, imu_baud: event.target.value })} /></label>{' '}
        <label>时长秒（留空时手动停止，最长 1800 秒） <input style={{ width: 70 }} type="number" value={form.duration}
          onChange={event => setForm({ ...form, duration: event.target.value })} /></label>
      </fieldset>
      <p>
        <button data-testid="start" disabled={!status || active || busy || !form.imu_port.trim()} onClick={start}>开始接收</button>{' '}
        <button data-testid="stop" disabled={!active || busy || status?.state === 'stopping'} onClick={stop}>停止</button>
      </p>
      {error && <p style={{ color: '#b00' }} role="alert">{error}</p>}
      {connectionError && <p style={{ color: '#b00' }} role="alert">状态连接失败：{connectionError}</p>}
      {status?.state === 'failed' && status.error
        && <p style={{ color: '#b00' }} data-testid="capture-failed">上次会话失败：{status.error}（目录已保留）</p>}
      <h2>状态：<span data-testid="state">{status ? STATE_LABEL[status.state] : '加载中'}</span></h2>
      {status && (
        <ul data-testid="capture-status">
          <li>会话：{status.session_id ?? '—'}{status.session_path ? `（${status.session_path}）` : ''}</li>
          {status.state === 'recording' && <li>剩余 {Math.round(status.remaining_s)} s</li>}
          {imu && <li>IMU：{imu.state}，{imu.frames} 帧，{imu.hz} Hz{imu.error ? `，${imu.error}` : ''}</li>}
          {lidar && <li>NA70：{lidar.state}，{lidar.captured} 包，{lidar.packets_per_sec} 包/s，{formatBytes(lidar.pcap_bytes)}{lidar.error ? `，${lidar.error}` : ''}</li>}
        </ul>
      )}
      <h2>历史会话</h2>
      {status && status.sessions.length === 0 && <p>暂无会话。</p>}
      {status && status.sessions.length > 0 && (
        <table border={1} cellPadding={4}>
          <thead><tr><th>会话</th><th>任务</th><th>状态</th><th>时长</th></tr></thead>
          <tbody data-testid="session-list">
            {status.sessions.map(item => (
              <tr key={item.session_id}>
                <td>{item.session_id}</td><td>{item.task ?? '—'}</td>
                <td>{item.status}</td>
                <td>{item.duration_s != null ? `${item.duration_s.toFixed(1)} s` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<LocalCapturePage />);
