export type Capture = { id: string; filename: string; size: number; status: string; progress: number; frame_count: number; duration: number; valid_packets: number; invalid_packets: number; ignored_packets: number; warnings: string[]; error: string | null; created_at: number; sha256?: string; analysis_status: string; analysis_progress: number; analysis_error: string | null; background_seconds?: number; analysis_seconds?: number; moving_frames?: number; moving_tracks?: number };
export type FrameInfo = { index: number; time: number; capture_time: number; end_time: number; device_time: number | null; frame_id: string; point_count: number; complete: boolean; packet_count: number };
export type MotionTarget = { band_id: number; id: number; label: string; status: string; position: number[]; velocity: number[]; speed: number; minimum: number[]; maximum: number[]; size: number[]; point_count: number; slice_index: number; time: number; duration: number; displacement: number; trajectory_length: number; linearity: number; score: number; time_start: number; time_end: number };
export type MotionBandSlice = { bucket: number; time: number; count: number; centre: number[] };
export type MotionBand = { band_id: number; label: string; score: number; point_count: number; duration: number; time_start: number; time_end: number; displacement: number; trajectory_length: number; speed: number; path_speed: number; linearity: number; linearity_branches: number; branches: number; regression_rms: number; regression_rms_split: number; coverage: number; slice_count: number; local_cross_norm: number; local_size_norm: number; spatial_span: number; centroid: number[]; start: number[]; end: number[]; main_direction: number[]; local_cross_size: number[]; slices: MotionBandSlice[] };
export type MotionFrameInfo = { index: number; time: number; ready: boolean; considered_count: number; candidate_count: number; moving_count: number; has_motion: boolean; point_count: number; moving_bands: number; clusters: Array<{ track_id: number; track_status: string; point_count: number; speed: number; displacement: number; moving: boolean; centroid: number[]; minimum: number[]; maximum: number[] }>; tracks: Array<{ id: number; status: string; position: number[]; velocity: number[]; speed: number; hits: number; missed: number; age: number }>; targets: MotionTarget[] };
export type Frame = { meta: FrameInfo; points: Float32Array; motion?: Uint8Array; motionMeta?: MotionFrameInfo };
export type AnalysisSummary = { version: string; method: string; frame_count: number; warmup_frames: number; moving_frames: number; candidate_points: number; moving_points: number; moving_tracks: number; band_count: number; band_note: string; bands: MotionBand[]; band_diagnostics: { raw_clusters?: number; accepted_bands?: number; rejected_bands?: number; merged_fragments?: number; rejected_reasons?: Record<string, number> }; elapsed_seconds: number; events: Array<{ start_frame: number; end_frame: number; start_time: number; end_time: number; peak_moving_points: number }>; tracks: Array<{ id: number; moving: boolean; hits: number; age: number; max_speed: number; max_displacement: number }> };
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
export async function readFrame(id: string, index: number, signal: AbortSignal, jobId?: string): Promise<Frame> {
  const response = await fetch(jobId ? `/api/mapping/jobs/${jobId}/frames/${index}/cloud` : `/api/captures/${id}/frames/${index}`, { signal });
  if (!response.ok) throw new Error((await response.json()).detail || '读取点云失败');
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength < 4) throw new Error('点云响应截断');
  const length = new DataView(buffer).getUint32(0, true);
  if (length % 4 || length > buffer.byteLength - 4) throw new Error('点云元数据无效');
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
  if (meta.version !== 1 || meta.dtype !== 'float32-le' || buffer.byteLength - 4 - length !== meta.point_count * 16) throw new Error('点云格式或长度不匹配');
  return { meta, points: new Float32Array(buffer, 4 + length) };
}
export async function readMotionFrame(id: string, index: number, signal: AbortSignal): Promise<{ meta: MotionFrameInfo; mask: Uint8Array }> {
  const response = await fetch(`/api/captures/${id}/analysis/frames/${index}`, { signal });
  if (!response.ok) throw new Error((await response.json()).detail || '读取运动分析结果失败');
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength < 4) throw new Error('运动分析响应截断');
  const length = new DataView(buffer).getUint32(0, true);
  if (length % 4 || length > buffer.byteLength - 4) throw new Error('运动分析元数据无效');
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length))) as MotionFrameInfo & { version: number; dtype: string };
  if (meta.version !== 1 || meta.dtype !== 'uint8' || buffer.byteLength - 4 - length !== meta.point_count) throw new Error('运动分析格式或长度不匹配');
  return { meta, mask: new Uint8Array(buffer, 4 + length) };
}
export const clock = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;

// ---------------------------------------------------------------- mapping job
// The backend owns the whole chain (decode -> preprocess -> register -> fuse ->
// persist). The browser only asks for a trajectory, a bounded set of map chunks
// and per-frame quality; it never transforms points itself.

