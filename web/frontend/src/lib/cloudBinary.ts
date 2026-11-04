// Shared strict parser for the backend's length-prefixed binary cloud responses:
// `<uint32-le header length><JSON header padded to 4 bytes><typed payload>`.
// Truncated buffers, wrong version/dtype and payload sizes that disagree with
// the declared point_count must throw — callers must never render unvalidated
// bytes. Error messages reuse the caller-provided label (点云 / 叠加云 / 运动分析 / …)
// so each endpoint keeps its original wording.

type EnvelopeMeta = { version: number; dtype: string; point_count: number; fields?: unknown };

function readEnvelope<M>(buffer: ArrayBuffer, label: string): {
  meta: M & EnvelopeMeta; payloadOffset: number; payloadBytes: number;
} {
  if (buffer.byteLength < 4) throw new Error(`${label}响应截断`);
  const length = new DataView(buffer).getUint32(0, true);
  if (!length || length % 4 || length > buffer.byteLength - 4) throw new Error(`${label}元数据无效`);
  let meta: M & EnvelopeMeta;
  try {
    meta = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(buffer, 4, length))) as M & EnvelopeMeta;
  } catch {
    throw new Error(`${label}元数据无效`);
  }
  if (meta === null || typeof meta !== 'object' || Array.isArray(meta)
      || !Number.isSafeInteger(meta.point_count) || meta.point_count < 0) {
    throw new Error(`${label}元数据无效`);
  }
  const payloadOffset = 4 + length;
  return { meta, payloadOffset, payloadBytes: buffer.byteLength - payloadOffset };
}

/** float32-le payload, `floatsPerPoint` values per point
 *  (default: header `fields` length when present, else x/y/z/intensity = 4). */
export function parseCloudPoints<M>(
  buffer: ArrayBuffer, label: string, versions: readonly number[], floatsPerPoint?: number,
): { meta: M & EnvelopeMeta; points: Float32Array } {
  const { meta, payloadOffset, payloadBytes } = readEnvelope<M>(buffer, label);
  if (!versions.includes(meta.version) || meta.dtype !== 'float32-le') {
    throw new Error(`${label}格式或长度不匹配`);
  }
  const stride = floatsPerPoint
    ?? (Array.isArray(meta.fields) && meta.fields.length > 0 ? meta.fields.length : 4);
  if (!Number.isSafeInteger(stride) || stride <= 0 ||
      (meta.fields !== undefined && (!Array.isArray(meta.fields)
        || meta.fields.length !== stride
        || !meta.fields.every(field => typeof field === 'string')))) {
    throw new Error(`${label}格式或长度不匹配`);
  }
  if (payloadBytes !== meta.point_count * stride * 4) throw new Error(`${label}格式或长度不匹配`);
  return { meta, points: new Float32Array(buffer, payloadOffset, meta.point_count * stride) };
}

/** uint8 payload, one mask byte per declared point. */
export function parseMotionMask<M>(
  buffer: ArrayBuffer, label: string, versions: readonly number[] = [1],
): { meta: M & EnvelopeMeta; mask: Uint8Array } {
  const { meta, payloadOffset, payloadBytes } = readEnvelope<M>(buffer, label);
  if (!versions.includes(meta.version) || meta.dtype !== 'uint8'
      || payloadBytes !== meta.point_count) {
    throw new Error(`${label}格式或长度不匹配`);
  }
  return { meta, mask: new Uint8Array(buffer, payloadOffset, meta.point_count) };
}
