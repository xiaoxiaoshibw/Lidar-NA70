import asyncio
import csv
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
from uuid import UUID
import numpy as np

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from .decoder import DECODER_VERSION, decode_frame, public_frame, scan_pcap
from .mapping import configuration, transform
from .mapping_jobs import MappingJobs
from .turntable_jobs import TurntableJobs
from .preflight import diagnostics, evaluate, metadata as capture_metadata
from .motion import ANALYSIS_VERSION, MotionConfig, analyze_capture

ROOT = Path(__file__).resolve().parents[1]


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.items = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pcap-work")
        # Mapping is a long, CPU-bound job.  It gets its own single worker so a
        # multi-minute odometry run can never starve upload indexing or analysis,
        # and two mapping runs on the same machine never fight for the same cores.
        self.mapping_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mapping")
        self.mapping: dict[str, MappingJobs] = {}
        self.turntable = TurntableJobs(self.root, self.mapping_pool, self.lock)
        # A decoded frame body is immutable once its capture is indexed, so a small
        # per-capture LRU turns scrubbing and replay over already-seen frames into a
        # cache hit instead of another decode. 64 frames ~ 20 MB.
        self.frame_cache: dict[str, OrderedDict] = {}
        self.frame_cache_limit = 64
        for file in self.root.glob("*/meta.json"):
            try:
                meta = json.loads(file.read_text("utf-8"))
                if str(UUID(file.parent.name)) != meta["id"]:
                    continue
                if meta["status"] in ("uploading", "indexing"):
                    meta.update(status="error", error="上次导入被中断，请移除后重新导入")
                meta.setdefault("analysis_status", "not_analyzed")
                meta.setdefault("analysis_progress", 0)
                meta.setdefault("analysis_error", None)
                if meta["analysis_status"] in ("queued", "analyzing"):
                    meta.update(analysis_status="error", analysis_error="上次分析被中断，请重新分析")
                frames_path = file.parent / "frames.json"
                frames = json.loads(frames_path.read_text("utf-8")) if meta["status"] == "ready" else []
                analysis_path = file.parent / "analysis.json"
                analysis = json.loads(analysis_path.read_text("utf-8")) if meta["analysis_status"] == "ready" and analysis_path.exists() else None
                if analysis is not None and analysis.get("version") != ANALYSIS_VERSION:
                    # A cached result from an earlier method is not comparable and
                    # has no band/target fields; ask for a fresh run instead.
                    analysis = None
                    meta.update(analysis_status="not_analyzed", analysis_progress=0, analysis_error=None)
                item = dict(meta=meta, frames=frames, analysis=analysis,
                            cancel=threading.Event(), analysis_cancel=threading.Event())
                self.items[meta["id"]] = item
                # Completed mapping jobs are restored here, so the WebUI can still
                # inspect them after a restart without recomputing anything.
                self.mapping[meta["id"]] = MappingJobs(
                    file.parent, meta["id"], (lambda target=item: target["frames"]),
                    meta.get("sha256"), self.mapping_pool, self.lock)
            except (ValueError, KeyError, OSError):
                continue

    def mapping_jobs(self, identifier):
        with self.lock:
            jobs = self.mapping.get(identifier)
            if jobs is None:
                item = self.get(identifier)
                jobs = self.mapping[identifier] = MappingJobs(
                    self.directory(identifier), identifier, (lambda target=item: target["frames"]),
                    item["meta"].get("sha256"), self.mapping_pool, self.lock)
            return jobs

    def directory(self, identifier):
        return self.root / str(UUID(identifier))

    def get(self, identifier):
        if identifier not in self.items:
            raise HTTPException(404, "记录不存在或已移除")
        return self.items[identifier]

    def save(self, item):
        folder = self.directory(item["meta"]["id"])
        temp = folder / "meta.tmp"
        temp.write_text(json.dumps(item["meta"], ensure_ascii=False), "utf-8")
        temp.replace(folder / "meta.json")

    def cached_frame(self, identifier, index):
        cache = self.frame_cache.get(identifier)
        if cache is None:
            return None
        body = cache.get(index)
        if body is not None:
            cache.move_to_end(index)
        return body

    def cache_frame(self, identifier, index, body):
        cache = self.frame_cache.get(identifier)
        if cache is None:
            cache = self.frame_cache[identifier] = OrderedDict()
        cache[index] = body
        cache.move_to_end(index)
        while len(cache) > self.frame_cache_limit:
            cache.popitem(last=False)

    def index(self, identifier):
        with self.lock:
            item = self.items.get(identifier)
            if not item:
                return
        tick = 0.0

        def progress(fraction, result):
            nonlocal tick
            now = time.monotonic()
            if now - tick < 0.2:
                return
            tick = now
            with self.lock:
                item["meta"].update(progress=round(fraction * 100, 1), valid_packets=result.valid_packets,
                                    invalid_packets=result.invalid_packets, frame_count=len(result.frames))

        started = time.perf_counter()
        try:
            result = scan_pcap(self.directory(identifier) / "capture.pcap", progress, item["cancel"].is_set)
            with self.lock:
                if item["cancel"].is_set():
                    return
                item["frames"] = result.frames
                (self.directory(identifier) / "frames.json").write_text(json.dumps(result.frames), "utf-8")
                item["meta"].update(status="ready", progress=100, frame_count=len(result.frames),
                                    valid_packets=result.valid_packets, invalid_packets=result.invalid_packets,
                                    ignored_packets=result.ignored_packets, duration=result.duration,
                                    warnings=result.warnings, index_seconds=round(time.perf_counter() - started, 3))
                item["meta"]["preflight_diagnostics"] = diagnostics(self.directory(identifier) / "capture.pcap", result.frames, item["meta"])
                self.save(item)
        except InterruptedError:
            pass
        except Exception as exc:
            with self.lock:
                if not item["cancel"].is_set():
                    item["meta"].update(status="error", error=str(exc))
                    self.save(item)
        finally:
            if item["cancel"].is_set():
                with self.lock:
                    shutil.rmtree(self.directory(identifier), ignore_errors=True)

    def analyze(self, identifier, config):
        with self.lock:
            item = self.items.get(identifier)
            if not item:
                return
        tick = 0.0

        def progress(fraction, _record):
            nonlocal tick
            now = time.monotonic()
            if now - tick < 0.2 and fraction < 1:
                return
            tick = now
            with self.lock:
                if identifier in self.items:
                    item["meta"]["analysis_progress"] = round(fraction * 100, 1)

        try:
            folder = self.directory(identifier)
            summary = analyze_capture(folder / "capture.pcap", item["frames"], folder, config,
                                      progress, item["analysis_cancel"].is_set)
            with self.lock:
                if item["analysis_cancel"].is_set() or identifier not in self.items:
                    return
                item["analysis"] = summary
                item["meta"].update(analysis_status="ready", analysis_progress=100,
                                    analysis_error=None, analysis_version=ANALYSIS_VERSION,
                                    analysis_seconds=summary["elapsed_seconds"],
                                    moving_frames=summary["moving_frames"],
                                    moving_tracks=summary["moving_tracks"])
                self.save(item)
        except InterruptedError:
            pass
        except Exception as exc:
            with self.lock:
                if not item["analysis_cancel"].is_set() and identifier in self.items:
                    item["meta"].update(analysis_status="error", analysis_error=str(exc))
                    self.save(item)


