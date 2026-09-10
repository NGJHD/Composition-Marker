"""Per-machine timings, so the app can say how long something will take.

The rule carried over from the meeting app: before the first completed run on
this machine with this model, show no number at all. There is nothing honest to
say -- the same job varies by more than ten times between a 16 GB NVIDIA card
and an integrated GPU running on the processor -- and a guess that is five
times out is worse than admitting ignorance.

Four measurements, all in seconds:

    load_s              starting llama-server and loading the weights
    transcribe_page_s   one page image read back as text
    mark_s              the marking call
    correct_s           one corrected version of the composition

Keyed by model *and* backend, because they describe the hardware as much as the
work: the same weights on the same machine run at completely different speeds
through CUDA and through the CPU build, and a card swap must not be averaged
against the old one.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from . import config

PATH = config.ROOT / "calibration.json"

# How many samples to keep per measurement. Small on purpose: this is the most
# hardware-dependent number in the app, so a long history mostly describes
# machines that no longer exist. Five is enough for a median to be stable and
# short enough that an upgrade is reflected within a handful of runs.
KEEP = 5

STAGES = ("load", "transcribe_page", "mark", "correct")

# Only used to shape the progress bar before anything has been measured. They
# are not shown to the user as a time -- nothing is, until there is a real
# measurement -- they only decide how the bar divides itself up.
FALLBACK_SHARES = {
    "prepare": 2.0,
    "load": 14.0,
    "transcribe": 44.0,
    "mark": 40.0,
}

_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    try:
        config.write_atomic(PATH, json.dumps(data, indent=2))
    except OSError:
        pass       # a read-only folder must not fail a finished job


def _key(model_key: str) -> str:
    return "%s|%s" % (model_key or "unknown", config.backend())


def record(model_key: str, stage: str, seconds: float) -> None:
    """Add one sample. Called as each stage finishes, not at the end.

    Per stage rather than per job so that a cancelled or failed run still
    contributes what it did manage to measure -- the model load in particular,
    which is the same cost whatever happens afterwards.
    """
    if stage not in STAGES or seconds <= 0:
        return
    with _lock:
        data = _load()
        models = data.setdefault("models", {})
        entry = models.setdefault(_key(model_key), {})
        samples = entry.setdefault(stage, [])
        samples.append(round(float(seconds), 2))
        del samples[:-KEEP]
        _save(data)


def median(model_key: str, stage: str) -> float:
    """The measured cost of one stage, or 0.0 if nothing has been measured."""
    entry = _load().get("models", {}).get(_key(model_key), {})
    samples = sorted(entry.get(stage, []))
    if not samples:
        return 0.0
    mid = len(samples) // 2
    if len(samples) % 2:
        return float(samples[mid])
    return (float(samples[mid - 1]) + float(samples[mid])) / 2.0


def has_profile(model_key: str, stages=("load", "transcribe_page", "mark")) -> bool:
    return all(median(model_key, s) > 0 for s in stages)


def expected(model_key: str, pages: int, kind: str = "mark",
             corrections: int = 0) -> dict:
    """Expected seconds per stage for a whole job, or {} if not yet measured.

    `kind` is "mark" for a full run and "correct" for the on-demand rewrite,
    which loads the model again but skips straight to the correction call.
    """
    if kind == "correct":
        if not has_profile(model_key, ("load", "correct")):
            return {}
        return {
            "prepare": 1.0,
            "load": median(model_key, "load"),
            "correct": median(model_key, "correct") * max(corrections, 1),
        }
    if not has_profile(model_key):
        return {}
    return {
        "prepare": 1.0,
        "load": median(model_key, "load"),
        "transcribe": median(model_key, "transcribe_page") * max(pages, 1),
        "mark": median(model_key, "mark"),
    }


def total_minutes(model_key: str, pages: int, kind: str = "mark",
                  corrections: int = 0):
    """Whole-job estimate in whole minutes, or None if nothing is measured."""
    per_stage = expected(model_key, pages, kind, corrections)
    if not per_stage:
        return None
    return max(1, int(round(sum(per_stage.values()) / 60.0)))
