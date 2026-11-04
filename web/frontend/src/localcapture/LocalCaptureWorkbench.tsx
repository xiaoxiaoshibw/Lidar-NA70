import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { PageNav } from '../PageNav';

// 本机双流采集：IMU USB 串口 + NA70 网口 UDP，由 8765 后端在同一进程内录制。
// 服务端契约见 web/backend/local_capture.py（复用 na70_core.capture.service）。

type CaptureState = 'idle' | 'starting' | 'recording' | 'stopping' | 'failed';
type FailedCheck = { name: string; detail: string };

type ImuStatus = {
  state: string; error: string; worker_error: string; port: string; baud: number;
  frames: number; bytes: number; hz: number; last_frame_age_s: number | null;
  checksum_errors: number; payload_errors: number; sequence_breaks: number;
  reconnects: number; queue_overflow: number;
};

type LidarStatus = {
  state: string; error: string; worker_error: string; bind_host: string; port: number;
  local_ip: string | null; pcap_bytes: number; captured: number; received: number;
  dropped: number; packets_per_sec: number;
};

type StatusPayload = {
  state: CaptureState; error: string; task: string | null; note: string;
  session_id: string | null; session_path: string | null; duration_s: number;
  remaining_s: number; disk_free_bytes: number | null;
  devices: { imu: ImuStatus | null; lidar: LidarStatus | null } | null;
  last: { session_id: string; status: string; path: string; duration_s: number | null;
          failed_checks: FailedCheck[] } | null;
  sessions: SessionSummary[];
};

type SessionSummary = {
  session_id: string; status: string; task: string | null; created_unix: number | null;
  duration_s: number | null; overlap_s: number | null; failed_checks: FailedCheck[];
  path: string;
};

type PortsPayload = { serial_ports: Array<{ device: string; description: string }>; local_ips: string[] };

