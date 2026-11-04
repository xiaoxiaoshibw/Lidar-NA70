"""Persistent mapping jobs: lifecycle, progress, cancellation and on-disk results.

A mapping job is the only supported way to run :mod:`backend.mapping`.  The
legacy ``/api/captures/{id}/registration`` endpoints are thin shims over this
module, so there is exactly one implementation and one result layout.

Layout, under the import record directory::

    mapping/
      index.json                     job list and latest job id
      <job_id>/
        job.json                     the job record (status, counters, bounds)
        config.json                  exact thresholds used
        trajectory.json / .csv       per-frame pose and state
        frame_quality.jsonl          one JSON record per frame
        keyframes.json               keyframe poses and quality
        map_metadata.json            voxel size, counts, bounds, chunk index
        map_chunks/<cx>_<cy>_<cz>.jsonl
        map.ply / map.pcd            fused voxel map
        validation.json              metrics, versions, environment, claim block
        export/mapping-evidence.zip  self-contained evidence bundle

Every write is atomic (``*.tmp`` + ``replace``) so a crash never leaves a
half-written result that the next start would read back.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import numpy as np

from .decoder import DECODER_VERSION, decode_frame
from .capture_validation import summarize
from .mapping import (
    FIXED_SCENE_STAGE, FIXED_SCENE_VERSION, ODOMETRY_VERSION, STAGE,
    configuration, configuration_version, run_fixed_scene, run_odometry,
    validate_configuration,
)

JOBS_DIRECTORY = "mapping"
INDEX_NAME = "index.json"
#: A job that was running when the process died cannot be resumed, and its
#: partial files are not a valid result.
INTERRUPTED_STATES = ("queued", "running", "cancelling")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_json(path: Path, payload) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, allow_nan=False))


def peak_memory_mb():
    """Process peak resident memory in MiB, or None when unavailable.

    ``tracemalloc`` was the obvious choice but it hooks every allocation and
    roughly doubles the wall time of a numpy-heavy odometry run, which is a bad
    trade for one number.  The OS counters are free and measure what actually
    matters on a 4 GB Jetson.  The argument types must be declared explicitly:
    ``GetCurrentProcess`` returns a 64-bit pseudo-handle that is silently
    truncated to an invalid 32-bit value otherwise.
    """
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            for library, name in (("psapi", "GetProcessMemoryInfo"),
                                  ("kernel32", "K32GetProcessMemoryInfo")):
                try:
                    candidate = getattr(ctypes.WinDLL(library, use_last_error=True), name)
                except (OSError, AttributeError):
                    continue
                candidate.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
                candidate.restype = wintypes.BOOL
                counters = Counters()
                counters.cb = ctypes.sizeof(Counters)
                if candidate(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                    value = counters.PeakWorkingSetSize / 1024 / 1024
                    # A running Python service cannot peak below 1 MiB.  A reading
                    # that small means the counter did not populate, and publishing
                    # it would be a wrong number rather than an absent one.
                    return round(value, 2) if value >= 1.0 else None
        else:
            with open("/proc/self/status", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmHWM:"):
                        return round(int(line.split()[1]) / 1024, 2)
    except Exception:  # noqa: BLE001 - the metric is optional, never fatal
        return None
    return None


def empty_job(job_id: str, capture_id: str, input_sha256, cfg: dict, frame_count: int) -> dict:
    """The job record.  The field names are part of the API contract."""
    fixed = cfg["pose_source"] == "fixed_sensor"
    return dict(
        job_id=job_id,
        capture_id=capture_id,
        input_sha256=input_sha256,
        code_version=f"{FIXED_SCENE_VERSION if fixed else ODOMETRY_VERSION}+{DECODER_VERSION}",
        config_version=configuration_version(cfg),
        status="queued",
        progress=0.0,
        created_at=time.time(),
        started_at=None,
        finished_at=None,
        frame_count=frame_count,
        accepted_frames=0,
        rejected_frames=0,
        tracking_lost_frames=0,
        keyframe_count=0,
        map_point_count=0,
        map_bounds=None,
        first_frame_index=cfg["stationary_start_frame"] if fixed else 0,
        last_frame_index=cfg["stationary_end_frame"] if fixed else max(0, frame_count - 1),
        imu_used=False,
        loop_closure_used=False,
        stage=FIXED_SCENE_STAGE if fixed else STAGE,
        pose_source=cfg["pose_source"],
        fixed_sensor_declaration=cfg["stationary_declaration"] if fixed else None,
        error=None,
        elapsed_seconds=None,
        peak_memory_mb=None,
        artifacts={},
        metrics={},
    )


class MappingJobs:
    """Job registry for one import record."""

    def __init__(self, folder: Path, capture_id: str, frames, input_sha256,
                 pool, lock, logger=None):
        self.folder = Path(folder)
        self.capture_id = capture_id
        # ``frames`` may be a callable: the owner reassigns its frame index when
        # indexing finishes, and a stored reference would go stale.
        self.frames_source = frames
        self.input_sha256 = input_sha256
        self.pool = pool
        self.lock = lock
        self.logger = logger
        self.jobs: dict = {}
        self.controls: dict = {}
        self.reload()

    @property
    def frames(self) -> list:
        return self.frames_source() if callable(self.frames_source) else self.frames_source

    # ------------------------------------------------------------------ paths

    @property
    def directory(self) -> Path:
        return self.folder / JOBS_DIRECTORY

    def job_directory(self, job_id: str) -> Path:
        return self.directory / job_id

    def _write_index(self) -> None:
        entries = [dict(job_id=job["job_id"], status=job["status"], created_at=job["created_at"],
                        progress=job["progress"], input_sha256=job["input_sha256"])
                   for job in self.jobs.values()]
        entries.sort(key=lambda entry: entry["created_at"], reverse=True)
        _atomic_write_json(self.directory / INDEX_NAME,
                           dict(jobs=entries, latest=entries[0]["job_id"] if entries else None))

    def _save(self, job: dict) -> None:
        _atomic_write_json(self.job_directory(job["job_id"]) / "job.json", job)
        self._write_index()

    # ----------------------------------------------------------------- restore

    def reload(self) -> None:
        """Rebuild the registry from disk; a crashed run becomes an explicit error."""
        self.jobs = {}
        self.controls = {}
        index_path = self.directory / INDEX_NAME
        candidates = []
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text("utf-8"))
                candidates = [entry["job_id"] for entry in index.get("jobs", [])]
            except (ValueError, KeyError, OSError):
                candidates = []
        if not candidates and self.directory.is_dir():
            candidates = [path.name for path in self.directory.iterdir() if path.is_dir()]
        for job_id in candidates:
            path = self.job_directory(job_id) / "job.json"
            if not path.exists():
                continue
            try:
                job = json.loads(path.read_text("utf-8"))
            except (ValueError, OSError):
                continue
            if job.get("capture_id") != self.capture_id or job.get("job_id") != job_id:
                continue
            job.setdefault("capture_preflight", dict(status="未预检", metadata=None, real_movement_acceptance="NOT_EVALUATED"))
            if job.get("status") in INTERRUPTED_STATES:
                job.update(status="error", error="上次建图任务被中断，请重新运行",
                           finished_at=time.time())
                _atomic_write_json(path, job)
            self.jobs[job_id] = job
            self.controls[job_id] = dict(cancel=threading.Event())
        if candidates:
            self._write_index()

    # ------------------------------------------------------------------- CRUD

    def latest(self):
        if not self.jobs:
            return None
        return max(self.jobs.values(), key=lambda job: job["created_at"])

    def get(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list(self) -> list:
        return sorted(self.jobs.values(), key=lambda job: job["created_at"], reverse=True)

    def create(self, cfg=None, preflight=None) -> dict:
        if not self.frames:
            raise ValueError("记录尚未就绪，无法开始建图")
        cfg = validate_configuration(cfg or configuration())
        if (cfg['initialization_mode'] == 'stationary_window' or cfg['pose_source'] == 'fixed_sensor') \
                and cfg['stationary_end_frame'] >= len(self.frames):
            raise ValueError("静止初始化区间超出采集帧数")
        job = empty_job(str(uuid4()), self.capture_id, self.input_sha256, cfg, len(self.frames))
        job["capture_preflight"] = preflight or dict(status="未预检", metadata=None, real_movement_acceptance="NOT_EVALUATED")
        self.jobs[job["job_id"]] = job
        self.controls[job["job_id"]] = dict(cancel=threading.Event())
        self.job_directory(job["job_id"]).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.job_directory(job["job_id"]) / "config.json", cfg)
        self._save(job)
        self.pool.submit(self._run, job["job_id"], cfg)
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        control = self.controls.get(job_id)
        if control:
            control["cancel"].set()
        if job["status"] in ("queued", "running"):
            job["status"] = "cancelling"
            self._save(job)
        return job

    def forget(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
        control = self.controls.pop(job_id, None)
        if control:
            control["cancel"].set()

    # -------------------------------------------------------------------- run

    def _cancelled(self, job_id: str) -> bool:
        control = self.controls.get(job_id)
        return (control is None) or control["cancel"].is_set()

    def _run(self, job_id: str, cfg: dict) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        directory = self.job_directory(job_id)
        started = time.perf_counter()
        last_tick = 0.0

        def progress(done, total):
            nonlocal last_tick
            now = time.monotonic()
            if now - last_tick < 0.25 and done < total:
                return
            last_tick = now
            with self.lock:
                job["progress"] = round(100.0 * done / max(1, total), 2)
                self._save(job)

        with self.lock:
            job.update(status="running", started_at=time.time(), progress=0.0, error=None)
            self._save(job)

        try:
            loader = lambda index: decode_frame(self.folder / "capture.pcap", self.frames[index])
            common = dict(cfg=cfg, cancelled=lambda: self._cancelled(job_id), progress=progress)
            report = (run_fixed_scene(loader, self.frames, **common)
                      if cfg["pose_source"] == "fixed_sensor" else
                      run_odometry(loader, [frame["time"] for frame in self.frames], **common))
            if self._cancelled(job_id):
                raise InterruptedError()
            artifacts = self._persist(job, directory, cfg, report)
            peak = peak_memory_mb()
            elapsed = round(time.perf_counter() - started, 3)
            with self.lock:
                if job_id not in self.jobs:
                    return
                if self._cancelled(job_id):
                    raise InterruptedError()
                metrics = report["metrics"]
                job.update(
                    status="ready", progress=100.0, finished_at=time.time(),
                    accepted_frames=metrics["accepted_frames"],
                    rejected_frames=metrics["rejected_frames"],
                    tracking_lost_frames=metrics["tracking_lost_frames"],
                    keyframe_count=metrics["keyframe_count"],
                    map_point_count=metrics["map_point_count"],
                    map_bounds=metrics["map_bounds"],
                    metrics=metrics,
                    elapsed_seconds=elapsed,
                    peak_memory_mb=peak,
                    artifacts=artifacts,
                    error=None,
                )
                self._write_validation(directory, job, cfg, report, elapsed, peak)
                self._save(job)
        except InterruptedError:
            with self.lock:
                if job_id in self.jobs:
                    job.update(status="cancelled", finished_at=time.time(), error=None)
                    self._save(job)
                self._write_validation(directory, job, cfg, None,
                                       round(time.perf_counter() - started, 3), 0, cancelled=True)
        except Exception as exc:  # noqa: BLE001 - the record must carry the failure
            with self.lock:
                if job_id in self.jobs:
                    job.update(status="error", finished_at=time.time(), error=str(exc))
                    self._save(job)
                self._write_validation(directory, job, cfg, None,
                                       round(time.perf_counter() - started, 3), 0, error=str(exc))

    # ---------------------------------------------------------------- writers

    def _persist(self, job: dict, directory: Path, cfg: dict, report: dict) -> dict:
        voxel_map = report["map"]
        metrics = report["metrics"]
        trajectory = report["trajectory"]

        _atomic_write_json(directory / "trajectory.json", trajectory)
        self._write_trajectory_csv(directory / "trajectory.csv", report)
        self._write_frame_quality(directory / "frame_quality.jsonl", report["frames"])
        _atomic_write_json(directory / "keyframes.json",
                           dict(version=report["version"], keyframes=report["keyframes"]))

        chunk_count, chunk_index = self._write_chunks(directory, voxel_map)
        metrics["chunk_count"] = chunk_count
        map_meta = voxel_map.metadata(
            schema_version=2, map_id=0, coordinate_frame="map",
            segment_semantics="most_recent_observation_source; all segments constrained to map 0",
            imu_used=False, loop_closure_used=False, stage=report["stage"],
            pose_source=cfg["pose_source"],
            map_origin="capture_lidar_link" if cfg["pose_source"] == "fixed_sensor" else "odometry_reference",
            source_id_semantics="last_frame_index" if cfg["pose_source"] == "fixed_sensor" else "last_keyframe_id",
            input_sha256=self.input_sha256,
            config_version=configuration_version(cfg),
            chunk_index="map_chunks/index.json",
            chunks=chunk_index,
        )
        _atomic_write_json(directory / "map_metadata.json", map_meta)
        for name, exporter in (("map.ply", voxel_map.export_ply), ("map.pcd", voxel_map.export_pcd)):
            temporary = directory / (name + ".tmp")
            exporter(temporary)
            temporary.replace(directory / name)
        return dict(
            job="job.json", config="config.json", trajectory="trajectory.json",
            trajectory_csv="trajectory.csv", frame_quality="frame_quality.jsonl",
            keyframes="keyframes.json", map_metadata="map_metadata.json",
            map_ply="map.ply", map_pcd="map.pcd", validation="validation.json",
            chunks="map_chunks/index.json",
        )

    def _write_trajectory_csv(self, path: Path, report: dict) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["frame_index", "timestamp_s", "state", "segment_id", "keyframe",
                             "x", "y", "z"] + [f"r{row}{column}" for row in range(3) for column in range(4)])
            for record in report["frames"]:
                pose = record["pose"]
                writer.writerow(
                    [record["index"], f'{record["timestamp"]:.6f}' if record["timestamp"] is not None else "", record["state"],
                     record["segment_id"], int(record["keyframe"])]
                    + ([f"{v:.6f}" for v in (pose[0][3], pose[1][3], pose[2][3])] if pose else ["", "", ""])
                    + ([f"{v:.6f}" for row in pose for v in row] if pose else [""] * 12)
                )
        temporary.replace(path)

    def _write_frame_quality(self, path: Path, records: list) -> None:
        lines = [json.dumps(record, ensure_ascii=False, allow_nan=False) for record in records]
        _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))

    def _write_chunks(self, directory: Path, voxel_map):
        chunk_directory = directory / "map_chunks"
        if chunk_directory.exists():
            shutil.rmtree(chunk_directory)
        chunk_directory.mkdir(parents=True, exist_ok=True)
        grouped = voxel_map.chunked()
        index = []
        size = voxel_map.voxel
        chunk_size = size * voxel_map.chunk_voxels
        for key, rows in sorted(grouped.items()):
            name = f"{key[0]}_{key[1]}_{key[2]}.jsonl"
            lines = [json.dumps(row, ensure_ascii=False) for row in rows]
            _atomic_write_text(chunk_directory / name, "\n".join(lines) + "\n")
            index.append(dict(
                id=name[:-6], key=list(key), file=name, voxel_count=len(rows),
                minimum=[round(key[axis] * chunk_size, 4) for axis in range(3)],
                maximum=[round((key[axis] + 1) * chunk_size, 4) for axis in range(3)],
            ))
        _atomic_write_json(chunk_directory / "index.json", dict(
            version="na70-voxel-map-v1", voxel_size=size, chunk_voxels=voxel_map.chunk_voxels,
            fields=["x", "y", "z", "intensity", "observations", "last_time", "keyframe_id", "segment_id"],
            chunk_count=len(index), chunks=index,
        ))
        return len(index), index

    def _write_validation(self, directory: Path, job: dict, cfg: dict, report, elapsed: float,
                          peak_bytes: int, error=None, cancelled=False) -> None:
        metrics = dict(report["metrics"]) if report else {}
        map_meta = report["map"].metadata() if report else {}
        claims = dict(
            engineering_chain="FAILED" if error else ("CANCELLED" if cancelled else "PASSED"),
            synthetic_algorithm=("see web/tests/test_mapping_api.py (SYNTHETIC)"
                                 if cfg["pose_source"] == "fixed_sensor" else
                                 "see web/tests/test_mapping.py (SYNTHETIC)"),
            real_capture_ghosting_acceptance="NOT_EVALUATED",
            jetson_acceptance="NOT_RUN",
            initialization_assumption=(cfg.get("stationary_declaration") or None)
            if cfg["pose_source"] == "odometry" else None,
            fixed_sensor_declaration=(cfg.get("stationary_declaration") or None)
            if cfg["pose_source"] == "fixed_sensor" else None,
        )
        payload = dict(
            job_id=job["job_id"],
            capture_id=self.capture_id,
            input_sha256=self.input_sha256,
            code_version=job["code_version"],
            config_version=configuration_version(cfg),
            config=cfg,
            stage=job["stage"],
            pose_source=cfg["pose_source"],
            imu_used=False,
            loop_closure_used=False,
            status=job["status"],
            error=error,
            metrics=metrics,
            map=map_meta,
            elapsed_seconds=elapsed,
            peak_memory_mb=peak_bytes if peak_bytes else None,
            memory_measurement="peak resident set size of the whole service process "
                                "(Windows PeakWorkingSetSize / Linux VmHWM), not just the job",
            environment=dict(
                platform=platform.platform(),
                python=sys.version.split()[0],
                numpy=np.__version__,
                cpu_count=os.cpu_count(),
            ),
            data_source=dict(kind="pcap", decoder=DECODER_VERSION,
                             frame_count=job["frame_count"],
                             first_frame_index=job["first_frame_index"],
                             last_frame_index=job["last_frame_index"]),
            capture_preflight=job.get("capture_preflight"),
            capture_validation=(dict(status="NOT_APPLICABLE", reason="fixed_sensor_scene_has_no_odometry")
                                if cfg["pose_source"] == "fixed_sensor" else
                                summarize(report["frames"] if report else [], job.get("capture_preflight"))),
            claims=claims,
        )
        _atomic_write_json(directory / "validation.json", payload)

    # ----------------------------------------------------------------- export

    def export(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] != "ready":
            raise ValueError("任务尚未完成，无法导出")
        directory = self.job_directory(job_id)
        export_directory = directory / "export"
        export_directory.mkdir(parents=True, exist_ok=True)
        bundle = export_directory / "mapping-evidence.zip"
        members = ["job.json", "config.json", "trajectory.json", "trajectory.csv",
                   "frame_quality.jsonl", "keyframes.json", "map_metadata.json",
                   "map.ply", "map.pcd", "validation.json", "map_chunks/index.json"]
        chunk_files = sorted(path.name for path in (directory / "map_chunks").glob("*.jsonl"))
        temporary = bundle.with_name(bundle.name + ".tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in members:
                path = directory / name
                if path.exists():
                    archive.write(path, name)
            for name in chunk_files:
                archive.write(directory / "map_chunks" / name, f"map_chunks/{name}")
        temporary.replace(bundle)
        manifest = dict(
            job_id=job_id,
            created_at=time.time(),
            bundle=bundle.name,
            bundle_bytes=bundle.stat().st_size,
            members=members + [f"map_chunks/{name}" for name in chunk_files],
            member_sha256={name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                           for name in members + [f"map_chunks/{name}" for name in chunk_files]},
            manifest_in_bundle=False,
            input_sha256=self.input_sha256,
            config_version=job["config_version"],
            code_version=job["code_version"],
            note="证据包只包含本机离线处理结果；真实无重影验收与 Jetson 性能验收不在此包内。",
        )
        _atomic_write_json(export_directory / "manifest.json", manifest)
        return manifest
