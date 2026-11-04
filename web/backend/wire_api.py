"""Persistent, bounded WebUI access to the existing W1 raw-return audit.

This module does not detect a wire.  A completed diagnostic remains
NOT_EVALUATED until independent, reviewed target and background evidence exists.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
import numpy as np

from .thin_wire import SCHEMA as W1_SCHEMA, load_config, run_audit, sha256_file, validate_config


SCHEMA = "wire-web-v1"
MAX_ACTIVE_RUNS = 2  # One worker and at most one waiting job per application.
MAX_CLOUD_POINTS = 10_000
INTERRUPTED = "服务重启时任务尚未完成；请新建运行，旧证据已保留"
WEB_CONFIG_BUDGET = {
    "max_window_points": 2_000_000,
    "max_windows": 64,
    "render_max_points": 60_000,
    "render_dpi": 150,
}
MAX_WINDOW_DURATIONS = 8
MAX_WINDOW_SECONDS = 5.0
MAX_BACKGROUND_ROIS = 8
MAX_CONFIG_BYTES = 65_536


def _write_json_once(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)


def _write_state(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    result = json.loads(path.read_text("utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return result


def _config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _web_config(supplied: dict) -> dict:
    try:
        config = validate_config(supplied)
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(422, f"config 无效: {error}") from error
    if len(encoded) > MAX_CONFIG_BYTES:
        raise HTTPException(422, f"config 超过 {MAX_CONFIG_BYTES} 字节上限")
    for key, ceiling in WEB_CONFIG_BUDGET.items():
        if config[key] > ceiling:
            raise HTTPException(422, f"config.{key} 不得超过 WebUI 上限 {ceiling}")
    if len(config["window_seconds"]) > MAX_WINDOW_DURATIONS:
        raise HTTPException(422, f"config.window_seconds 最多 {MAX_WINDOW_DURATIONS} 项")
    if any(duration > MAX_WINDOW_SECONDS for duration in config["window_seconds"]):
        raise HTTPException(422, f"config.window_seconds 不得超过 {MAX_WINDOW_SECONDS:g} 秒")
    if len(config["background_rois"]) > MAX_BACKGROUND_ROIS:
        raise HTTPException(422, f"config.background_rois 最多 {MAX_BACKGROUND_ROIS} 个")
    return config


def _config_schema() -> dict:
    """Discoverable partial-config contract; cross-field rules use W1 validation."""
    positive = {"type": "number", "exclusiveMinimum": 0}
    nonnegative = {"type": "number", "minimum": 0}
    nullable_text = {"type": ["string", "null"]}
    roi = {"type": "array", "items": {"type": "number"}, "minItems": 6,
           "maxItems": 6, "description": "[x_min, x_max, y_min, y_max, z_min, z_max]; each min < max"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "description": "Partial W1 config; omitted fields inherit default_config. Output is normalized and hashed per run.",
        "properties": {
            "schema_version": {"const": W1_SCHEMA},
            "window_seconds": {"type": "array", "items": {**positive, "maximum": MAX_WINDOW_SECONDS},
                               "uniqueItems": True, "maxItems": MAX_WINDOW_DURATIONS},
            "include_single_frame": {"type": "boolean"},
            "install_direction": {"enum": [1, 2]},
            "max_packet_gap_s": positive,
            "time_epsilon_s": positive,
            **{key: {"type": "integer", "minimum": 1, "maximum": ceiling}
               for key, ceiling in WEB_CONFIG_BUDGET.items()},
            "target_roi": {"oneOf": [{"type": "null"}, roi]},
            "background_rois": {"type": "array", "items": roi, "maxItems": MAX_BACKGROUND_ROIS},
            "target_reference": {"oneOf": [{"type": "null"}, {
                "type": "object", "additionalProperties": False,
                "required": ["endpoints_sensor_m", "corridor_radius_m", "position_uncertainty_m",
                             "source", "review_status"],
                "properties": {
                    "endpoints_sensor_m": {"type": "array", "minItems": 2, "maxItems": 2,
                                           "items": {"type": "array", "minItems": 3, "maxItems": 3,
                                                     "items": {"type": "number"}}},
                    "corridor_radius_m": positive,
                    "position_uncertainty_m": nonnegative,
                    "source": {"type": "string", "minLength": 1},
                    "review_status": {"enum": ["reviewed", "unreviewed"]},
                },
            }]},
            "acquisition": {"type": "object", "additionalProperties": False,
                            "properties": {
                                "data_label": {"enum": ["REAL_PCAP_DIAGNOSTIC", "SYNTHETIC"]},
                                "capture_role": {"enum": ["unknown", "target_present", "target_absent"]},
                                "static_status": {"enum": ["unknown", "confirmed", "moving"]},
                                "static_evidence": nullable_text,
                                "target_distance_m": {"oneOf": [{"type": "null"}, positive]},
                                "wire_diameter_mm": {"oneOf": [{"type": "null"}, positive]},
                                **{key: nullable_text for key in
                                   ("wire_material", "wire_orientation", "sensor_settings",
                                    "background_pairing_evidence", "notes")},
                            }},
        },
        "x-cross-field-rules": [
            "time_epsilon_s < max_packet_gap_s",
            "window_seconds nonempty unless include_single_frame is true",
            "ROI bounds ordered, non-overlapping and non-touching",
            "reference endpoints distinct and inside target_roi; corridor includes uncertainty",
            "confirmed static acquisition requires static_evidence",
        ],
        "x-max-config-bytes": MAX_CONFIG_BYTES,
    }


def _uuid(value, name: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, f"{name} 必须是 UUID 字符串")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise HTTPException(422, f"{name} 必须是 UUID 字符串") from error


def _frame(value, count: int, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value < count:
        raise HTTPException(422, f"{name} 必须是现有帧的非负整数索引")
    return value


def _window_rows(run_dir: Path) -> list[dict]:
    path = run_dir / "evidence" / "window_metrics.jsonl"
    with path.open("r", encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _packet_rows(run_dir: Path, window_id: str) -> list[dict]:
    path = run_dir / "evidence" / "packet_metrics.jsonl"
    with path.open("r", encoding="utf-8") as source:
        return [row for line in source if line.strip()
                if (row := json.loads(line)).get("window_id") == window_id]


W3_W4_SCHEMA = "wire-detect-v1"
MAX_W3_W4_POINTS = 400_000
W3_ROI_SCHEMA = {
    "type": "array", "items": {"type": "number"}, "minItems": 6, "maxItems": 6,
    "description": "[x_min, x_max, y_min, y_max, z_min, z_max]; each min < max",
}


def _parse_roi(value, name: str) -> tuple:
    if value is None:
        raise HTTPException(422, f"{name} 需要六个坐标")
    try:
        array = tuple(float(v) for v in value)
    except (TypeError, ValueError) as error:
        raise HTTPException(422, f"{name} 必须是数字数组") from error
    if len(array) != 6 or any(not np.isfinite(v) for v in array):
        raise HTTPException(422, f"{name} 必须是六个有限数字")
    if any(array[i] >= array[i + 1] for i in (0, 2, 4)):
        raise HTTPException(422, f"{name} 各轴 min 必须小于 max")
    return array


def _decode_window_batch(pcap: Path, scan, selected, install_direction: int,
                         capture_sha: str) -> "RayBatch":
    from na70_core import RayBatch  # lazy to keep W1 path dependency-free
    grouped: dict[int, list] = {}
    for packet in selected.packets:
        grouped.setdefault(packet.frame_index, []).append(packet)
    buffers = {key: [] for key in ("codes", "points", "frame_indices", "packet_indices",
                                   "point_indices", "device_times", "capture_times")}
    with pcap.open("rb") as source:
        for frame_index, parts in sorted(grouped.items()):
            frame = scan.frames[frame_index]
            if not frame["complete"]:
                continue
            points = decoder.decode_frame(pcap, frame, install_direction=install_direction, source=source)
            codes = decoder.decode_frame_codes(pcap, frame, source=source)
            ray_ids = np.concatenate([np.arange(p.local_index * 200, (p.local_index + 1) * 200, dtype=np.int64)
                                      for p in parts])
            buffers["codes"].append(codes[ray_ids])
            buffers["points"].append(points[ray_ids])
            buffers["frame_indices"].append(np.full(ray_ids.size, frame_index, dtype=np.int64))
            buffers["packet_indices"].append(np.repeat(np.array([p.index for p in parts], dtype=np.int64), 200))
            buffers["point_indices"].append(ray_ids.astype(np.int64))
            buffers["device_times"].append(np.repeat(
                np.array([p.device_time if p.device_time is not None else np.nan for p in parts]), 200))
            buffers["capture_times"].append(np.repeat(np.array([p.capture_time for p in parts]), 200))
    arrays = {k: np.concatenate(v) if v else np.empty((0, 4) if k in ("codes", "points") else (0,))
              for k, v in buffers.items()}
    return RayBatch.from_decoded(capture_sha256=capture_sha,
                                 coordinate_frame="lidar_link",
                                 install_direction=install_direction,
                                 codes=arrays["codes"], points=arrays["points"],
                                 frame_indices=arrays["frame_indices"],
                                 packet_indices=arrays["packet_indices"],
                                 point_indices=arrays["point_indices"],
                                 device_times=arrays["device_times"],
                                 capture_times=arrays["capture_times"])


class WireDetectionRuns:
    """W3 (manual ROI) and W4 (full-FOV) persistent runs.

    A run owns one causal window over one capture, one detection config and
    one detection mode. All results are written to disk before the run is
    marked ready; restart drops queued/running runs to error but never
    re-executes them.
    """

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wire-w3w4")
        self.runs: dict[str, dict] = {}
        self.router = APIRouter(prefix="/api/wire/detect")
        self._restore()
        self._routes()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=False)

    def _restore(self) -> None:
        for capture_id in tuple(self.store.items):
            parent = self.store.directory(capture_id) / "wire_detect"
            if not parent.is_dir() or parent.is_symlink():
                continue
            for folder in parent.iterdir():
                if not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    run_id = str(UUID(folder.name))
                    if run_id != folder.name:
                        continue
                    request = _read_json(folder / "manifest.json")
                    if (request.get("schema_version") != W3_W4_SCHEMA
                            or request.get("run_id") != run_id
                            or request.get("capture_id") != capture_id):
                        continue
                    state_path = folder / "state.json"
                    state = (_read_json(state_path) if state_path.is_file()
                             else {"status": "error", "error": "任务状态缺失", "updated_at": time.time()})
                    if state.get("status") in ("queued", "running"):
                        state.update(status="error", error=INTERRUPTED, updated_at=time.time())
                        _write_state(state_path, state)
                    self.runs[run_id] = {"folder": folder, "request": request, "state": state}
                except (OSError, ValueError, json.JSONDecodeError):
                    continue

    def _public(self, run: dict) -> dict:
        request, state = run["request"], run["state"]
        return {"run_id": request["run_id"], "capture_id": request["capture_id"],
                "mode": request["mode"], "end_frame": request["end_frame"],
                "duration_s": request["duration_s"], "status": state["status"],
                "error": state.get("error"), "created_at": request["created_at"],
                "updated_at": state["updated_at"]}

    def _set_state(self, run: dict, **changes) -> None:
        with self.lock:
            state = {**run["state"], **changes, "updated_at": time.time()}
            _write_state(run["folder"] / "state.json", state)
            run["state"] = state

    def _execute(self, run_id: str) -> None:
        run = self.runs[run_id]
        request = run["request"]
        from na70_core import RayBatch  # noqa: F401 (module import cost once)
        from na70_core.wire import (FullFovConfig, WindowInputs, load_wire_config,
                                    run_detection_window)
        from na70_core.windows import PacketWindowRing, RingLimits
        from . import thin_wire
        from . import decoder as _decoder
        global decoder
        decoder = _decoder
        try:
            self._set_state(run, status="running")
            pcap = self.store.directory(request["capture_id"]) / "capture.pcap"
            scan = decoder.scan_pcap(pcap)
            cfg_w1 = thin_wire.load_config()
            packets = thin_wire.packet_timeline(scan, cfg_w1)
            ring = PacketWindowRing(RingLimits(
                max_packets_in_ring=max(2000, len(packets) + 64),
                max_window_packets=max(5000, 100 * len(scan.frames)),
                time_epsilon_s=cfg_w1["time_epsilon_s"],
                max_packet_gap_s=cfg_w1["max_packet_gap_s"]))
            for packet in packets:
                ring.push(frame_index=packet.frame_index, local_index=packet.local_index,
                          device_time=packet.device_time, capture_time=packet.capture_time)
            selected = ring.select(request["duration_s"], end_frame_index=request["end_frame"])
            selected_packet_count = selected.packet_count
            if selected_packet_count * 200 > MAX_W3_W4_POINTS:
                raise RuntimeError("窗口点数超出 Web 预算")
            batch = _decode_window_batch(pcap, scan, selected,
                                         request["install_direction"],
                                         request["capture_sha256"])
            wire_cfg = load_wire_config()
            config_sha = _config_hash(json.loads(json.dumps(wire_cfg.to_dict())))
            config_sha = hashlib.sha256(
                json.dumps(wire_cfg.to_dict(), sort_keys=True).encode()).hexdigest()
            enable_full_fov = request["mode"] == "w4_full_fov"
            inputs = WindowInputs(
                batch=batch, selected_window=selected,
                source_window_id=f"web:{request['capture_id']}:{request['end_frame']}:{request['duration_s']}",
                manual_roi_aabb_m=tuple(request["roi_m"]) if request["roi_m"] else None,
                config=wire_cfg,
                window_end_device_time_s=selected.end_device_time,
                availability=("available" if selected.time_quality in ("ok", "partial_window")
                              else "invalid_time"),
                enable_full_fov=enable_full_fov,
                full_fov_config=FullFovConfig() if enable_full_fov else None)
            result = run_detection_window(inputs)
            rows = [dict(row) for row in result.stage_trace]
            evidence_dir = run["folder"] / "evidence"
            evidence_dir.mkdir(exist_ok=True)
            _write_json_once(evidence_dir / "candidate.json", result.to_dict())
            with open(evidence_dir / "stages.jsonl", "x", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            _write_json_once(evidence_dir / "manifest.json", {
                "schema_version": W3_W4_SCHEMA, "status": "COMPLETED",
                "mode": request["mode"], "config_sha256": config_sha,
                "window_packet_count": selected_packet_count,
                "window_quality": selected.time_quality,
                "window_partial": selected.partial_window,
                "wire_return_status": "NOT_EVALUATED",
                "warnings": ["Detection evidence only; identity requires reviewed truth."]})
            self._set_state(run, status="ready",
                            window_status=result.status,
                            first_blocking_stage=result.first_blocking_stage,
                            reject_reason=result.reject_reason)
        except Exception as error:
            try:
                self._set_state(run, status="error", error=f"{type(error).__name__}: {error}")
            except OSError:
                pass

    def _new(self, payload: dict) -> dict:
        allowed = {"capture_id", "mode", "end_frame", "duration_s",
                   "roi_m", "install_direction", "background_pair_available"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise HTTPException(422, "请求包含未知字段")
        capture_id = _uuid(payload.get("capture_id"), "capture_id")
        mode = payload.get("mode")
        if mode not in ("w3_manual_roi", "w4_full_fov"):
            raise HTTPException(422, "mode 必须是 w3_manual_roi 或 w4_full_fov")
        install_direction = payload.get("install_direction", 1)
        if type(install_direction) is not int or install_direction not in (1, 2):
            raise HTTPException(422, "install_direction 必须是 1 或 2")
        duration = payload.get("duration_s")
        if not isinstance(duration, (int, float)) or duration <= 0 or duration > 5.0:
            raise HTTPException(422, "duration_s 必须在 (0, 5] 秒")
        roi = None
        if mode == "w3_manual_roi":
            roi = _parse_roi(payload.get("roi_m"), "roi_m")
        elif payload.get("roi_m") is not None:
            raise HTTPException(422, "w4_full_fov 不接受 roi_m")
        with self.store.lock:
            capture = self.store.get(capture_id)
            if capture["meta"]["status"] != "ready":
                raise HTTPException(409, "主录制尚未就绪")
            end = _frame(payload.get("end_frame"), len(capture["frames"]), "end_frame")
            capture_sha = capture["meta"].get("sha256")
            folder = self.store.directory(capture_id)
            if not (folder / "capture.pcap").is_file():
                raise HTTPException(409, "主录制文件缺失")
            with self.lock:
                if sum(run["state"]["status"] in ("queued", "running")
                       for run in self.runs.values()) >= MAX_ACTIVE_RUNS:
                    raise HTTPException(429, "检测队列已满，请等待运行完成")
                run_id = str(uuid4())
                created_at = time.time()
                parent = folder / "wire_detect"
                if parent.is_symlink():
                    raise HTTPException(409, "检测目录无效")
                parent.mkdir(exist_ok=True)
                run_dir = parent / run_id
                run_dir.mkdir(exist_ok=False)
                request = {
                    "schema_version": W3_W4_SCHEMA, "run_id": run_id, "mode": mode,
                    "capture_id": capture_id, "capture_sha256": capture_sha,
                    "end_frame": end, "duration_s": float(duration),
                    "roi_m": list(roi) if roi else None,
                    "install_direction": install_direction,
                    "created_at": created_at,
                }
                state = {"status": "queued", "error": None, "updated_at": created_at}
                _write_json_once(run_dir / "manifest.json", request)
                _write_json_once(run_dir / "state.json", state)
                run = self.runs[run_id] = {"folder": run_dir, "request": request, "state": state}
                try:
                    self.pool.submit(self._execute, run_id)
                except RuntimeError as error:
                    self._set_state(run, status="error", error=f"任务调度失败: {error}")
                    raise HTTPException(503, "任务调度失败") from error
                return self._public(run)

    def _routes(self) -> None:
        router = self.router

        @router.post("/runs", status_code=202)
        def create(payload: dict):
            return self._new(payload)

        @router.get("/runs")
        def list_runs(capture_id: UUID | None = None, limit: int = Query(50, ge=1, le=500)):
            key = str(capture_id) if capture_id else None
            with self.lock:
                runs = [self._public(run) for run in self.runs.values()
                        if key is None or run["request"]["capture_id"] == key]
            runs.sort(key=lambda item: item["created_at"], reverse=True)
            return runs[:limit]

        @router.get("/runs/{run_id}")
        def detail(run_id: UUID):
            run = self._get(str(run_id))
            result = {**self._public(run), "request": run["request"]}
            if run["state"]["status"] == "ready":
                candidate = _read_json(run["folder"] / "evidence" / "candidate.json")
                manifest = _read_json(run["folder"] / "evidence" / "manifest.json")
                result["candidate"] = candidate
                result["evidence"] = manifest
            return result

        @router.get("/runs/{run_id}/stage-trace")
        def trace(run_id: UUID):
            run = self._get(str(run_id))
            if run["state"]["status"] != "ready":
                raise HTTPException(409, "运行结果尚未就绪")
            stages = (run["folder"] / "evidence" / "stages.jsonl").read_text("utf-8").splitlines()
            return {"run_id": str(run_id), "stage_trace": [json.loads(s) for s in stages if s.strip()]}

    def _get(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                raise HTTPException(404, "检测运行不存在")
            return run


class WireRuns:
    """One worker, one queued slot, immutable requests and recoverable state."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wire-w1")
        self.runs: dict[str, dict] = {}
        self.router = APIRouter(prefix="/api/wire")
        self._restore()
        self._routes()

    def shutdown(self) -> None:
        # W1 has no cancellation hook. Finish active work before a new app can
        # restore these directories and mistake an old writer for interruption.
        self.pool.shutdown(wait=True, cancel_futures=False)

    def has_active_capture(self, capture_id: str) -> bool:
        """Prevent deleting a source PCAP while W1 is reading or writing it."""
        with self.lock:
            return any(run["state"]["status"] in ("queued", "running") and
                       capture_id in (run["request"]["capture_id"],
                                      run["request"]["background_capture_id"])
                       for run in self.runs.values())

    @staticmethod
    def _completed_evidence(run: dict) -> dict:
        manifest = _read_json(run["folder"] / "evidence" / "manifest.json")
        if manifest.get("status") != "COMPLETED" or manifest.get("wire_return_status") != "NOT_EVALUATED":
            raise ValueError("W1 证据清单未完成或状态无效")
        if manifest.get("config_sha256") != run["request"]["config_sha256"]:
            raise ValueError("W1 证据配置与运行请求不一致")
        expected = [("primary", run["request"]["capture_sha256"])]
        if run["request"]["background_capture_id"]:
            expected.append(("background", run["request"]["background_sha256"]))
        inputs = [(row.get("role"), row.get("sha256")) for row in manifest.get("inputs", [])]
        if inputs != expected:
            raise ValueError("W1 证据来源与运行请求不一致")
        return manifest

    def _restore(self) -> None:
        for capture_id in tuple(self.store.items):
            parent = self.store.directory(capture_id) / "wire"
            if not parent.is_dir() or parent.is_symlink():
                continue
            for folder in parent.iterdir():
                if not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    run_id = str(UUID(folder.name))
                    if run_id != folder.name or (folder / "manifest.json").is_symlink():
                        continue
                    request = _read_json(folder / "manifest.json")
                    if (request.get("schema_version") != SCHEMA or
                            request.get("run_id") != run_id or
                            request.get("capture_id") != capture_id):
                        continue
                    if _config_hash(request["config"]) != request.get("config_sha256"):
                        continue
                    state_path = folder / "state.json"
                    state = _read_json(state_path) if state_path.is_file() and not state_path.is_symlink() else {
                        "status": "error", "window_count": 0,
                        "error": "任务状态文件缺失", "updated_at": time.time(),
                    }
                    if state.get("status") in ("queued", "running"):
                        state.update(status="error", error=INTERRUPTED, updated_at=time.time())
                        _write_state(state_path, state)
                    run = {"folder": folder, "request": request, "state": state}
                    if state.get("status") == "ready":
                        try:
                            evidence = self._completed_evidence(run)
                            if len(_window_rows(folder)) != evidence.get("window_count"):
                                raise ValueError("W1 窗口指标数量与清单不一致")
                        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                            state.update(status="error", error=f"既有证据损坏: {error}", updated_at=time.time())
                            _write_state(state_path, state)
                    elif state.get("status") != "error":
                        state.update(status="error", error="任务状态无效", updated_at=time.time())
                        _write_state(state_path, state)
                    self.runs[run_id] = run
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    # A partial or tampered directory is not a valid run.
                    continue

    def _get(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                raise HTTPException(404, "导线运行不存在")
            return run

    def _public(self, run: dict) -> dict:
        request, state = run["request"], run["state"]
        return {
            "run_id": request["run_id"], "capture_id": request["capture_id"],
            "background_capture_id": request["background_capture_id"],
            "end_frame": request["end_frame"],
            "background_end_frame": request["background_end_frame"],
            "config_sha256": request["config_sha256"],
            "status": state["status"], "wire_return_status": "NOT_EVALUATED",
            "window_count": state.get("window_count", 0),
            "error": state.get("error"), "created_at": request["created_at"],
            "updated_at": state["updated_at"],
        }

    def _set_state(self, run: dict, **changes) -> None:
        with self.lock:
            state = {**run["state"], **changes, "updated_at": time.time()}
            _write_state(run["folder"] / "state.json", state)
            run["state"] = state

    def _execute(self, run_id: str) -> None:
        run = self.runs[run_id]
        request = run["request"]
        try:
            self._set_state(run, status="running")
            primary = self.store.directory(request["capture_id"]) / "capture.pcap"
            background = (self.store.directory(request["background_capture_id"]) / "capture.pcap"
                          if request["background_capture_id"] else None)
            result = run_audit(
                primary, run["folder"] / "evidence", request["config"],
                end_frames=[request["end_frame"]] if request["end_frame"] is not None else None,
                background_pcap=background,
                background_end_frames=([request["background_end_frame"]]
                                       if request["background_end_frame"] is not None else None),
                command=["webui", "W1_RAW_DIAGNOSTIC", run_id], render=request["render"],
            )
            if result.get("status") != "COMPLETED" or result.get("wire_return_status") != "NOT_EVALUATED":
                raise RuntimeError("W1 审计未生成预期的诊断结果")
            self._completed_evidence(run)
            self._set_state(run, status="ready", window_count=result["window_count"], error=None)
        except Exception as error:
            # Keep the W1 failure manifest and every partial artifact for review.
            try:
                self._set_state(run, status="error", error=f"{type(error).__name__}: {error}")
            except OSError:
                # A capture deleted through the existing capture API has no
                # persistent run directory left to update.
                pass

    def _new(self, payload: dict) -> dict:
        allowed = {"capture_id", "background_capture_id", "end_frame", "background_end_frame", "render", "config"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise HTTPException(422, "请求包含未知字段")
        capture_id = _uuid(payload.get("capture_id"), "capture_id")
        background_id = (None if payload.get("background_capture_id") is None else
                         _uuid(payload["background_capture_id"], "background_capture_id"))
        if background_id == capture_id:
            raise HTTPException(422, "背景必须是独立录制")
        render = payload.get("render", True)
        if type(render) is not bool:
            raise HTTPException(422, "render 必须是布尔值")
        config = _web_config(payload["config"] if "config" in payload else {})
        with self.store.lock:
            capture = self.store.get(capture_id)
            if capture["meta"]["status"] != "ready":
                raise HTTPException(409, "主录制尚未就绪")
            background = self.store.get(background_id) if background_id else None
            if background is not None and background["meta"]["status"] != "ready":
                raise HTTPException(409, "背景录制尚未就绪")
            if background is None and payload.get("background_end_frame") is not None:
                raise HTTPException(422, "background_end_frame 需要 background_capture_id")
            end = _frame(payload.get("end_frame"), len(capture["frames"]), "end_frame")
            bg_end = (_frame(payload.get("background_end_frame"), len(background["frames"]),
                             "background_end_frame") if background else None)
            if background and capture["meta"].get("sha256") == background["meta"].get("sha256"):
                raise HTTPException(422, "背景内容与主录制相同")
            # Keep only immutable scalar capture identity outside Store's lock.
            capture_sha = capture["meta"].get("sha256")
            background_sha = background["meta"].get("sha256") if background else None
            folder = self.store.directory(capture_id)
            if not (folder / "capture.pcap").is_file():
                raise HTTPException(409, "主录制文件缺失")
            if background and not (self.store.directory(background_id) / "capture.pcap").is_file():
                raise HTTPException(409, "背景录制文件缺失")
            # Hold Store's lock through registration. Capture deletion takes
            # Store -> Wire locks in the same order and cannot race this step.
            with self.lock:
                if sum(run["state"]["status"] in ("queued", "running")
                       for run in self.runs.values()) >= MAX_ACTIVE_RUNS:
                    raise HTTPException(429, "导线审计队列已满，请等待运行完成")
                created_at = time.time()
                run_id = str(uuid4())
                parent = folder / "wire"
                if parent.is_symlink():
                    raise HTTPException(409, "导线数据目录无效")
                parent.mkdir(exist_ok=True)
                run_dir = parent / run_id
                run_dir.mkdir(exist_ok=False)
                request = {
                    "schema_version": SCHEMA, "run_id": run_id,
                    "mode": "W1_RAW_DIAGNOSTIC", "created_at": created_at,
                    "capture_id": capture_id, "capture_sha256": capture_sha,
                    "background_capture_id": background_id, "background_sha256": background_sha,
                    "end_frame": end, "background_end_frame": bg_end, "render": render,
                    "config": config,
                    "config_sha256": _config_hash(config),
                }
                state = {"status": "queued", "window_count": 0, "error": None, "updated_at": created_at}
                _write_json_once(run_dir / "manifest.json", request)
                _write_json_once(run_dir / "state.json", state)
                run = self.runs[run_id] = {"folder": run_dir, "request": request, "state": state}
                try:
                    self.pool.submit(self._execute, run_id)
                except RuntimeError as error:
                    self._set_state(run, status="error", error=f"任务调度失败: {error}")
                    raise HTTPException(503, "任务调度失败") from error
                return self._public(run)

    def _ready(self, run_id: str) -> dict:
        run = self._get(run_id)
        if run["state"]["status"] != "ready":
            raise HTTPException(409, "运行结果尚未就绪")
        return run

    def _window(self, run_id: str, window_id: str) -> tuple[dict, dict]:
        run = self._ready(run_id)
        # A window ID is accepted only when present in the finished W1 manifest.
        manifest = self._completed_evidence(run)
        if window_id not in {row["window_id"] for row in manifest.get("windows", [])}:
            raise HTTPException(404, "窗口不存在")
        for row in _window_rows(run["folder"]):
            if row["window_id"] == window_id:
                return run, row
        raise HTTPException(404, "窗口指标文件缺失")

    def _routes(self) -> None:
        router = self.router

        @router.get("/capabilities")
        def capabilities():
            config = load_config()
            return {
                "schema_version": SCHEMA,
                "modes": [{"id": "w1_raw", "label": "W1 原始回波诊断",
                           "available": True, "evaluation_status": "NOT_EVALUATED"}],
                "future_modes": [{"id": mode, "available": False} for mode in
                                 ("w3_roi", "w4_full_fov", "w5_motion")],
                "max_active_runs": MAX_ACTIVE_RUNS,
                "default_window_seconds": config["window_seconds"],
                "default_config": config,
                "config_schema": _config_schema(),
            }

        @router.post("/runs", status_code=202)
        def create_run(payload: dict):
            return self._new(payload)

        @router.get("/runs")
        def list_runs(capture_id: UUID | None = None, limit: int = Query(50, ge=1, le=500)):
            key = str(capture_id) if capture_id else None
            with self.lock:
                runs = [self._public(run) for run in self.runs.values()
                        if key is None or run["request"]["capture_id"] == key]
            runs.sort(key=lambda item: item["created_at"], reverse=True)
            return runs[:limit]

        @router.get("/runs/{run_id}")
        def get_run(run_id: UUID):
            run = self._get(str(run_id))
            result = {**self._public(run), "request": run["request"],
                      "windows": [], "evidence_gaps": []}
            if run["state"]["status"] == "ready":
                w1 = self._completed_evidence(run)
                result["evidence_gaps"] = w1.get("evidence_gaps", [])
                result["windows"] = [{key: row.get(key) for key in (
                    "window_id", "input_role", "requested_duration_s", "actual_span_s",
                    "time_quality", "ray_count", "valid_return_count", "zero_distance_count",
                    "target_roi", "wire_return_status",
                )} for row in _window_rows(run["folder"])]
            return result

        @router.get("/runs/{run_id}/windows/{window_id}")
        def get_window(run_id: UUID, window_id: str):
            run, row = self._window(str(run_id), window_id)
            return {"run_id": str(run_id), "window": row,
                    "packets": _packet_rows(run["folder"], window_id)}

        @router.get("/runs/{run_id}/windows/{window_id}/cloud")
        def get_cloud(run_id: UUID, window_id: str,
                      limit: int = Query(4000, ge=1, le=MAX_CLOUD_POINTS),
                      subset: str = Query("all", pattern="^(all|target_roi|background_roi|reference_corridor)$")):
            run, row = self._window(str(run_id), window_id)
            expected = f"raw_windows/{window_id}.npz"
            if row.get("raw_file") != expected:
                raise HTTPException(409, "原始窗口路径与清单不一致")
            path = run["folder"] / "evidence" / expected
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(run["folder"].resolve()):
                raise HTTPException(404, "原始窗口文件缺失")
            if sha256_file(path) != row.get("raw_sha256"):
                raise HTTPException(409, "原始窗口哈希与记录不一致")
            try:
                with np.load(path, allow_pickle=False) as raw:
                    valid_mask = raw["valid_mask"]
                    total_valid = int(valid_mask.sum())
                    mask = valid_mask if subset == "all" else valid_mask & raw[f"{subset}_mask"]
                    indices = np.flatnonzero(mask)
                    total_selected = len(indices)
                    if total_selected > limit:
                        indices = indices[np.linspace(0, total_selected - 1, num=limit, dtype=np.int64)]
                    xyz_i = raw["points"][indices]
                    frames = raw["frame_indices"][indices]
                    packets = raw["packet_indices"][indices]
                    point_indices = raw["point_indices"][indices]
                    times = raw["device_times"][indices]
                    targets = raw["target_roi_mask"][indices]
                    backgrounds = raw["background_roi_mask"][indices]
                    corridors = raw["reference_corridor_mask"][indices]
            except (OSError, ValueError, KeyError) as error:
                raise HTTPException(409, f"原始窗口无法读取: {error}") from error
            points = [{"x": float(point[0]), "y": float(point[1]),
                       "z": float(point[2]), "intensity": float(point[3]),
                       "frame_index": int(frames[i]), "packet_index": int(packets[i]),
                       "point_index": int(point_indices[i]),
                       "device_time": float(times[i]) if np.isfinite(times[i]) else None,
                       "target_roi": bool(targets[i]), "background_roi": bool(backgrounds[i]),
                       "reference_corridor": bool(corridors[i])}
                      for i, point in enumerate(xyz_i) if np.isfinite(point).all()]
            return {"run_id": str(run_id), "window_id": window_id,
                    "capture_id": (run["request"]["capture_id"] if row["input_role"] == "primary"
                                   else run["request"]["background_capture_id"]),
                    "input_role": row["input_role"], "coordinate_frame": "lidar_link",
                    "subset": subset, "total_valid_points": total_valid,
                    "total_selected_points": total_selected,
                    "sampled": total_selected > limit, "points": points}
