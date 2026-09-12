"""Paths, config loading and the child-process environment.

Everything the app touches lives under ROOT so the folder stays portable:
nothing is written outside the app directory, ever.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BIN = ROOT / "bin"
PROMPTS = ROOT / "prompts"
WEB = ROOT / "web"
TEMP = ROOT / "temp"
OUTPUT = ROOT / "output"

CONFIG_PATH = ROOT / "config.json"

# One folder per backend. CUDA where the machine has an NVIDIA card, Vulkan for
# AMD and Intel (and as a fallback anywhere), CPU as the last resort. The
# shared CUDA runtime sits in bin\ itself, where Windows finds it on PATH.
#
#     bin\llama-cuda    bin\llama-vulkan    bin\llama-cpu
#
# There is only one engine here. The meeting app this is derived from shipped
# whisper.cpp alongside llama.cpp and had to keep their ggml DLLs apart;
# nothing in this app touches audio, so bin\ is half the size and the
# per-engine resolution collapses to a single lookup.
LEGACY_DIR = BIN / "llama"


def engine_dir(backend_name: str) -> Path:
    candidate = BIN / ("llama-%s" % backend_name)
    if candidate.is_dir():
        return candidate
    if LEGACY_DIR.is_dir():
        return LEGACY_DIR
    return candidate


def llama_server_path(backend_name: str) -> Path:
    return engine_dir(backend_name) / "llama-server.exe"


_backend_cache: dict = {}


def backend() -> str:
    """The backend llama.cpp will actually use, honouring config.json.

    Memoised: it stats the binary folders and is asked for on every page load.
    config.json is read once at startup anyway, so nothing here can change
    without a restart.
    """
    if "llama" in _backend_cache:
        return _backend_cache["llama"]
    from . import hardware

    try:
        requested = str(load_config().get("gpu", {}).get("backend", "auto"))
    except RuntimeError:
        requested = "auto"
    _backend_cache["llama"] = hardware.resolve_backend(requested)
    return _backend_cache["llama"]


class _BackendPath:
    """`config.LLAMA_SERVER` reads as a constant but is a lookup.

    Attribute-compatible with Path so every str(), .exists() and .parent call
    site works unchanged.
    """

    def __init__(self, resolver):
        self._resolver = resolver

    def _p(self) -> Path:
        return self._resolver(backend())

    def __getattr__(self, name):
        return getattr(self._p(), name)

    def __fspath__(self) -> str:
        return str(self._p())

    def __str__(self) -> str:
        return str(self._p())

    def __truediv__(self, other):
        return self._p() / other


LLAMA_SERVER = _BackendPath(llama_server_path)

MODELS = ROOT / "models"


def models_dir() -> Path:
    """Where the weights live.

    models\\ inside the app folder by default, which is what keeps the whole
    thing portable. The override exists for one case: a machine that already
    holds these exact GGUFs for another app has no reason to keep a second
    16.5 GB copy. An absolute path is taken as given; a relative one resolves
    against the app folder, so "..\\Meeting-Summarizer\\models" works and still
    moves with a copied pair of folders.
    """
    try:
        raw = str(load_config().get("paths", {}).get("models_dir") or "models")
    except RuntimeError:
        raw = "models"
    return resolve(raw)


def composition_dir(name: str) -> Path:
    """Everything one composition produced, in one folder.

    A run makes a marked report and a transcript, plus up to two corrected
    versions on demand. Flat in output\\ those interleave with every other
    piece of work; the filenames keep the composition prefix so a document
    still identifies itself once copied out of the folder.
    """
    return OUTPUT / Path(name).name


def write_atomic(path: Path, text: str) -> None:
    """Write so the previous version survives a failure mid-write.

    Documents are overwritten in place when regenerated. A cancel is already
    safe -- nothing is written until the model has finished -- but a crash or a
    full disk during the write itself would leave a truncated file where a good
    one used to be. Write beside it and rename: os.replace is atomic on Windows
    within a volume, so a reader sees either the old file or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


