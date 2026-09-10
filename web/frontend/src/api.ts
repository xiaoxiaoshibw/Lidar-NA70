export type Capture = { id: string; filename: string; size: number; status: string; progress: number; frame_count: number; duration: number; valid_packets: number; invalid_packets: number; ignored_packets: number; warnings: string[]; error: string | null; created_at: number; sha256?: string };
export type FrameInfo = { index: number; time: number; capture_time: number; end_time: number; device_time: number | null; frame_id: string; point_count: number; complete: boolean; packet_count: number };
export type Frame = { meta: FrameInfo; points: Float32Array };
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, options);
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try { message = JSON.parse(text).detail; } catch { /* text error */ }
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return response.status === 204 ? undefined as T : response.json();
}
export async function readFrame(id: string, index: number, signal: AbortSignal): Promise<Frame> {
  const response = await fetch(`/api/captures/${id}/frames/${index}`, { signal });
  if (!response.ok) throw new Error((await response.json()).detail || '读取点云失败');
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength < 4) throw new Error('点云响应截断');
  const length = new DataView(buffer).getUint32(0, true);
  if (length % 4 || length > buffer.byteLength - 4) throw new Error('点云元数据无效');
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
  if (meta.version !== 1 || meta.dtype !== 'float32-le' || buffer.byteLength - 4 - length !== meta.point_count * 16) throw new Error('点云格式或长度不匹配');
  return { meta, points: new Float32Array(buffer, 4 + length) };
}
export const clock = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
