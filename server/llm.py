"""llama-server lifecycle and chat calls, including the vision path.

Uses http.client rather than a third-party client for two reasons: it is in the
standard library, so nothing extra is vendored, and it exposes the connection
object, which is what lets a cancel abort a request already in flight instead
of waiting out a whole call.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Optional

from . import config
from .jobs import Cancelled, Job, JobError

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LLM_FAILED = "The language model could not be started."
LLM_CALL_FAILED = "The language model stopped responding."

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK_RE = re.compile(r"^.*?</think>", re.DOTALL)

# The chat template raises a Jinja exception -- surfacing as HTTP 500 -- for any
# effort outside this set, and silently rewrites "high" to "xhigh". Validate
# before dispatch rather than after.
VALID_EFFORT = {"xhigh", "medium", "low"}


def strip_thinking(text: str) -> str:
    """Remove <think> blocks, even when thinking is disabled.

    With --jinja, llama.cpp returns reasoning in a separate reasoning_content
    field and `content` is already clean. This is belt and braces: a stray
    block reaching a marked report is far more damaging than a wasted regex,
    and it also handles a truncated stream that leaves an unbalanced </think>.
    """
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = _OPEN_THINK_RE.sub("", text)
    return text.strip()


def load_prompt(name: str) -> str:
    """Load a prompt from prompts\\ and inline the shared rules.

    Substitution is by str.replace, never str.format: the prompts contain
    literal braces and Markdown that format() would choke on.
    """
    path = config.PROMPTS / ("%s.txt" % name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JobError(
            "A prompt file is missing from the application folder.",
            "cannot read %s: %s" % (path, exc),
        ) from exc
    if "{{SHARED_RULES}}" in text:
        shared = (config.PROMPTS / "_shared.txt").read_text(encoding="utf-8").strip()
        text = text.replace("{{SHARED_RULES}}", shared)
    return text


def fill(template: str, **values) -> str:
    for key, value in values.items():
        template = template.replace("{{%s}}" % key.upper(), str(value))
    return template


def data_url(path: Path) -> str:
    """A page photograph as the chat API wants it.

    The browser has already rotated and downscaled it, so this is a plain
    base64 wrap of bytes that are typically 200-400 KB. Over loopback that is
    not worth streaming or caching.
    """
    raw = path.read_bytes()
    kind = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return "data:%s;base64,%s" % (kind, base64.b64encode(raw).decode("ascii"))


class LlamaServer:
    """Owns the llama-server process for the life of one job."""

    def __init__(self, job: Job, cfg: dict):
        from . import hardware

        self.job = job
        self.cfg = cfg
        self.llm = cfg["llm"]
        # "Port" in the model dropdown means the operator is running their own
        # llama-server and we are a client of it: nothing is loaded, nothing is
        # shut down afterwards, and the port is theirs rather than ours.
        self.external = hardware.is_external(job.model_key)
        self.port = (int(job.external_port or hardware.DEFAULT_EXTERNAL_PORT)
                     if self.external else int(self.llm.get("port", 8719)))
        self.proc: Optional[subprocess.Popen] = None
        self._conn: Optional[HTTPConnection] = None
        self._conn_lock = threading.Lock()
        self._resolved = None
        self._log_handle = None

    # -- lifecycle -------------------------------------------------------

    def resolve_model(self) -> tuple[Path, str, str]:
        """Return (weights path, gpu_layers, offload regex)."""
        from . import hardware

        if getattr(self, "_resolved", None) is not None:
            return self._resolved

        layers = str(self.llm.get("gpu_layers", "auto"))
        regex = str(self.llm.get("cpu_ffn_regex") or "")

        requested = self.job.model_key or self.llm.get("model") or "auto"
        if requested not in hardware.BY_KEY and requested not in ("auto", ""):
            # An explicit path in config.json still wins.
            self._resolved = (config.resolve(requested),
                              "" if layers == "auto" else layers,
                              "" if regex == "auto" else regex)
            return self._resolved

        gpu = hardware.detect_gpu()
        key = hardware.resolve_key(requested, gpu["vram_mb"])
        path = hardware.model_path(key)
        auto_layers, auto_regex = hardware.placement(key, gpu)
        if layers == "auto":
            layers = auto_layers
        if regex == "auto":
            regex = auto_regex

        placement = ("llama.cpp decides" if layers == ""
                     else "on the processor" if layers == "0"
                     else "%d FFN blocks to system RAM" % (regex.count("|") + 1)
                     if regex else "fully on the GPU")
        self.job.log(
            "llm: %s | %s via %s (%d MiB%s) | %s"
            % (path.name, gpu["device"], config.backend(), gpu["vram_mb"],
               ", unified memory" if gpu.get("uma") else "", placement)
        )
        # Record what was actually chosen. Calibration is keyed by model, and a
        # job that never went through the dropdown would otherwise be timed
        # against the wrong one.
        self.job.model_key = key
        self._resolved = (path, layers, regex)
        return self._resolved

    def command(self) -> list[str]:
        from . import hardware

        model, layers, regex = self.resolve_model()
        cmd = [
            str(config.LLAMA_SERVER),
            "-m", str(model),
            "--ctx-size", str(int(self.llm.get("ctx_size", 16384))),
            "--flash-attn", "on",
            "--cache-type-k", str(self.llm.get("cache_type_k", "q8_0")),
            "--cache-type-v", str(self.llm.get("cache_type_v", "q8_0")),
            "--jinja",
            "--batch-size", "512", "--ubatch-size", "512",
            "--parallel", "1",
            "--host", "127.0.0.1", "--port", str(self.port),
            "--no-webui",
        ]

        # The vision projector. Without it the server starts, accepts an
        # image_url part, and answers about an image it never saw -- which is
        # far worse than refusing, because the answer is fluent.
        mmproj = config.mmproj_path()
        if not mmproj.exists():
            raise JobError(
                "The handwriting reader is missing from the application folder.",
                "mmproj not found at %s" % mmproj,
            )
        cmd += ["--mmproj", str(mmproj)]

        offload = str(self.llm.get("mmproj_offload", "auto"))
        if offload == "auto":
            # Keep the encoder wherever the weights are. On unified memory the
            # language model runs on the processor, and sending only the image
            # encode across to the integrated GPU costs a copy each way for a
            # device that shares the same memory bus.
            if layers == "0":
                cmd.append("--no-mmproj-offload")
        elif offload in ("0", "false", "no", "off"):
            cmd.append("--no-mmproj-offload")

        threads = str(self.llm.get("threads", "auto"))
        if threads and threads != "auto":
            cmd += ["--threads", threads]
        if layers:
            cmd += ["--n-gpu-layers", layers]
        # Only when there is a choice to get wrong. Detection sized the model
        # against one specific adapter; llama.cpp must use that one and not
        # whichever it would have picked by itself.
        device_id = hardware.detect_gpu().get("device_id")
        if device_id and layers != "0":
            cmd += ["--device", device_id]
        if regex:
            cmd += ["--override-tensor", regex]

        # Speculative decoding off the model's own block-64 MTP layer: no
        # second file, no draft model to size, 1.77x measured. Gated on the
        # layer actually being in these weights, because the low-quality quant
        # does not keep it -- see hardware.model_has_mtp. Off on the CPU build:
        # drafting spends compute to save memory bandwidth, which is the wrong
        # way round when there is no GPU to be starved.
        if (str(self.llm.get("mtp", "auto")) != "off"
                and layers != "0"
                and hardware.model_has_mtp(model)):
            cmd += ["--spec-type", "draft-mtp"]
        return cmd

    def start(self) -> None:
        if self.external:
            return self._attach()
        model, _layers, _regex = self.resolve_model()
        if not model.exists():
            raise JobError(LLM_FAILED, "model file missing: %s" % model)

        started = time.time()
        self.job.log("llm: starting llama-server on port %d" % self.port)
        log_path = config.TEMP / "llama-server.log"
        config.TEMP.mkdir(parents=True, exist_ok=True)
        # stderr to a file, never a pipe: llama-server is verbose and a full
        # pipe would block it.
        self._log_handle = open(log_path, "w", encoding="utf-8", errors="replace")
        try:
            self.proc = subprocess.Popen(
                self.command(),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=config.child_env(),
                creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise JobError(LLM_FAILED, "could not spawn llama-server: %s" % exc) from exc
        self.job.register_proc(self.proc)

        timeout = float(self.llm.get("startup_timeout_s", 240))
        self.job.log("llm: loading model (up to 2 minutes on first run)")
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.job.check_cancelled()
            if self.proc.poll() is not None:
                raise JobError(LLM_FAILED, self._log_tail())
            if self._healthy():
                elapsed = time.time() - started
                self.job.log("llm: ready after %.0fs" % elapsed)
                from . import calibration

                calibration.record(self.job.model_key, "load", elapsed)
                return
            # The bar has nothing to count during a two-minute model load, and
            # a bar that does not move is indistinguishable from a hang.
            self.job.tick()
            time.sleep(1.0)
        raise JobError(
            LLM_FAILED,
            "llama-server did not become ready within %.0fs\n%s"
            % (timeout, self._log_tail()),
        )

    def _attach(self) -> None:
        """Use a llama-server somebody else started, on 127.0.0.1:<port>.

        No model is loaded and no process is spawned, so the only thing that
        can be checked is whether something is answering. Whether it has a
        vision projector is deliberately not probed: the operator chose this
        option and knows what they are running, and a server without one will
        answer about a page it never saw -- which shows up as a nonsense
        transcript on the Transcript tab rather than as a silent wrong mark.
        """
        self.job.log("llm: using the llama-server already running on port %d"
                     % self.port)
        if self._healthy():
            self.job.log("llm: connected")
            return
        raise JobError(
            "Nothing is answering on port %d. Start your llama-server first, "
            "or choose High or Low Quality to use the built-in model."
            % self.port,
            "no response from http://127.0.0.1:%d/health" % self.port,
        )

    def _log_tail(self, lines: int = 40) -> str:
        try:
            with open(config.TEMP / "llama-server.log", "r",
                      encoding="utf-8", errors="replace") as fh:
                return "".join(fh.readlines()[-lines:])
        except OSError:
            return ""

    def _healthy(self) -> bool:
        try:
            conn = HTTPConnection("127.0.0.1", self.port, timeout=3)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return resp.status == 200
        except Exception:  # noqa: BLE001 - not up yet is the normal case
            return False

    def stop(self) -> None:
        """Shut the server down. 16 GB of VRAM must not stay allocated."""
        self.abort()
        if self.external:
            return          # not ours to stop
        proc, self.proc = self.proc, None
        if proc is None:
            return
        self.job.unregister_proc(proc)
        try:
            if proc.poll() is None:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=20,
                )
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._log_handle:
                self._log_handle.close()
        except Exception:  # noqa: BLE001
            pass
        self.job.log("llm: server stopped")

    def abort(self) -> None:
        """Drop any in-flight request so a cancel does not wait out a call."""
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "LlamaServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- requests --------------------------------------------------------

    def chat(self, content, *, thinking: bool = False, effort: str = "medium",
             max_tokens: int = 2000, temperature: float = 0.7,
             top_p: float = 0.8, stage: str = "") -> str:
        """One chat completion, streamed. Returns the cleaned content.

        `content` is either a string or a list of OpenAI content parts, which
        is how an image is attached:

            [{"type": "text", "text": ...},
             {"type": "image_url", "image_url": {"url": "data:image/jpeg;..."}}]

        Streaming is not for show. A non-streaming call needs a single total
        timeout covering the whole generation, and there is no good value for
        it: a marking call on unified memory can legitimately run for minutes,
        while a genuinely hung server should be caught in seconds. Streaming
        replaces that guess with an idle timeout between tokens, and gives the
        progress bar something real to move on.
        """
        payload = {
            "messages": [{"role": "user", "content": content}],
            "max_tokens": int(max_tokens),
            "temperature": temperature,
            "top_p": top_p,
            "top_k": 20,
            "repeat_penalty": 1.0,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
        }
        if thinking:
            # Validated here rather than passed through: an unexpected value
            # raises inside the Jinja template and comes back as HTTP 500.
            payload["chat_template_kwargs"]["reasoning_effort"] = (
                effort if effort in VALID_EFFORT else "medium")

        # How much the model is about to read, so the progress line can say so
        # from the first second rather than only in hindsight. Exact rather
        # than estimated: a characters-per-token guess drifts badly on a
        # rubric full of Markdown, and this costs one local round trip.
        prompt_tokens = self.token_count(content)
        text = self._stream(payload, idle_timeout=180.0, stage=stage,
                            prompt_tokens=prompt_tokens)
        return strip_thinking(text)

    def token_count(self, content) -> int:
        """Prompt size via /tokenize. 0 when it cannot be counted.

        An image cannot go through /tokenize, so a page call counts only its
        text part -- and the image is the bulk of it. Rather than report a
        number that is wrong by 2,500, those calls report nothing and the
        progress line simply omits the "read" figure.
        """
        if not isinstance(content, str):
            return 0
        try:
            data = self._post_json("/tokenize", {"content": content}, timeout=60)
        except Exception:  # noqa: BLE001 - a missing count is not a failure
            return 0
        return len(data.get("tokens", []))

    def _post_json(self, path: str, payload: dict, timeout: float) -> dict:
        body = json.dumps(payload).encode("utf-8")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("POST", path, body=body,
                         headers={"Content-Type": "application/json",
                                  "Content-Length": str(len(body))})
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError("HTTP %d from %s" % (resp.status, path))
            return json.loads(raw)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # How the retry loop reacts to a call that ran out of budget. Measured on
    # the marking call: `max_tokens` caps *everything the model generates*,
    # reasoning included, so at 3000 tokens with thinking on the model spent
    # the entire budget reasoning and returned an empty `content` -- a silent
    # failure that looked like a broken server. The cap is now generous enough
    # (config `llm.max_mark_tokens`), and if a call still runs out, the retry
    # gives up the reasoning rather than repeating the same call and losing
    # another eighty seconds to it.
    _RETRY_WITHOUT_THINKING = True

    def _post_stream(self, payload: dict, timeout: float):
        body = json.dumps(payload).encode("utf-8")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        with self._conn_lock:
            self._conn = conn
        conn.request(
            "POST", "/v1/chat/completions", body=body,
            headers={"Content-Type": "application/json",
                     "Content-Length": str(len(body))},
        )
        return conn

    def _stream(self, payload: dict, idle_timeout: float, stage: str = "",
                prompt_tokens: int = 0) -> str:
        """POST a streaming completion and assemble the content.

        The timeout is per read, not per call: it catches a server that has
        stopped producing tokens without capping a call that is simply long.
        """
        attempts = 0
        last_detail = ""
        best = ""
        while attempts < 3:
            attempts += 1
            self.job.check_cancelled()
            conn = None
            try:
                conn = self._post_stream(payload, idle_timeout)
                resp = conn.getresponse()
                if resp.status != 200:
                    raw = resp.read()[:400].decode("utf-8", "replace")
                    raise RuntimeError("HTTP %d: %s" % (resp.status, raw))
                parts = []
                finish = ""
                pending = b""
                last_tick = 0.0
                last_log = time.time()
                stats = self.job.begin_call(prompt_tokens)
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        piece, thinking, reason = self._sse_event(line)
                        if piece:
                            parts.append(piece)
                            stats["content"] += 1
                        if thinking:
                            stats["reasoning"] += 1
                        if reason:
                            finish = reason
                    now = time.time()
                    if now - last_tick > 1.0:
                        last_tick = now
                        self.job.check_cancelled()
                        self.job.tick()
                    # A line in the log every half minute as well as the live
                    # counter, so a finished job's log still shows what the
                    # long call was doing while it ran.
                    if now - last_log > 30.0:
                        last_log = now
                        self.job.log("llm: %s" % self.job.call_summary())
                text = "".join(parts).strip()
                self.job.log("llm: %s done, %s"
                             % (stage or "call", self.job.call_summary()))
                self.job.end_call()
                if text and finish != "length":
                    return text
                if text:
                    # Truncated, but there is something. Keep it as the answer
                    # of last resort and try once more for a whole one.
                    best = text if len(text) > len(best) else best
                    last_detail = "the model ran out of room mid-answer"
                else:
                    last_detail = ("the model used its whole budget on "
                                   "reasoning and produced no answer"
                                   if finish == "length"
                                   else "the model returned an empty completion")
                if finish == "length" and self._RETRY_WITHOUT_THINKING:
                    kwargs = payload.get("chat_template_kwargs") or {}
                    if kwargs.get("enable_thinking"):
                        kwargs["enable_thinking"] = False
                        kwargs.pop("reasoning_effort", None)
                        payload["chat_template_kwargs"] = kwargs
                        last_detail += "; retrying without thinking"
            except Cancelled:
                raise
            except (socket.timeout, TimeoutError) as exc:
                last_detail = "no output for %.0fs (%s)" % (idle_timeout, exc)
            except Exception as exc:  # noqa: BLE001
                last_detail = str(exc)
            finally:
                with self._conn_lock:
                    if self._conn is conn:
                        self._conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self.job.cancelled:
                raise Cancelled()
            self.job.log("llm: %s call failed (%s)%s"
                         % (stage or "model", last_detail,
                            " - retrying" if attempts < 3 else ""))
            if attempts >= 3:
                break
            time.sleep(2.0 * attempts)
        if best:
            self.job.log("llm: %s ran out of room every time; keeping the "
                         "longest answer" % (stage or "model"))
            return best
        raise JobError(LLM_CALL_FAILED, "%s: %s" % (stage or "call", last_detail))

    @staticmethod
    def _sse_event(line: bytes) -> tuple:
        """One `data:` line of an OpenAI-style stream.

        Returns (content, reasoning, finish_reason).

        Only `content` is kept for the document. With --jinja the model's
        reasoning arrives in a separate `reasoning_content` field and is not
        part of the answer -- but it is *counted*, because on the marking call
        the model can spend a minute and a half there before the first word of
        the report appears. Counting only content made the progress line sit
        unchanged through all of it, which is what "it's just stuck there"
        looks like from the outside.

        `finish_reason` matters as much as the text. "length" means the answer
        was cut off at `max_tokens` -- which, with thinking on, can mean there
        was no answer at all.
        """
        if not line.startswith(b"data:"):
            return "", "", ""
        raw = line[5:].strip()
        if not raw or raw == b"[DONE]":
            return "", "", ""
        try:
            event = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return "", "", ""
        piece, thinking, reason = "", "", ""
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                piece = delta["content"]
            if delta.get("reasoning_content"):
                thinking = delta["reasoning_content"]
            if choice.get("finish_reason"):
                reason = choice["finish_reason"]
        return piece, thinking, reason