_DEFAULTS = {
    "llm": {
        "model": "auto",
        # 16384, and this was tested at 32768 before being put back.
        #
        # The worry was that a real composition -- 380 words for Primary 4, 800
        # for JC2, not the 157-word test script -- would outgrow the window.
        # Measured on a real 380-word three-page script: prompt 1,647,
        # reasoning 1,931, report 1,098. About 4,700 tokens, or 29% of 16k.
        #
        # The reasoning does NOT scale with the composition the way it looked
        # like it would: the same 157-word script produced 2,191 tokens of
        # reasoning on one run and 5,504 on another, so the variation between
        # runs dwarfs the variation with length. 16k holds the worst of both.
        #
        # And 32768 has a real cost, measured on a 10 GB card: the extra
        # ~0.53 GB of KV cache pushes the image-encode buffers out of VRAM, and
        # transcription fell from 6-7 seconds a page to **73 seconds a page**,
        # with marking down from 36 to 24 tokens/s. A twelve-fold regression on
        # the vision path to buy headroom nothing was using.
        "ctx_size": 16384,
        "gpu_layers": "auto",
        "cpu_ffn_regex": "auto",
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
        "threads": "auto",
        # The vision projector. The same file serves both quantisations: it is
        # the image encoder, not part of the language weights.
        "mmproj": "mmproj-F16.gguf",
        "mmproj_offload": "auto",
        # "auto" uses the model's own MTP layer as a draft model where the
        # weights carry one; "off" disables it. 1.77x measured, at 0.8 GB of
        # VRAM. BUILD_NOTES section 7.6i.
        "mtp": "auto",
        # NOT 8080. That is llama.cpp's own default, so it is exactly the port
        # an operator's own llama-server will be sitting on -- and the Port
        # option in the model dropdown exists to talk to one of those. Two
        # servers fighting over 8080 is a confusing failure; a number nobody
        # else claims costs nothing.
        "port": 8719,
        "startup_timeout_s": 240,
        "max_transcribe_tokens": 2000,
        # These caps cover EVERYTHING the model generates, reasoning included.
        # Marking runs with thinking on, and the reasoning is both large and
        # highly variable: 2,191 and 5,504 tokens on two runs of the *same*
        # two-page script. At 3,000 the model spent the entire budget reasoning
        # and returned an empty answer; at 6,000 the 5,504-token run was cut
        # off mid-report and had to be redone with thinking off, which marks
        # less well.
        #
        # 12,000 covers the worst reasoning seen plus a long report, and still
        # leaves room for the prompt inside a 16k window: the largest prompt
        # measured is 1,647 for a three-page script, and a JC2 essay should not
        # exceed ~2,500. A cap is not a target -- a real call finishes in 3,000
        # -- so the headroom costs nothing except when it is the difference
        # between a whole report and half a table.
        "max_mark_tokens": 12000,
        "max_correct_tokens": 4000,
        # The improved rewrite runs with thinking on and writes a whole
        # composition, so it needs room for both -- and 8,000 was not room for
        # both: the cap fell mid-thought and the call returned an empty
        # content, on Q4_K_M and IQ4_XS alike.
        #
        # 12,000 is better and is not a cure. Measured reasoning on one
        # 399-word script ran 9,815 / 10,125 / 10,563 / >12,000 / >12,000
        # tokens -- unbounded, so no budget inside a 16,384 context catches
        # every call, and the ones it misses fall through to the
        # retry-without-thinking path. reasoning_effort is not the lever:
        # "low" spent the same 8,000 and also returned nothing.
        #
        # It cannot simply be raised to the context ceiling either. At 14,000
        # only ~2,300 tokens are left for a prompt that carries the whole
        # composition plus two sections of the marking report: fine for a P4
        # script, not for a JC2 essay. BUILD_NOTES section 7.6g has the two
        # real ways out, both of which are design changes rather than numbers.
        "max_improved_tokens": 12000,
        # The improved rewrite is checked against these and re-asked if it
        # misses, because section 8's two length rules -- never shorter than
        # the original, never past 105% of it -- are arithmetic, and the model
        # misses them often enough to matter. 0.95 rather than 1.00 as the
        # floor: "not shorter" counted to the word would fail a rewrite that
        # is one word down, which is not what the rule is protecting against.
        # The retries run with thinking off; see pipeline._hold_the_length.
        "rewrite_min_ratio": 0.95,
        "rewrite_max_ratio": 1.05,
        "rewrite_length_attempts": 3,
    },
    "thinking": {
        # Transcription is mechanical: reasoning about handwriting produces
        # nothing but tokens, once per page. Marking is judgement, and it is a
        # single call, which is exactly where thinking earns its cost.
        "transcribe": False,
        "mark": True,
        "mark_effort": "medium",
        # The two corrections are opposite jobs. The minimal one finds errors
        # and fixes them; reasoning buys it nothing. The improved one has to
        # reimagine the composition while holding the plot, a target level one
        # step above the child's, a hard word ceiling and a paragraph structure
        # all at once -- it cannot do that without thinking.
        "correct_minimal": False,
        # The master switch. Each model also gets a veto: the improved
        # rewrite reasons for ten thousand tokens before writing anything, and
        # only the high-quality quant holds a task together over that
        # distance -- see hardware.MODELS["rewrite_thinking"]. Setting this
        # False turns it off for every model; setting it True does not turn it
        # on for one that cannot use it.
        "correct_improved": True,
        "correct_effort": "medium",
    },
    "images": {
        # What the browser downscales to before uploading. 1600px on the long
        # edge is ~2,450 visual tokens per page and reads cleanly; going higher
        # costs context quadratically for handwriting that is already legible.
        "max_edge_px": 1600,
        "jpeg_quality": 0.85,
        "max_pages": 12,
        "max_bytes_per_page": 12000000,
    },
    "gpu": {"backend": "auto"},
    "paths": {"models_dir": "models"},
    "server": {"port": 8000},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """Read config.json, falling back to defaults for anything absent.

    A malformed config.json must not take the app down silently, but it also
    must not be papered over -- the operator edits this file by hand.
    """
    cfg = _DEFAULTS
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = _merge(_DEFAULTS, json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                "config.json could not be read (%s). Fix or delete it." % exc
            ) from exc
    return cfg


def resolve(rel: str) -> Path:
    """Resolve a config path (written relative to the app folder)."""
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / p)


