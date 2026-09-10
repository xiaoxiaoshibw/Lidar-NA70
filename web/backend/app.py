import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from .decoder import DECODER_VERSION, decode_frame, public_frame, scan_pcap

ROOT = Path(__file__).resolve().parents[1]


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.items = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pcap-index")
        for file in self.root.glob("*/meta.json"):
            try:
                meta = json.loads(file.read_text("utf-8"))
                if str(UUID(file.parent.name)) != meta["id"]:
                    continue
                if meta["status"] in ("uploading", "indexing"):
                    meta.update(status="error", error="上次导入被中断，请移除后重新导入")
                frames_path = file.parent / "frames.json"
                frames = json.loads(frames_path.read_text("utf-8")) if meta["status"] == "ready" else []
                self.items[meta["id"]] = dict(meta=meta, frames=frames, cancel=threading.Event())
            except (ValueError, KeyError, OSError):
                continue

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


def create_app(data_dir=None):
    store = Store(Path(data_dir or os.environ.get("NA70_DATA_DIR", ROOT / "data")))

    @asynccontextmanager
    async def lifespan(_):
        yield
        with store.lock:
            for item in store.items.values():
                item["cancel"].set()
        store.pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="NA70 Point Cloud Studio", lifespan=lifespan)
    app.state.store = store

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
                        error=None, analysis_status="not_analyzed", decoder=DECODER_VERSION,
                        created_at=time.time())
            item = dict(meta=meta, frames=[], cancel=threading.Event())
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
        with store.lock:
            item = store.get(str(identifier))
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            if index < 0 or index >= len(item["frames"]):
                raise HTTPException(404, "帧编号超出范围")
            frame = item["frames"][index]
            points = decode_frame(store.directory(str(identifier)) / "capture.pcap", frame)
        # v1: uint32 LE header byte length, UTF-8 JSON padded to 4 bytes, XYZI float32 LE.
        header = json.dumps(dict(public_frame(frame), version=1, fields=["x", "y", "z", "intensity"],
                                 dtype="float32-le"), ensure_ascii=True).encode()
        header += b" " * (-len(header) % 4)
        return Response(struct.pack("<I", len(header)) + header + points.tobytes(),
                        media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.delete("/api/captures/{identifier}", status_code=204)
    def remove(identifier: UUID):
        with store.lock:
            item = store.get(str(identifier))
            item["cancel"].set()
            store.items.pop(str(identifier))
            # Active readers/writers clean up after closing their handles (Windows).
            shutil.rmtree(store.directory(str(identifier)), ignore_errors=True)
        return Response(status_code=204)

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