const ACTIVE_STATES = new Set<CaptureState>(['starting', 'recording', 'stopping']);
const STATE_LABEL: Record<CaptureState, string> = {
  idle: '空闲', starting: '启动中', recording: '录制中', stopping: '停止中', failed: '失败',
};
const SESSION_LABEL: Record<string, string> = {
  ok: '合格', degraded: '降级', failed: '失败', unfinalized: '未收尾', recording: '录制中',
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) return '—';
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} B`;
}

export default function LocalCaptureWorkbench() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [ports, setPorts] = useState<PortsPayload | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({
    task: '', duration: '', operator: '', note: '',
    imu_port: '', imu_baud: '460800', lidar_port: '2368',
  });

  const refreshCatalog = useCallback(() => {
    api<PortsPayload>('/local-capture/ports')
      .then(result => {
        setPorts(result);
        setForm(current => current.imu_port
          ? current
          : { ...current, imu_port: result.serial_ports[0]?.device ?? '' });
      })
      .catch(error => setStatusError(`串口/地址列表：${errorText(error)}`));
  }, []);

  // 状态轮询：活跃态 1.2 s，空闲 3 s，setTimeout 链式自轮询（与 wire 页一致）。
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await api<StatusPayload>('/local-capture/status');
        if (!live) return;
        setStatus(result);
        setStatusError(null);
        timer = setTimeout(poll, ACTIVE_STATES.has(result.state) ? 1200 : 3000);
      } catch (error) {
        if (!live) return;
        setStatusError(errorText(error));
        timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, []);

  useEffect(() => { refreshCatalog(); }, [refreshCatalog]);

  const active = status != null && ACTIVE_STATES.has(status.state);

  const start = async () => {
    setActionError(null);
    setSubmitting(true);
    try {
      await api('/local-capture/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task: form.task.trim(),
          duration_s: form.duration.trim() ? Number(form.duration) : null,
          operator: form.operator.trim() ? { name: form.operator.trim() } : {},
          note: form.note.trim(),
          imu_port: form.imu_port,
          imu_baud: Number(form.imu_baud) || 460800,
          lidar_port: Number(form.lidar_port) || 2368,
        }),
      });
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const stop = async () => {
    setActionError(null);
    try {
      await api('/local-capture/stop', { method: 'POST' });
    } catch (error) {
      setActionError(errorText(error));
    }
  };

  const imu = status?.devices?.imu ?? null;
  const lidar = status?.devices?.lidar ?? null;
  const primaryIp = ports?.local_ips[0] ?? null;

  return (
    <div className="lc-page">
      <PageNav />
      <header className="lc-header">
        <h1>本机双流采集</h1>
        <p>IMU 经 USB 串口、NA70 经网口 UDP，同一台电脑、同一进程同时录制；
           会话落盘 <code>radar_capture_data/na70_sessions/</code>，与标定批处理工具直接兼容。</p>
      </header>
      {primaryIp && (
        <div className="lc-hint" data-testid="lidar-ip-hint">
          雷达 UDP 目的 IP 须配置为本机地址：<strong>{primaryIp}</strong>
          {ports && ports.local_ips.length > 1 && `（候选：${ports.local_ips.join('、')}）`}
          ；Windows 防火墙首次放行 2368/UDP 端口。
        </div>
      )}
      {statusError && <div className="lc-error">状态获取失败：{statusError}</div>}
      {status?.state === 'failed' && status.error && (
        <div className="lc-error" data-testid="capture-failed">上次会话失败：{status.error}（目录已保留，可新建会话重试）</div>
      )}
      <div className="lc-grid">
        <section className="lc-card">
          <h2>启动会话</h2>
          <label>任务标签
            <input value={form.task} placeholder="如 CAL-STATIC / CAL-ROT / MANUAL"
                   onChange={event => setForm({ ...form, task: event.target.value })} />
          </label>
          <label>时长（秒，留空 = 手动停止，上限 1800）
            <input type="number" min={3} max={1800} value={form.duration}
                   onChange={event => setForm({ ...form, duration: event.target.value })} />
          </label>
          <label>IMU 串口
            <select value={form.imu_port}
                    onChange={event => setForm({ ...form, imu_port: event.target.value })}>
              <option value="">— 选择串口 —</option>
              {ports?.serial_ports.map(item =>
                <option key={item.device} value={item.device}>
                  {item.device}{item.description ? `（${item.description}）` : ''}
                </option>)}
            </select>
          </label>
          <div className="lc-row">
            <label>IMU 波特率
              <input type="number" value={form.imu_baud}
                     onChange={event => setForm({ ...form, imu_baud: event.target.value })} />
            </label>
            <label>雷达 UDP 端口
              <input type="number" value={form.lidar_port}
                     onChange={event => setForm({ ...form, lidar_port: event.target.value })} />
            </label>
          </div>
          <div className="lc-row">
            <label>操作人
              <input value={form.operator}
                     onChange={event => setForm({ ...form, operator: event.target.value })} />
            </label>
            <label>备注
              <input value={form.note}
                     onChange={event => setForm({ ...form, note: event.target.value })} />
            </label>
          </div>
          <div className="lc-actions">
            <button className="lc-primary" disabled={active || submitting || !form.task.trim() || !form.imu_port}
                    onClick={start}>
              {submitting ? '提交中…' : '开始采集'}
            </button>
            <button disabled={!active} onClick={stop}>停止录制</button>
          </div>
          {actionError && <div className="lc-error">{actionError}</div>}
        </section>
        <section className="lc-card" data-testid="capture-status">
          <h2>当前状态：<span className={`lc-state lc-state-${status?.state ?? 'unknown'}`}>
            {status ? STATE_LABEL[status.state] : '加载中'}</span></h2>
          {status ? (
            <dl className="lc-stats">
              <dt>会话</dt><dd>{status.session_id ?? '—'}</dd>
              <dt>任务</dt><dd>{status.task ?? '—'}</dd>
              <dt>剩余/时长</dt><dd>{status.state === 'recording' ? `${Math.round(status.remaining_s)} s / ${status.duration_s} s` : '—'}</dd>
              <dt>磁盘剩余</dt><dd>{formatBytes(status.disk_free_bytes)}</dd>
            </dl>
          ) : <p>等待状态…</p>}
          {imu && (
            <div className="lc-device" data-testid="imu-status">
              <h3>IMU（{imu.port} @ {imu.baud}）</h3>
              <dl className="lc-stats">
                <dt>状态</dt><dd>{imu.state}{imu.error ? `：${imu.error}` : ''}</dd>
                <dt>帧数 / 频率</dt><dd>{imu.frames} 帧 / {imu.hz} Hz</dd>
                <dt>校验错误 / 重连</dt><dd>{imu.checksum_errors} / {imu.reconnects}</dd>
                <dt>tid 断号 / 溢出</dt><dd>{imu.sequence_breaks} / {imu.queue_overflow}</dd>
              </dl>
            </div>
          )}
          {lidar && (
            <div className="lc-device" data-testid="lidar-status">
              <h3>NA70（UDP {lidar.bind_host}:{lidar.port}）</h3>
              <dl className="lc-stats">
                <dt>状态</dt><dd>{lidar.state}{lidar.error ? `：${lidar.error}` : ''}</dd>
                <dt>包数 / 速率</dt><dd>{lidar.captured} / {lidar.packets_per_sec} 包·s⁻¹</dd>
                <dt>PCAP 大小</dt><dd>{formatBytes(lidar.pcap_bytes)}</dd>
                <dt>本机地址</dt><dd>{lidar.local_ip ?? '—'}</dd>
              </dl>
            </div>
          )}
        </section>
      </div>
      <section className="lc-card lc-sessions" data-testid="session-list">
        <h2>历史会话（radar_capture_data/na70_sessions）</h2>
        {status && status.sessions.length === 0 && <p>暂无会话。</p>}
        {status && status.sessions.length > 0 && (
          <table>
            <thead>
              <tr><th>会话</th><th>任务</th><th>状态</th><th>时长</th><th>重叠</th><th>未过项</th><th>目录</th></tr>
            </thead>
            <tbody>
              {status.sessions.map(item => (
                <tr key={item.session_id}>
                  <td>{item.session_id}</td>
                  <td>{item.task ?? '—'}</td>
                  <td className={`lc-session-${item.status}`}>{SESSION_LABEL[item.status] ?? item.status}</td>
                  <td>{item.duration_s != null ? `${item.duration_s.toFixed(1)} s` : '—'}</td>
                  <td>{item.overlap_s != null ? `${item.overlap_s.toFixed(1)} s` : '—'}</td>
                  <td>{item.failed_checks.length === 0 ? '—'
                    : item.failed_checks.map(check => check.name).join('、')}</td>
                  <td className="lc-path">{item.path}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
