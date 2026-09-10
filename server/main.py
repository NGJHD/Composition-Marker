"""FastAPI app: static frontend, page uploads, SSE progress, cancellation.

Bound to 127.0.0.1 only, never 0.0.0.0.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import signal
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from . import calibration, config, hardware, jobs, levels, pipeline, version

ACCEPTED_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def _prime_hardware() -> None:
    try:
        hardware.detect_gpu()
        config.backend()
    except Exception as exc:  # noqa: BLE001 - detection must never stop startup
        jobs.log_exception(exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.TEMP.mkdir(parents=True, exist_ok=True)
    config.OUTPUT.mkdir(parents=True, exist_ok=True)
    # Sweep temp in case a previous run died badly -- but not while another
    # copy of the app is still using it. run.bat will happily start a second
    # instance on the next free port, and sweeping there deletes the pages the
    # first one is mid-way through reading.
    if jobs.another_instance_running():
        jobs._append_job_log(
            "startup: another instance is running; temp\\ left alone")
    else:
        jobs.clean_temp(keep_log=False)
    jobs.mark_running()
    jobs.install_kill_on_close()
    # Probe the GPU now, on a background thread, rather than lazily. Detection
    # shells out to nvidia-smi and, on a machine without it, two llama.cpp
    # probes with 60-second timeouts. It is reachable from the status event, so
    # a first call arriving there would stall every request -- Cancel included.
    threading.Thread(target=_prime_hardware, name="gpu-probe", daemon=True).start()
    try:
        yield
    finally:
        jobs.clear_running()
        jobs.shutdown_all()


app = FastAPI(title=version.APP_NAME, docs_url=None, redoc_url=None,
              lifespan=lifespan)


# ---------------------------------------------------------------------------
# static frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((config.WEB / "index.html").read_text(encoding="utf-8"))


@app.get("/app.js")
async def app_js() -> FileResponse:
    return FileResponse(config.WEB / "app.js",
                        media_type="application/javascript; charset=utf-8")


@app.get("/style.css")
async def style_css() -> FileResponse:
    return FileResponse(config.WEB / "style.css", media_type="text/css; charset=utf-8")


@app.get("/api/health")
async def health() -> dict:
    missing = [p.name for p in config.missing_files()]
    return {"ok": not missing, "missing": missing}


@app.get("/api/about")
async def about() -> dict:
    return {
        "name": version.APP_NAME,
        "author": version.APP_AUTHOR,
        "version": version.APP_VERSION,
        "repo_url": version.REPO_URL,
    }


@app.get("/api/options")
async def options() -> dict:
    """Everything the first screen needs: models, levels, languages."""
    described = await asyncio.to_thread(
        lambda: hardware.describe(hardware.detect_vram_mb()))
    described["levels"] = levels.choices()
    # The level and language come back to whatever was marked last, which for a
    # parent working through one child's exercise book is nearly always right.
    prefs = config.load_preferences()
    remembered = str(prefs.get("level") or "")
    described["default_level"] = (remembered if remembered in levels.BY_KEY
                                  else levels.DEFAULT_KEY)
    described["default_language"] = (
        "zh" if prefs.get("language") == "zh" else "en")
    described["languages"] = levels.LANGUAGES
    described["max_pages"] = int(
        config.load_config()["images"].get("max_pages", 12))
    described["image_max_edge"] = int(
        config.load_config()["images"].get("max_edge_px", 1600))
    described["image_quality"] = float(
        config.load_config()["images"].get("jpeg_quality", 0.85))
    return described


@app.get("/api/estimate")
async def estimate(model: str = "", pages: int = 1, kind: str = "mark",
                   corrections: int = 2) -> dict:
    """How long this will take on this machine, or nothing if never measured.

    Before the first completed run there is no honest number: the same job
    varies by more than ten times between a 16 GB NVIDIA card and an integrated
    GPU running on the processor, and a guess that is five times out is worse
    than admitting ignorance. The UI says so in words.
    """
    key = hardware.resolve_key(model or "auto")
    return {
        "model": key,
        "minutes": calibration.total_minutes(key, max(pages, 1), kind, corrections),
    }


@app.get("/api/current")
async def current_job() -> dict:
    """The job in flight, if any.

    Closing the tab must not orphan a run. The page asks this on load and
    reattaches to the live progress stream, which is also the only way to reach
    the Cancel button again after a reload.
    """
    job = jobs.active()
    if job is None:
        return {"job": None}
    return {"job": {
        "id": job.id,
        "kind": job.kind,
        "name": job.name,
        "percent": round(job.percent, 2),
        "stage_label": jobs.STAGE_LABELS.get(job.stage, job.stage),
    }}


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------

@app.post("/api/jobs")
async def create_job() -> dict:
    if jobs.active() is not None:
        return JSONResponse(
            {"error": "Something is already being marked. Wait for it to "
                      "finish, or cancel it first."},
            status_code=409,
        )
    job_id = uuid.uuid4().hex[:12]
    jobs.create(job_id)
    # One job's pages at a time: the folder is emptied when a new one starts so
    # a cancelled run does not leave its photographs behind.
    pages_dir = config.TEMP / "pages"
    import shutil

    shutil.rmtree(pages_dir, ignore_errors=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/pages")
async def upload_page(job_id: str, request: Request, index: int = 0):
    """Stream one page straight to disk as it arrives.

    Raw body rather than multipart: multipart would buffer each image a second
    time, and there is nothing here that a filename would tell us -- the order
    is the order the user arranged in the browser, and it arrives as `index`.
    """
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    if job.state == "running":
        return JSONResponse({"error": "That job has already started."},
                            status_code=409)

    cfg = config.load_config()["images"]
    max_pages = int(cfg.get("max_pages", 12))
    if index < 0 or index >= max_pages:
        return JSONResponse(
            {"error": "That is more pages than this can mark at once "
                      "(%d maximum)." % max_pages},
            status_code=400,
        )

    pages_dir = config.TEMP / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    path = pages_dir / ("page_%02d.jpg" % (index + 1))
    limit = int(cfg.get("max_bytes_per_page", 12000000))

    size = 0
    head = b""
    try:
        with open(path, "wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if not head:
                    head = chunk[:8]
                size += len(chunk)
                if size > limit:
                    raise ValueError("page larger than %d bytes" % limit)
                fh.write(chunk)
    except ValueError:
        path.unlink(missing_ok=True)
        return JSONResponse(
            {"error": "One of those images is too large. They are meant to be "
                      "resized in the browser before they are sent."},
            status_code=400,
        )
    except OSError as exc:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "That page couldn't be saved. %s" % exc},
                            status_code=500)

    if size == 0 or not head.startswith(ACCEPTED_MAGIC):
        path.unlink(missing_ok=True)
        return JSONResponse(
            {"error": "One of those files is not a photograph. Use JPEG, PNG "
                      "or HEIC images of the pages."},
            status_code=400,
        )
    return {"ok": True, "index": index, "bytes": size}


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

@app.post("/api/jobs/{job_id}/start")
async def start(job_id: str, request: Request):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    if jobs.active() is not None:
        return JSONResponse(
            {"error": "Something is already being marked."}, status_code=409)

    body = await request.json()
    pages_dir = config.TEMP / "pages"
    job.pages = sorted(pages_dir.glob("page_*.jpg"))
    if not job.pages:
        return JSONResponse(
            {"error": "No pages were received. Add the photographs again."},
            status_code=400)

    job.kind = "mark"
    job.level = str(body.get("level") or levels.DEFAULT_KEY)
    if job.level not in levels.BY_KEY:
        job.level = levels.DEFAULT_KEY
    job.language = "zh" if str(body.get("language")) == "zh" else "en"
    job.topic = str(body.get("topic") or "").strip()[:200]
    job.model_key = hardware.resolve_key(str(body.get("model") or "auto"))

    # Remembered here rather than on every dropdown change: what is worth
    # coming back to is what was actually marked, not what was clicked past.
    config.remember("level", job.level)
    config.remember("language", job.language)

    _launch(job, pipeline.run)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/correct")
async def generate_correction(job_id: str, request: Request):
    """The Generate and Download button. One or two model calls, no images."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    if jobs.active() is not None:
        return JSONResponse(
            {"error": "Something is already running."}, status_code=409)

    body = await request.json()
    kind = str(body.get("correction") or "both")
    if kind not in ("minimal", "improved", "both"):
        kind = "both"
    job.kind = "correct"
    job.correction = kind
    job.corrected_paths = []
    job._weights = {}
    job._expected = {}

    _launch(job, pipeline.run_correction)
    return {"ok": True}