export type Bounds = { minimum: number[]; maximum: number[] };
export type MappingMetrics = {
  initialization_frames?: number; registered_frames?: number; recovered_frames?: number;
  frame_count: number; accepted_frames: number; rejected_frames: number; tracking_lost_frames: number;
  keyframe_count: number | null; accepted_ratio: number; rejected_ratio: number;
  first_tracking_lost_time: number | null; max_continuous_tracking_s: number;
  mean_overlap: number | null; mean_rmse_m: number | null;
  max_translation_step_m: number | null; max_rotation_step_deg: number | null;
  trajectory_length_m: number; closure_translation_m: number | null; closure_rotation_deg: number | null;
  segment_count: number; map_point_count: number; map_voxel_count: number; map_bounds: Bounds | null;
  chunk_count?: number;
};
export type MappingJob = {
  capture_preflight?: { status: string; reasons?: string[]; metadata: unknown; real_movement_acceptance: string };
  job_id: string; capture_id: string; input_sha256: string | null; code_version: string; config_version: string;
  status: string; progress: number; created_at: number; started_at: number | null; finished_at: number | null;
  frame_count: number; accepted_frames: number; rejected_frames: number; tracking_lost_frames: number;
  keyframe_count: number; map_point_count: number; map_bounds: Bounds | null;
  first_frame_index: number; last_frame_index: number; imu_used: boolean; loop_closure_used: boolean;
  stage: string; error: string | null; elapsed_seconds: number | null; peak_memory_mb: number | null;
  metrics: MappingMetrics;
};
export type FrameState = 'reference' | 'accepted' | 'quality_failed' | 'insufficient_points' | 'degenerate_geometry' | 'timestamp_invalid' | 'tracking_lost' | 'reinitialized';
export type MappingFrame = {
  spacing?: { within_frame?: { p50: number } | null }; submap_frame_count?: number; normal_matrix_ratio?: number | null;
  index: number; timestamp: number; state: FrameState; reason: string;
  overlap: number | null; rmse: number | null; translation_step_m: number | null;
  rotation_step_deg: number | null; iterations: number | null; point_count: number;
  keyframe: boolean; segment_id: number; pose: number[][] | null;
  /** Solver output for a rejected frame, for display only - never fused. */
  candidate_pose: number[][] | null;
};
export type MappingSegment = {
  segment_id: number; start_frame_index: number; end_frame_index: number; keyframe_ids: number[];
  length_m: number; closure: { translation_m: number; rotation_deg: number; first_frame_index: number; last_frame_index: number } | null;
};
export type MappingTrajectory = {
  version: string; segments: MappingSegment[];
  frames: Array<{ frame_index: number; timestamp: number; state: FrameState; reason: string; pose: number[][] | null; candidate_pose: number[][] | null; segment_id: number; keyframe: boolean }>;
};
export type MapChunkInfo = { id: string; key: number[]; file: string; voxel_count: number; minimum: number[]; maximum: number[] };
export type MapMetadata = {
  version: string; voxel_size: number; chunk_voxels: number; fused_points: number; dropped_points: number;
  voxel_count: number; chunk_count: number; max_voxels: number;
  segments: Array<{ segment_id: number; voxels: number; observations: number }>;
  minimum: number[] | null; maximum: number[] | null;
  imu_used: boolean; loop_closure_used: boolean; stage: string; input_sha256: string | null;
  config_version: string; chunk_index: string; chunks: MapChunkInfo[];
};
export type MapChunk = { id: string; key: number[]; minimum: number[]; maximum: number[]; voxel_count: number; total_voxels: number; voxels: number[][] };
export type MapChunkResponse = {
  job_id: string; voxel_size: number; fields: string[]; chunk_voxels: number; chunk_count: number;
  returned_chunks: number; returned_voxels: number; truncated: boolean; chunks: MapChunk[];
};

// Frame-state colours are the acceptance vocabulary of the mapping stage:
// green = accepted, yellow = quality gate failed, orange = degenerate geometry,
// red = tracking lost, grey = not processed.  Rejected frames are never hidden.
export const STATE_COLOR: Record<string, string> = {
  accepted: '#2f8f6a', reference: '#2f8f6a', reinitialized: '#4aa8d8',
  quality_failed: '#d8a22f', insufficient_points: '#d8a22f',
  degenerate_geometry: '#e07a1f', timestamp_invalid: '#b45fd0',
  tracking_lost: '#c8392a', pending: '#334350',
};
export const STATE_LABEL: Record<string, string> = {
  accepted: '已接受', reference: '参考帧', reinitialized: '重新初始化', quality_failed: '质量不达标',
  insufficient_points: '点数不足', degenerate_geometry: '几何退化', tracking_lost: '跟踪失锁',
  timestamp_invalid: '时间戳异常', pending: '未处理',
};
export const JOB_ACTIVE = new Set(['queued', 'running', 'cancelling']);

export const createMappingJob = (captureId: string, config?: Record<string, unknown>, signal?: AbortSignal) =>
  api<MappingJob>('/mapping/jobs', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ capture_id: captureId, ...(config ? { config } : {}) }) });
export const readMappingJob = (jobId: string, signal?: AbortSignal) => api<MappingJob>(`/mapping/jobs/${jobId}`, { signal });
export const cancelMappingJob = (jobId: string) => api<MappingJob>(`/mapping/jobs/${jobId}/cancel`, { method: 'POST' });
export const readTrajectory = (jobId: string, signal?: AbortSignal) => api<MappingTrajectory>(`/mapping/jobs/${jobId}/trajectory`, { signal });
export const readMapMetadata = (jobId: string, signal?: AbortSignal) => api<MapMetadata>(`/mapping/jobs/${jobId}/map`, { signal });
export const readFrameQuality = (jobId: string, index: number, signal?: AbortSignal) => api<MappingFrame>(`/mapping/jobs/${jobId}/frames/${index}`, { signal });
export const readMapChunks = (jobId: string, query: string, signal?: AbortSignal) => api<MapChunkResponse>(`/mapping/jobs/${jobId}/map/chunks?${query}`, { signal });
export const exportMappingJob = (jobId: string) => api<{ bundle: string; bundle_bytes: number; members: string[]; input_sha256: string; config_version: string; code_version: string }>(`/mapping/jobs/${jobId}/export`, { method: 'POST' });
export const mapChunkIdOf = (key: number[]) => key.join('_');