def create_app(data_dir=None):
    store = Store(Path(data_dir or os.environ.get("NA70_DATA_DIR", ROOT / "data")))

    @asynccontextmanager
    async def lifespan(_):
        yield
        with store.lock:
            for item in store.items.values():
                item["cancel"].set()
                item["analysis_cancel"].set()
            for jobs in store.mapping.values():
                for job_id in list(jobs.jobs):
                    jobs.forget(job_id)
        store.pool.shutdown(wait=True, cancel_futures=True)
        store.mapping_pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="NA70 Point Cloud Studio", lifespan=lifespan)
    app.state.store = store

    # ------------------------------------------------------------ mapping jobs
    # One implementation, one result layout.  The legacy registration endpoints
    # below are shims over this job API, not a second pipeline.

    def job_context(job_id):
        key = str(job_id)
        with store.lock:
            for identifier, jobs in store.mapping.items():
                if key in jobs.jobs:
                    return identifier, jobs, jobs.jobs[key]
        raise HTTPException(404, "建图任务不存在")

    def ready_job(job_id):
        identifier, jobs, job = job_context(job_id)
        if job["status"] != "ready":
            raise HTTPException(409, f"任务状态为 {job['status']}，结果尚未就绪")
        return identifier, jobs, job

    def artifact_path(job_id, name):
        _, jobs, job = ready_job(job_id)
        path = jobs.job_directory(job["job_id"]) / name
        if not path.exists():
            raise HTTPException(404, f"结果文件 {name} 不存在")
        return path

    def legacy_result(jobs, job):
        """The pre-job response shape, rebuilt from the persisted frame quality."""
        frames = []
        quality = jobs.job_directory(job["job_id"]) / "frame_quality.jsonl"
        if quality.exists():
            for line in quality.read_text("utf-8").splitlines():
                if not line:
                    continue
                record = json.loads(line)
                frames.append(dict(index=record["index"], time=record["timestamp"],
                                   accepted=record["state"] in ("reference", "accepted", "reinitialized"),
                                   pose=record["pose"], reason=record["state"], rmse_m=record["rmse"],
                                   overlap=record["overlap"]))
        return dict(version=job["code_version"], imu=False, loop_closure=False,
                    frame_count=job["frame_count"], accepted_frames=job["accepted_frames"],
                    frames=frames, metrics=job["metrics"], job_id=job["job_id"])

    LEGACY_STATUS = dict(queued="running", running="running", cancelling="running",
                         ready="ready", error="error", cancelled="error")

    @app.put("/api/captures/{identifier}/preflight")
    def capture_preflight(identifier: UUID, payload: dict):
        with store.lock:
            item = store.get(str(identifier))
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            try:
                m = capture_metadata(payload)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            d = item["meta"].get("preflight_diagnostics")
            if d is None:
                d = diagnostics(store.directory(str(identifier)) / "capture.pcap", item["frames"], item["meta"])
                item["meta"]["preflight_diagnostics"] = d
            result = evaluate(d, m)
            item["meta"]["capture_preflight"] = result
            store.save(item)
            return result

    @app.post("/api/mapping/jobs", status_code=202)
    def create_mapping_job(payload: dict | None = None):
        payload = payload or {}
        identifier = str(payload.get("capture_id", ""))
        with store.lock:
            item = store.get(identifier)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
        jobs = store.mapping_jobs(identifier)
        running = [job for job in jobs.list() if job["status"] in ("queued", "running", "cancelling")]
        if running:
            raise HTTPException(409, "已有建图任务在运行，请先取消或等待完成")
        overrides = payload.get("config") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(422, "config 必须为对象")
        try:
            job = jobs.create((configuration() | overrides) if overrides else None, preflight=item["meta"].get("capture_preflight"))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return job

    @app.get("/api/mapping/jobs")
    def list_mapping_jobs(capture_id: UUID | None = None, limit: int = Query(50, ge=1, le=500)):
        with store.lock:
            targets = [str(capture_id)] if capture_id else list(store.mapping)
        records = []
        for identifier in targets:
            jobs = store.mapping.get(identifier)
            if jobs is None:
                continue
            records.extend(jobs.list())
        records.sort(key=lambda job: job["created_at"], reverse=True)
        return dict(jobs=records[:limit])

    # Multi-capture fixed-point turntable mode uses the same decoder and voxel
    # map production core, but has a store-level job because one result consumes
    # several imported captures.
    @app.post("/api/mapping/turntable/jobs", status_code=202)
    def create_turntable_mapping_job(payload: dict):
        identifiers = payload.get("capture_ids")
        if not isinstance(identifiers, list) or len(identifiers) < 1 or len(set(identifiers)) != len(identifiers):
            raise HTTPException(422, "capture_ids 必须是无重复的记录编号数组")
        angle_log = payload.get("angle_log")
        calibration = payload.get("calibration")
        if not isinstance(angle_log, str) or not angle_log.strip():
            raise HTTPException(422, "angle_log 必须为非空 CSV 文本")
        if not isinstance(calibration, dict):
            raise HTTPException(422, "calibration 必须为 JSON 对象")
        sources = {}
        with store.lock:
            for raw in identifiers:
                identifier = str(raw)
                item = store.get(identifier)
                if item["meta"]["status"] != "ready":
                    raise HTTPException(409, f"记录 {identifier} 尚未就绪")
                name = Path(item["meta"]["filename"]).name
                if name in sources:
                    raise HTTPException(422, f"输入文件名重复: {name}")
                sources[name] = store.directory(identifier) / "capture.pcap"
        overrides = payload.get("config") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(422, "config 必须为对象")
        try:
            return store.turntable.create(sources, [str(value) for value in identifiers], angle_log,
                                          calibration, overrides)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/mapping/turntable/jobs")
    def list_turntable_mapping_jobs():
        return dict(jobs=store.turntable.list())

    @app.get("/api/mapping/turntable/jobs/{job_id}")
    def turntable_mapping_job(job_id: UUID):
        try:
            return dict(store.turntable.get(str(job_id)))
        except KeyError:
            raise HTTPException(404, "转台建图任务不存在")

    @app.post("/api/mapping/turntable/jobs/{job_id}/export")
    def export_turntable_mapping_job(job_id: UUID):
        try:
            return store.turntable.export(str(job_id))
        except KeyError:
            raise HTTPException(404, "转台建图任务不存在")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/mapping/turntable/jobs/{job_id}/artifacts/{name}")
    def download_turntable_mapping_artifact(job_id: UUID, name: str):
        try:
            job = store.turntable.get(str(job_id))
        except KeyError:
            raise HTTPException(404, "转台建图任务不存在")
        safe = Path(name).name
        path = store.turntable.directory(job["job_id"]) / safe
        if not path.exists() or path.name.startswith("_"):
            raise HTTPException(404, "转台建图产物不存在")
        media = "image/png" if safe.endswith(".png") else "application/zip" if safe.endswith(".zip") else "application/octet-stream"
        return Response(path.read_bytes(), media_type=media,
                        headers={"Content-Disposition": f'inline; filename="{safe}"'})

    @app.get("/api/mapping/jobs/{job_id}")
    def mapping_job(job_id: UUID):
        _, _, job = job_context(job_id)
        return dict(job)

    @app.post("/api/mapping/jobs/{job_id}/cancel")
    def cancel_mapping_job(job_id: UUID):
        _, jobs, job = job_context(job_id)
        jobs.cancel(job["job_id"])
        return dict(job)

    @app.get("/api/mapping/jobs/{job_id}/trajectory")
    def mapping_trajectory(job_id: UUID):
        return json.loads(artifact_path(job_id, "trajectory.json").read_text("utf-8"))

    @app.get("/api/mapping/jobs/{job_id}/frames/{frame_index}")
    def mapping_frame(job_id: UUID, frame_index: int):
        path = artifact_path(job_id, "frame_quality.jsonl")
        if frame_index < 0:
            raise HTTPException(404, "帧编号超出范围")
        with path.open("r", encoding="utf-8") as handle:
            for position, line in enumerate(handle):
                if position == frame_index:
                    return json.loads(line)
        raise HTTPException(404, "帧编号超出范围")

    @app.get("/api/mapping/jobs/{job_id}/map")
    def mapping_map(job_id: UUID):
        return json.loads(artifact_path(job_id, "map_metadata.json").read_text("utf-8"))

    @app.get("/api/mapping/jobs/{job_id}/frames/{frame_index}/cloud")
    def mapping_frame_cloud(job_id: UUID, frame_index: int):
        quality = mapping_frame(job_id, frame_index)
        _, jobs, job = job_context(job_id)
        frame = jobs.frames[frame_index]
        points = decode_frame(jobs.folder / "capture.pcap", frame).copy()
        if quality.get("pose") is None:
            points = points[:0]
        else:
            points[:, :3] = transform(points[:, :3], np.asarray(quality["pose"]))
        header = json.dumps(dict(public_frame(frame), index=frame_index, point_count=len(points),
                                 version=1, dtype="float32-le", coordinate_frame="map", map_id=0)).encode()
        header += b" " * (-len(header) % 4)
        return Response(struct.pack("<I", len(header)) + header + points.astype('<f4').tobytes(),
                        media_type="application/octet-stream")

    @app.get("/api/mapping/jobs/{job_id}/map/chunks")
    def mapping_map_chunks(
        job_id: UUID,
        ids: str | None = Query(None, description="逗号分隔的分块 ID"),
        min_x: float | None = None, min_y: float | None = None, min_z: float | None = None,
        max_x: float | None = None, max_y: float | None = None, max_z: float | None = None,
        limit: int = Query(200000, ge=1, le=2000000),
    ):
        """Return only the voxels the caller asked for.

        The browser never receives the whole map: it requests the chunks inside
        its current view, and ``limit`` bounds the voxel payload of one response.
        """
        _, jobs, job = ready_job(job_id)
        directory = jobs.job_directory(job["job_id"]) / "map_chunks"
        index = json.loads((directory / "index.json").read_text("utf-8"))
        selected = index["chunks"]
        if ids:
            wanted = {item.strip() for item in ids.split(",") if item.strip()}
            selected = [chunk for chunk in selected if chunk["id"] in wanted]
        lower = [value for value in (min_x, min_y, min_z)]
        upper = [value for value in (max_x, max_y, max_z)]
        if all(value is not None for value in lower):
            selected = [chunk for chunk in selected if chunk["maximum"][0] >= min_x
                        and chunk["maximum"][1] >= min_y and chunk["maximum"][2] >= min_z]
        if all(value is not None for value in upper):
            selected = [chunk for chunk in selected if chunk["minimum"][0] <= max_x
                        and chunk["minimum"][1] <= max_y and chunk["minimum"][2] <= max_z]

        total = 0
        payload = []
        truncated = False
        for chunk in selected:
            rows = [json.loads(line) for line in
                    (directory / chunk["file"]).read_text("utf-8").splitlines() if line]
            if total + len(rows) > limit:
                rows = rows[:max(0, limit - total)]
                truncated = True
            total += len(rows)
            payload.append(dict(id=chunk["id"], key=chunk["key"], minimum=chunk["minimum"],
                                maximum=chunk["maximum"], voxel_count=len(rows),
                                total_voxels=chunk["voxel_count"], voxels=rows))
            if truncated:
                break
        return dict(job_id=job["job_id"], voxel_size=index["voxel_size"], fields=index["fields"],
                    chunk_voxels=index["chunk_voxels"], chunk_count=index["chunk_count"],
                    returned_chunks=len(payload), returned_voxels=total, truncated=truncated,
                    chunks=payload)

    @app.post("/api/mapping/jobs/{job_id}/export")
    def export_mapping_job(job_id: UUID):
        _, jobs, job = job_context(job_id)
        try:
            return jobs.export(job["job_id"])
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/mapping/jobs/{job_id}/export/{name}")
    def download_mapping_artifact(job_id: UUID, name: str):
        _, jobs, job = job_context(job_id)
        safe = Path(name).name
        path = jobs.job_directory(job["job_id"]) / "export" / safe
        if not path.exists():
            raise HTTPException(404, "导出文件不存在")
        media = "application/zip" if safe.endswith(".zip") else "application/json"
        return Response(path.read_bytes(), media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{safe}"'})

    @app.get("/api/captures/{identifier}/mapping/jobs")
    def capture_mapping_jobs(identifier: UUID):
        return dict(jobs=store.mapping_jobs(str(identifier)).list())

    # ------------------------------------------- legacy registration endpoints

    @app.post("/api/captures/{identifier}/registration")
    def start_registration(identifier: UUID):
        key = str(identifier)
        with store.lock:
            item = store.get(key)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
        jobs = store.mapping_jobs(key)
        running = [job for job in jobs.list() if job["status"] in ("queued", "running", "cancelling")]
        if running:
            raise HTTPException(409, "配准正在运行")
        job = jobs.create()
        return dict(status="running", progress=0.0, job_id=job["job_id"])

    @app.get("/api/captures/{identifier}/registration")
    def registration_result(identifier: UUID):
        key = str(identifier)
        with store.lock:
            store.get(key)
        jobs = store.mapping_jobs(key)
        job = jobs.latest()
        if job is None:
            return dict(status="idle", progress=0.0)
        payload = dict(status=LEGACY_STATUS.get(job["status"], "error"),
                       progress=job["progress"] / 100.0, job_id=job["job_id"], error=job["error"])
        if job["status"] == "ready":
            payload["result"] = legacy_result(jobs, job)
        return payload

    @app.get("/api/health")
    def health():
        return dict(status="ok", decoder=DECODER_VERSION)

    @app.get("/api/captures")
    def captures():
        with store.lock:
            return [dict(item["meta"]) for item in store.items.values()]

    @app.post("/api/captures", status_code=202)
    async def upload(request: Request, id: UUID, filename: str = Query(min_length=1, max_length=255)):
        identifier = str(id)
        with store.lock:
            if identifier in store.items:
                raise HTTPException(409, "记录 ID 已存在")
            folder = store.directory(identifier)
            folder.mkdir(exist_ok=False)
            meta = dict(id=identifier, filename=filename.replace("\\", "/").split("/")[-1], size=0,
                        status="uploading", progress=0, frame_count=0, duration=0,
                        valid_packets=0, invalid_packets=0, ignored_packets=0, warnings=[],
                        error=None, analysis_status="not_analyzed", analysis_progress=0,
                        analysis_error=None, decoder=DECODER_VERSION,
                        created_at=time.time())
            item = dict(meta=meta, frames=[], analysis=None, cancel=threading.Event(),
                        analysis_cancel=threading.Event())
            store.items[identifier] = item
            store.save(item)
        digest = hashlib.sha256()
        try:
            with (folder / "capture.pcap").open("wb") as output:
                async for chunk in request.stream():
                    if item["cancel"].is_set():
                        raise InterruptedError()
                    await asyncio.to_thread(output.write, chunk)
                    digest.update(chunk)
                    meta["size"] += len(chunk)
            with store.lock:
                if item["cancel"].is_set():
                    raise InterruptedError()
                meta.update(status="indexing", sha256=digest.hexdigest())
                store.save(item)
                store.pool.submit(store.index, identifier)
            return dict(meta)
        except (ClientDisconnect, InterruptedError, asyncio.CancelledError):
            with store.lock:
                item["cancel"].set()
                store.items.pop(identifier, None)
                shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(499, "上传已取消")
        except Exception:
            with store.lock:
                item["cancel"].set()
                store.items.pop(identifier, None)
                shutil.rmtree(folder, ignore_errors=True)
            raise

    @app.get("/api/captures/{identifier}")
    def metadata(identifier: UUID):
        with store.lock:
            return dict(store.get(str(identifier))["meta"])

    @app.get("/api/captures/{identifier}/frames")
    def frame_index(identifier: UUID, offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=2000)):
        with store.lock:
            item = store.get(str(identifier))
            return dict(total=len(item["frames"]), frames=[public_frame(f) for f in item["frames"][offset:offset + limit]])

    @app.get("/api/captures/{identifier}/frames/{index}")
    def frame_data(identifier: UUID, index: int):
        key = str(identifier)
        with store.lock:
            item = store.get(key)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            if index < 0 or index >= len(item["frames"]):
                raise HTTPException(404, "帧编号超出范围")
            frame = item["frames"][index]
            body = store.cached_frame(key, index)
        if body is None:
            # Decode outside the store lock: the frame index is immutable once the
            # capture is ready, and decoding under the lock serialised every frame
            # request across the whole server.
            points = decode_frame(store.directory(key) / "capture.pcap", frame)
            # v1: uint32 LE header byte length, UTF-8 JSON padded to 4 bytes, XYZI float32 LE.
            header = json.dumps(dict(public_frame(frame), version=1, fields=["x", "y", "z", "intensity"],
                                     dtype="float32-le"), ensure_ascii=True).encode()
            header += b" " * (-len(header) % 4)
            body = struct.pack("<I", len(header)) + header + points.tobytes()
            with store.lock:
                store.cache_frame(key, index, body)
        return Response(body, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.post("/api/captures/{identifier}/analysis", status_code=202)
    def start_analysis(identifier: UUID):
        key = str(identifier)
        with store.lock:
            item = store.get(key)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            if item["meta"]["analysis_status"] in ("queued", "analyzing"):
                raise HTTPException(409, "分析任务正在运行")
            item["analysis_cancel"] = threading.Event()
            item["analysis"] = None
            # In-memory invalidation is enough: a stale on-disk result is never
            # served because analysis_status is no longer "ready", and the new run
            # atomically replaces analysis.json / analysis_masks.bin when it ends.
            # Deleting up front would also destroy the previous result if the new
            # run fails, which is strictly worse.
            item["meta"].update(analysis_status="queued", analysis_progress=0, analysis_error=None,
                                analysis_method="ray_history_spatiotemporal_bands",
                                moving_frames=0, moving_tracks=0)
            store.save(item)
            config = MotionConfig()
            item["meta"]["analysis_status"] = "analyzing"
            store.pool.submit(store.analyze, key, config)
            return dict(item["meta"])

    @app.get("/api/captures/{identifier}/analysis")
    def analysis_summary(identifier: UUID):
        with store.lock:
            item = store.get(str(identifier))
            if item["meta"]["analysis_status"] != "ready" or not item["analysis"]:
                raise HTTPException(409, "分析结果尚未就绪")
            return {key: value for key, value in item["analysis"].items() if key != "frames"}

    @app.get("/api/captures/{identifier}/analysis/frames/{index}")
    def analysis_frame(identifier: UUID, index: int):
        key = str(identifier)
        with store.lock:
            item = store.get(key)
            if item["meta"]["analysis_status"] != "ready" or not item["analysis"]:
                raise HTTPException(409, "分析结果尚未就绪")
            records = item["analysis"]["frames"]
            if index < 0 or index >= len(records):
                raise HTTPException(404, "帧编号超出范围")
            record = dict(records[index])
            mask_path = store.directory(key) / "analysis_masks.bin"
        with mask_path.open("rb") as source:
            source.seek(record.pop("mask_offset"))
            mask = source.read(record["point_count"])
        if len(mask) != record["point_count"]:
            raise HTTPException(500, "分析掩码文件已损坏，请重新分析")
        header = json.dumps(dict(record, version=1, dtype="uint8"), ensure_ascii=True).encode()
        header += b" " * (-len(header) % 4)
        return Response(struct.pack("<I", len(header)) + header + mask,
                        media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/captures/{identifier}/analysis.csv")
    def analysis_csv(identifier: UUID):
        with store.lock:
            item = store.get(str(identifier))
            if item["meta"]["analysis_status"] != "ready" or not item["analysis"]:
                raise HTTPException(409, "分析结果尚未就绪")
            records = list(item["analysis"]["frames"])
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["frame", "time_s", "background_ready", "considered_points", "foreground_points",
                         "moving_points", "has_motion", "clusters", "moving_clusters", "active_tracks",
                         "moving_bands"])
        for record in records:
            writer.writerow([record["index"], f'{record["time"]:.6f}', int(record["ready"]),
                             record["considered_count"], record["candidate_count"], record["moving_count"],
                             int(record["has_motion"]), len(record["clusters"]),
                             sum(cluster["moving"] for cluster in record["clusters"]), len(record["tracks"]),
                             record.get("moving_bands", 0)])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="motion-analysis.csv"'})

    @app.get("/api/captures/{identifier}/analysis/bands.csv")
    def bands_csv(identifier: UUID):
        """Per-slice local target state for every accepted spatio-temporal band."""
        with store.lock:
            item = store.get(str(identifier))
            if item["meta"]["analysis_status"] != "ready" or not item["analysis"]:
                raise HTTPException(409, "分析结果尚未就绪")
            bands = list(item["analysis"].get("bands", []))
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["band_id", "label", "slice_index", "time_s", "x", "y", "z", "vx", "vy", "vz",
                         "speed_mps", "slice_points", "status", "band_duration_s", "band_displacement_m",
                         "band_trajectory_length_m", "band_linearity", "band_cross_section_m", "band_score"])
        for band in bands:
            for state in band.get("slices", []):
                centre = state["centre"]
                writer.writerow([band["band_id"], band["label"], state["bucket"], f'{state["time"]:.6f}',
                                 f'{centre[0]:.4f}', f'{centre[1]:.4f}', f'{centre[2]:.4f}',
                                 "", "", "", "", state["count"], "CONFIRMED",
                                 f'{band["duration"]:.4f}', f'{band["displacement"]:.4f}',
                                 f'{band["trajectory_length"]:.4f}', f'{band["linearity_branches"]:.4f}',
                                 f'{band["local_cross_norm"]:.4f}', f'{band["score"]:.4f}'])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="motion-bands.csv"'})

    @app.delete("/api/captures/{identifier}", status_code=204)
    def remove(identifier: UUID):
        key = str(identifier)
        with store.lock:
            item = store.get(key)
            item["cancel"].set()
            item["analysis_cancel"].set()
            jobs = store.mapping.pop(key, None)
            if jobs is not None:
                for job_id in list(jobs.jobs):
                    jobs.forget(job_id)
            store.items.pop(key)
            store.frame_cache.pop(key, None)
            # Active readers/writers clean up after closing their handles (Windows).
            shutil.rmtree(store.directory(key), ignore_errors=True)
        return Response(status_code=204)

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
