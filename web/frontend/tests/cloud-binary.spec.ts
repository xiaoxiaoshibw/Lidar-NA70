import { expect, test } from '@playwright/test';
import { parseCloudPoints, parseMotionMask } from '../src/lib/cloudBinary';

function envelope(meta: Record<string, unknown>, payload: Float32Array | Uint8Array): ArrayBuffer {
  const encoded = new TextEncoder().encode(JSON.stringify(meta));
  const headerBytes = Math.ceil(encoded.length / 4) * 4;
  const buffer = new ArrayBuffer(4 + headerBytes + payload.byteLength);
  const view = new DataView(buffer);
  view.setUint32(0, headerBytes, true);
  new Uint8Array(buffer, 4, headerBytes).fill(32);
  new Uint8Array(buffer, 4, encoded.length).set(encoded);
  new Uint8Array(buffer, 4 + headerBytes).set(
    new Uint8Array(payload.buffer, payload.byteOffset, payload.byteLength));
  return buffer;
}

test('strict cloud parser preserves point values and rejects mismatched frames', () => {
  const cloud = envelope(
    { version: 3, dtype: 'float32-le', point_count: 1,
      fields: ['x', 'y', 'z', 'intensity', 'classification', 'highlight'] },
    new Float32Array([1, 2, 3, 40, 2, 1]));
  const parsed = parseCloudPoints(cloud, '调试云', [3]);
  expect(Array.from(parsed.points)).toEqual([1, 2, 3, 40, 2, 1]);
  expect(parsed.meta.point_count).toBe(1);
  expect(() => parseCloudPoints(cloud, '调试云', [1])).toThrow('格式或长度不匹配');
  expect(() => parseCloudPoints(cloud.slice(0, -4), '调试云', [3])).toThrow('格式或长度不匹配');
  expect(() => parseCloudPoints(cloud.slice(0, 3), '调试云', [3])).toThrow('响应截断');
  for (const count of [-1, 0.5, '1', null]) {
    const malformed = envelope({ version: 3, dtype: 'float32-le', point_count: count }, new Float32Array([1, 2, 3, 4]));
    expect(() => parseCloudPoints(malformed, '调试云', [3])).toThrow('元数据无效');
  }
  const wrongFields = envelope(
    { version: 3, dtype: 'float32-le', point_count: 1, fields: ['x', 'y', 'z'] },
    new Float32Array([1, 2, 3, 4]));
  expect(() => parseCloudPoints(wrongFields, '调试云', [3], 4)).toThrow('格式或长度不匹配');
  expect(parseCloudPoints(envelope({ version: 3, dtype: 'float32-le', point_count: 0 }, new Uint8Array()), '调试云', [3]).points.length).toBe(0);
});

test('motion mask parser preserves one byte per point and checks declared size', () => {
  const mask = envelope({ version: 1, dtype: 'uint8', point_count: 3 }, new Uint8Array([0, 1, 2]));
  expect(Array.from(parseMotionMask(mask, '运动分析').mask)).toEqual([0, 1, 2]);
  expect(() => parseMotionMask(mask.slice(0, -1), '运动分析')).toThrow('格式或长度不匹配');
});