# ---------------------------------------------------------------------------
# remembered choices
# ---------------------------------------------------------------------------

# The level dropdown comes back to whatever was marked last. A parent marks the
# same child's work for a year at a time, so defaulting to Primary 5 forever is
# a small daily annoyance.
#
# Kept in the app folder rather than in localStorage, deliberately. The folder
# is the whole application -- copy it to another machine, or to another user
# account on this one, and the setting comes with it; delete it and nothing is
# left behind in a browser profile. It also survives a switch from Edge to
# Chrome, which localStorage would not.
PREFERENCES_PATH = ROOT / "preferences.json"


def load_preferences() -> dict:
    try:
        with open(PREFERENCES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def remember(key: str, value) -> None:
    """Store one preference. Never raises: this is a convenience, not state."""
    try:
        prefs = load_preferences()
        if prefs.get(key) == value:
            return
        prefs[key] = value
        write_atomic(PREFERENCES_PATH, json.dumps(prefs, indent=2))
    except OSError:
        pass


def mmproj_path() -> Path:
    try:
        name = str(load_config()["llm"].get("mmproj") or "mmproj-F16.gguf")
    except RuntimeError:
        name = "mmproj-F16.gguf"
    p = Path(name)
    return p if p.is_absolute() else (models_dir() / p.name)


def child_env() -> dict:
    """Environment for spawned binaries.

    bin\\ must be on PATH so ggml-cuda.dll can find cublas64_12.dll and
    cudart64_12.dll. Without it llama.cpp silently falls back to the CPU.
    """
    env = dict(os.environ)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")

    # AMD on Vulkan crashes hard in ggml's KHR_coopmat path. ggml tests this
    # variable for existence, not value, so "1" and "0" both disable it.
    #
    # Guarded on probed(): detection spawns children of its own, and those
    # children ask for this environment. Consulting detection here before it
    # has run would recurse forever.
    from . import hardware

    if hardware.probed() and hardware.needs_coopmat_workaround():
        env["GGML_VK_DISABLE_COOPMAT"] = "1"
    return env


def missing_files() -> list[Path]:
    """Everything that must be present before a job can succeed.

    Checked at startup rather than at the first model call: a missing 16.5 GB
    GGUF should be a plain sentence on the first screen, not a failure four
    minutes into a job.
    """
    from . import hardware

    missing = []
    if not llama_server_path(backend()).exists():
        missing.append(llama_server_path(backend()))
    # The projector is not optional. Without it llama-server starts happily and
    # then rejects every image, which would surface as a marking failure rather
    # than a missing file.
    if not mmproj_path().exists():
        missing.append(mmproj_path())
    try:
        # Someone using their own llama-server on a port needs none of our
        # weights, and should not be held at the door by a health check
        # demanding 24 GB of downloads they will never load.
        if str(load_preferences().get("model") or "") == hardware.EXTERNAL_KEY:
            return missing
        requested = load_config()["llm"]["model"]
        if requested in ("auto", "", None):
            # Both ship, and the UI lets the user pick either, so both must be
            # present -- not just whichever this card would default to.
            wanted = [hardware.model_path(m["key"]) for m in hardware.MODELS]
        else:
            wanted = [resolve(requested)]
        missing += [p for p in wanted if not p.exists()]
    except Exception:  # noqa: BLE001 - a broken config is reported elsewhere
        pass
    return missing