def _launch(job, target) -> None:
    """Run one job on a worker thread, with every failure path covered."""
    job.state = "running"
    job.started_at = time.time()
    job.stage_started_at = time.time()
    job.percent = 0.0
    job.error = ""
    job.cancel_event.clear()
    job._weights = {}
    job._expected = {}

    def work():
        try:
            target(job)
            job.finish()
        except jobs.Cancelled:
            job.mark_cancelled()
        except jobs.JobError as exc:
            jobs.log_exception(exc)
            job.fail(exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001 - never show a traceback
            detail = jobs.log_exception(exc)
            job.fail("Something went wrong while marking. The details are in "
                     "the diagnostic information below.", detail)
        finally:
            if job.state == "running":
                job.state = "error"

    threading.Thread(target=work, name="job-%s" % job.id, daemon=True).start()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    job.cancel()
    return {"ok": True}


@app.get("/api/events/{job_id}")
async def events(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")

    queue: asyncio.Queue = asyncio.Queue()
    job._queues.append(queue)
    job._loop = asyncio.get_running_loop()

    async def stream():
        # Replay current state so a reconnecting client is never blank.
        yield _sse({"type": "status",
                    **{k: v for k, v in job._status().items() if k != "type"}})
        for line in job.log_lines[-200:]:
            yield _sse({"type": "log", "line": line})
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("type") in {"done", "error", "cancelled"}:
                    break
        finally:
            try:
                job._queues.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return "data: %s\n\n" % json.dumps(event)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

def _documents(job) -> dict:
    folder = config.composition_dir(job.name) if job.name else None
    out = {}
    if not folder or not folder.is_dir():
        return out
    for key, suffix in (("marking", "_marking.md"),
                        ("transcript", "_transcript.md"),
                        ("minimal", "_corrected.md"),
                        ("improved", "_improved.md")):
        path = folder / ("%s%s" % (job.name, suffix))
        if path.exists():
            out[key] = {
                "name": path.name,
                "markdown": path.read_text(encoding="utf-8", errors="replace"),
            }
    return out


@app.get("/api/jobs/{job_id}/result")
async def result(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return {
        "name": job.name,
        "score": job.score,
        "level": job.level,
        "level_label": levels.label(job.level),
        "language": job.language,
        "topic": job.topic,
        "documents": _documents(job),
    }


# There is no download route. Everything a run produces is already a file in
# the composition's own folder, and the result page opens that folder rather
# than handing the browser a second copy to put in Downloads\.


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

@app.get("/api/history")
async def history() -> dict:
    """Everything already marked, newest first.

    The files are the record: a composition deleted from output\\ disappears
    from the list, and nothing is stored anywhere else.
    """
    out = []
    if config.OUTPUT.is_dir():
        for folder in config.OUTPUT.iterdir():
            if not folder.is_dir():
                continue
            report = folder / ("%s_marking.md" % folder.name)
            if not report.exists():
                continue
            record = {}
            try:
                record = json.loads(
                    (folder / "composition.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            # No score and no document inventory: the list shows the name and
            # the level, so neither is read. That also keeps this cheap -- one
            # small JSON per folder, and no report parsed to build a list.
            out.append({
                "name": folder.name,
                "level_label": levels.label(record.get("level") or ""),
                "language": record.get("language") or "en",
                "when": report.stat().st_mtime,
            })
    out.sort(key=lambda r: r["when"], reverse=True)
    return {"compositions": out}


@app.post("/api/history/open")
async def history_open(request: Request):
    """Rebuild a finished job from what is on disk, so the result page opens."""
    body = await request.json()
    name = Path(str(body.get("name") or "")).name
    folder = config.composition_dir(name)
    if not name or not (folder / ("%s_marking.md" % name)).exists():
        return JSONResponse({"error": "That composition is no longer in the "
                                      "output folder."}, status_code=404)

    record = {}
    try:
        record = json.loads((folder / "composition.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    job_id = uuid.uuid4().hex[:12]
    job = jobs.create(job_id)
    job.state = "done"
    job.name = name
    job.level = record.get("level") or levels.DEFAULT_KEY
    job.language = record.get("language") or "en"
    job.topic = record.get("topic") or ""
    job.model_key = record.get("model") or ""
    job.score = record.get("score")
    if job.score is None:
        job.score = pipeline.parse_score(
            (folder / ("%s_marking.md" % name)).read_text(
                encoding="utf-8", errors="replace"))
    job.report_path = folder / ("%s_marking.md" % name)
    job.transcript_path = folder / ("%s_transcript.md" % name)
    return {"job_id": job_id, "name": name}


# ---------------------------------------------------------------------------
# odds and ends
# ---------------------------------------------------------------------------

@app.get("/api/diagnostics")
async def diagnostics() -> PlainTextResponse:
    return PlainTextResponse(jobs.read_job_log())


@app.post("/api/open-output")
async def open_output(request: Request):
    """Open a folder in Explorer -- this composition's, or the output root."""
    import subprocess

    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - no body is a request for the root
        pass
    target = config.OUTPUT
    name = Path(str(body.get("name") or "")).name
    if name and config.composition_dir(name).is_dir():
        target = config.composition_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(["explorer", str(target)])
    except OSError:
        return JSONResponse({"error": "Couldn't open the folder."}, status_code=500)
    return {"ok": True}


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def _emergency_cleanup(*_args) -> None:
    jobs.shutdown_all()


atexit.register(_emergency_cleanup)

# SIGINT is deliberately NOT handled here: uvicorn installs its own handler and
# uses it to run a clean shutdown (which calls jobs.shutdown_all via lifespan).
# Overriding it would leave Ctrl+C unable to stop the server.
for _sig in ("SIGTERM", "SIGBREAK"):
    if hasattr(signal, _sig):
        try:
            signal.signal(getattr(signal, _sig), _emergency_cleanup)
        except (ValueError, OSError):
            pass
