"""Persistent asynchronous wrapper around the production turntable mapper."""
from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from .turntable import configuration, run_turntable_job, validate_configuration


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
    temporary.replace(path)


class TurntableJobs:
    """Store-level multi-capture jobs that survive service restarts."""

    def __init__(self, root: Path, pool, lock):
        self.root = Path(root) / "turntable_mapping"
        self.pool = pool
        self.lock = lock
        self.jobs = {}
        self.reload()

    def reload(self):
        self.jobs = {}
        if not self.root.is_dir():
            return
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text("utf-8"))
                if job.get("status") in {"queued", "running"}:
                    job.update(status="error", error="上次转台建图任务被服务重启中断", finished_at=time.time())
                    _write(path, job)
                self.jobs[job["job_id"]] = job
            except (OSError, ValueError, KeyError):
                continue

    def directory(self, job_id):
        return self.root / job_id

    def list(self):
        return sorted(self.jobs.values(), key=lambda item: item.get("created_at", 0), reverse=True)

    def get(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.jobs[job_id]

    def create(self, sources: dict[str, Path], source_ids: list[str], angle_log: str,
               calibration: dict, cfg: dict | None = None):
        if any(job.get("status") in {"queued", "running"} for job in self.jobs.values()):
            raise ValueError("已有转台建图任务在运行")
        cfg = validate_configuration((configuration() | (cfg or {})))
        job_id = str(uuid4()); directory = self.directory(job_id); directory.mkdir(parents=True)
        angle_path = directory / "_angle_log.csv"
        calibration_path = directory / "_turntable_calibration.json"
        angle_path.write_text(angle_log, "utf-8")
        calibration_path.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), "utf-8")
        job = dict(job_id=job_id, status="queued", progress=0.0, created_at=time.time(),
                   started_at=None, finished_at=None, error=None, source_capture_ids=source_ids,
                   source_filenames=list(sources), output_directory=str(directory), artifacts={})
        self.jobs[job_id] = job; _write(directory / "job.json", job)
        self.pool.submit(self._run, job_id, sources, angle_path, calibration_path, cfg)
        return dict(job)

    def _run(self, job_id, sources, angle_path, calibration_path, cfg):
        job = self.jobs[job_id]; directory = self.directory(job_id)
        with self.lock:
            job.update(status="running", progress=5.0, started_at=time.time())
            _write(directory / "job.json", job)
        try:
            result = run_turntable_job(sources, angle_path, calibration_path, directory, cfg,
                                       command="POST /api/mapping/turntable/jobs")
            artifacts = {path.name: path.name for path in directory.iterdir() if path.is_file() and not path.name.startswith("_")}
            with self.lock:
                job.update(result["job"], job_id=job_id, status="ready", progress=100.0,
                           source_capture_ids=job["source_capture_ids"], source_filenames=list(sources),
                           finished_at=time.time(), error=None, artifacts=artifacts,
                           submaps=result["qualities"], validation=result["validation"],
                           map_metadata=result["map_metadata"])
                _write(directory / "job.json", job)
        except Exception as exc:  # persisted failure is part of the evidence
            with self.lock:
                job.update(status="error", progress=100.0, finished_at=time.time(), error=str(exc))
                _write(directory / "job.json", job)

    def export(self, job_id):
        job = self.get(job_id)
        if job.get("status") != "ready":
            raise ValueError("转台建图结果尚未就绪")
        directory = self.directory(job_id); target = directory / "turntable-development-evidence.zip"
        temporary = target.with_suffix(".zip.tmp")
        members = [path for path in sorted(directory.iterdir())
                   if path.is_file() and not path.name.startswith("_")
                   and path not in {target, temporary}]
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in members:
                archive.write(path, path.name)
        temporary.replace(target)
        return dict(bundle=target.name, bundle_bytes=target.stat().st_size,
                    members=sorted(job.get("artifacts", {})))
