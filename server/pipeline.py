"""Stage orchestration: read the pages, mark the composition, write it out.

Two passes over the model, not one, and the reason is worth stating because a
single pass looks simpler and is worse.

Pass one reads each photographed page back as text, one call per page. Pass two
marks that text with no image attached at all. Marking straight from the
photographs would save a step and cost the two things the report depends on:
the model paraphrases a sentence it is quoting from pixels, so the "what was
written" column stops matching what was written; and every page shares one
context, so a six-page Secondary essay would arrive as ~15,000 visual tokens
before the rubric was even read.

Splitting them also means the transcript exists as a document in its own right,
which is what makes a misreading visible after the fact rather than invisible
inside a mark.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import calibration, config, levels, llm
from .jobs import Cancelled, Job, JobError

NO_TEXT = "NO_TEXT_FOUND"

LANGUAGE_NAMES = {"en": "English", "zh": "Chinese"}

# The feedback is in English even when the composition is not. That is the
# operator's decision and it is the right one for the audience: the adult
# reading the report is more comfortable in English, while the child's own
# sentences and every suggested rewrite must stay in the language they wrote
# in, or the correction is useless to them.
MARK_LANGUAGE_INSTRUCTION = {
    "en": "Write everything in English.",
    "zh": (
        "The composition is written in Chinese. Write all of your feedback, "
        "comments, headings and reasons in ENGLISH. But quote the child's own "
        "sentences in CHINESE, exactly as written, and write every improved or "
        "rewritten sentence in CHINESE. Never translate the child's writing "
        "into English, and never suggest an English replacement for a Chinese "
        "sentence."
    ),
}

CORRECT_LANGUAGE_INSTRUCTION = {
    "en": "Write the composition in English.",
    "zh": (
        "The composition is written in Chinese. Write your version in CHINESE. "
        "Do not translate it into English and do not mix English words in."
    ),
}

CORRECTION_LABELS = {
    "minimal": "Minimal correction",
    "improved": "Improved rewrite",
}


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def composition_name(job: Job) -> str:
    """The output folder's name: the topic if there is one, else the date.

    Kept short and free of anything Windows refuses, because this is also the
    prefix on every file inside the folder.
    """
    base = _BAD_CHARS.sub("", (job.topic or "").strip()).strip(" .")
    if not base:
        base = "Composition %s" % time.strftime("%Y-%m-%d %H%M")
    else:
        base = "%s - %s" % (base[:60].strip(), levels.label(job.level))
    return base[:90].strip() or "Composition"


def _paths(job: Job) -> dict:
    folder = config.composition_dir(job.name)
    return {
        "folder": folder,
        "report": folder / ("%s_marking.md" % job.name),
        "transcript": folder / ("%s_transcript.md" % job.name),
        "minimal": folder / ("%s_corrected.md" % job.name),
        "improved": folder / ("%s_improved.md" % job.name),
    }


# ---------------------------------------------------------------------------
# stage 1 - read the pages
# ---------------------------------------------------------------------------

def transcribe_pages(job: Job, server: llm.LlamaServer, cfg: dict) -> str:
    """Each page image read back as text, joined in reading order."""
    template = llm.load_prompt("transcribe")
    thinking = cfg["thinking"]
    max_tokens = int(cfg["llm"].get("max_transcribe_tokens", 2000))
    total = len(job.pages)
    chunks = []

    for index, page in enumerate(job.pages, start=1):
        job.check_cancelled()
        job.page_index = index - 1
        job.set_progress(
            (index - 1) / float(total),
            "Reading page %d of %d" % (index, total),
        )
        started = time.time()
        prompt = llm.fill(
            template,
            page_index=index,
            page_total=total,
            language_name=LANGUAGE_NAMES.get(job.language, "English"),
        )
        text = server.chat(
            [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": llm.data_url(page)}},
            ],
            thinking=bool(thinking.get("transcribe", False)),
            max_tokens=max_tokens,
            temperature=0.2,          # copying, not composing
            top_p=0.9,
            stage="transcribe",
        )
        elapsed = time.time() - started
        calibration.record(job.model_key, "transcribe_page", elapsed)
        job.page_index = index

        # Unwrapping is done here and not in _clean_page, which the correction
        # calls also use: a rewritten composition may separate its paragraphs
        # with a single newline, and joining those would collapse the whole
        # piece into one block.
        cleaned = _clean_page(text)
        if cleaned != NO_TEXT:
            cleaned = _unwrap(cleaned)
        if cleaned == NO_TEXT or not cleaned:
            # Say what the model actually replied. A page dropped in silence is
            # indistinguishable from a blank sheet, and the two need completely
            # different responses from whoever is looking at the log.
            job.log("page %d: no handwriting found (%.0fs); the model replied: %s"
                    % (index, elapsed, _snippet(text)))
            continue
        job.log("page %d: %s in %.0fs" % (index, _size_of(cleaned, job.language),
                                          elapsed))
        chunks.append(cleaned)

    if not chunks:
        raise JobError(
            "No handwriting could be read from those photographs. Check that "
            "the pages are the right way up, in focus, and well lit, then try "
            "again.",
            "every page returned %s" % NO_TEXT,
        )
    # A page break in the middle of a sentence is a page break, not a
    # paragraph: the model is told to end a page mid-sentence where the child
    # did, so joining on a blank line would invent paragraphing that the
    # marking would then reward or punish.
    return _join_pages(chunks)


def _snippet(text: str, limit: int = 220) -> str:
    """A one-line look at what a model said, for the log."""
    flat = " ".join((text or "").split())
    if not flat:
        return "(nothing at all)"
    return flat[:limit] + ("..." if len(flat) > limit else "")


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _clean_page(text: str) -> str:
    """Strip the wrappers a model reaches for even when told not to."""
    text = (text or "").strip()
    text = _FENCE.sub("", text).strip()
    if text.upper().startswith(NO_TEXT):
        return NO_TEXT
    # An occasional "Here is the text of the page:" survives the instruction.
    lines = text.splitlines()
    if lines and re.match(r"^(here (is|are)|the (page|text)|transcription)\b.*:$",
                          lines[0].strip(), re.IGNORECASE):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _size_of(text: str, language: str) -> str:
    """How much was read, in the unit the language is actually counted in.

    Chinese has no spaces, so `len(text.split())` returns the number of
    paragraphs -- a whole page came back as "5 words", which reads as a
    failure. Chinese compositions are measured in 字 and the log should say so.
    """
    if language == "zh":
        return "%d characters" % len(_CJK.findall(text))
    return "%d words" % len(text.split())


def _unwrap(text: str) -> str:
    """Join the lines of a paragraph back together, keeping the blank lines.

    The prompt asks for this and the model does not reliably do it: told to
    join a word that runs off the end of a ruled line, it copies the page's
    line breaks anyway. Left alone, a composition on ruled paper arrives as
    twenty one-line "paragraphs".

    That is not cosmetic. Organisation is 20 of the 100 marks and paragraphing
    is most of it, so invented line breaks would be marked as invented
    paragraphs. Doing it here rather than insisting harder in the prompt makes
    it deterministic, and costs nothing when the model gets it right.
    """
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        joined = lines[0]
        for line in lines[1:]:
            # A line ending in a hyphen is a word split across the line break.
            if joined.endswith("-"):
                joined = joined[:-1] + line
            elif _CJK.search(joined[-1:]) and _CJK.search(line[:1]):
                # Chinese does not put spaces between characters, and a space
                # inserted at every line break would be visible in the quoted
                # sentences and in both corrected versions.
                joined += line
            else:
                joined += " " + line
        out.append(joined)
    return "\n\n".join(out)


# CJK ideographs plus the full-width punctuation that ends a Chinese sentence.
_CJK = re.compile("[　-鿿＀-￯]")


def _join_pages(chunks: list) -> str:
    out = chunks[0]
    for chunk in chunks[1:]:
        # If the previous page ended without terminal punctuation the sentence
        # runs on, so the pages are joined with a space rather than a break.
        if re.search(r"[.!?\"'。！？”’]\s*$", out):
            out += "\n\n" + chunk
        else:
            out += " " + chunk
    return out.strip()


# ---------------------------------------------------------------------------
# stage 2 - mark it
# ---------------------------------------------------------------------------

def _topic_block(job: Job) -> str:
    if not (job.topic or "").strip():
        return (
            "The topic the child was given is not known. Judge relevance by "
            "whether the composition holds together and stays on one subject, "
            "and do not speculate about what the question might have been."
        )
    return (
        "The topic the child was given: %s\n"
        "Relevance to that topic is part of the Content mark."
        % job.topic.strip()
    )


def mark(job: Job, server: llm.LlamaServer, cfg: dict, composition: str) -> str:
    job.set_stage("mark")
    started = time.time()
    thinking = cfg["thinking"]
    prompt = llm.fill(
        llm.load_prompt("mark"),
        level_block=levels.expectations_block(job.level, job.language),
        topic_block=_topic_block(job),
        marks_table=levels.marks_table(job.language),
        bands=levels.BANDS_EN,
        language_instruction=MARK_LANGUAGE_INSTRUCTION.get(
            job.language, MARK_LANGUAGE_INSTRUCTION["en"]),
        title=job.name,
        composition=composition,
    )
    report = server.chat(
        prompt,
        thinking=bool(thinking.get("mark", True)),
        effort=str(thinking.get("mark_effort", "medium")),
        max_tokens=int(cfg["llm"].get("max_mark_tokens", 12000)),
        temperature=0.7,
        top_p=0.8,
        stage="mark",
    )
    calibration.record(job.model_key, "mark", time.time() - started)
    return report.strip()


SCORE_RE = re.compile(r"overall\s*[:\-]?\s*\**\s*(\d{1,3})\s*/\s*100", re.IGNORECASE)


def parse_score(report: str):
    """The headline mark, for the result page. None if it cannot be found.

    Deliberately forgiving, and deliberately not load-bearing: the report is
    the document, and a missing number costs a badge on the page rather than a
    failed job.
    """
    m = SCORE_RE.search(report or "")
    if not m:
        return None
    value = int(m.group(1))
    return value if 0 <= value <= 100 else None


# ---------------------------------------------------------------------------
# stage 3 - the corrected versions, generated on demand
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^##\s+(What to work on|Sentences to improve)\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL)


def improvements_from_report(job: Job) -> str:
    """The changes the marking call already decided on, for the rewrite to apply.

    The improved rewrite is the hardest thing asked of the model: decide what is
    weak in a piece of writing, then fix it, at a fixed level, without making it
    worse. Asked cold it either hands the composition back or -- worse -- runs
    out of ideas and simplifies it.

    But that judgement has already been made, minutes earlier, by the marking
    call: with thinking on, it named the weak sentences and wrote a stronger
    version of each. Handing those back turns an open-ended writing task into a
    mechanical one, which is what the small model is actually good at.

    Best effort. A missing or unparseable report costs the extra grounding and
    nothing else -- the prompt still stands on its own.
    """
    path = _paths(job)["report"]
    try:
        report = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = [m.group(0).strip() for m in _SECTION_RE.finditer(report)]
    if not found:
        return ""
    return (
        "IMPROVEMENTS ALREADY IDENTIFIED IN THIS COMPOSITION\n\n"
        "These were worked out when the composition was marked. Apply every "
        "one of them, and make the same kind of change wherever else it "
        "applies.\n\n" + "\n\n".join(found)
    )


def _word_cap(composition: str, language: str, headroom: float = 1.05) -> tuple:
    """(what the original is, what the rewrite may not exceed).

    The cap is counted here and stated in the prompt because the model cannot
    count and will not stop on its own: asked to reimagine a composition it
    will happily return half as much again.
    """
    import math

    if language == "zh":
        n = len(_CJK.findall(composition))
        return ("%d characters" % n, "%d characters" % math.ceil(n * headroom))
    n = len(composition.split())
    return ("%d words" % n, "%d words" % math.ceil(n * headroom))


def correct(job: Job, server: llm.LlamaServer, cfg: dict, composition: str,
            kind: str) -> str:
    original_words, max_words = _word_cap(composition, job.language)
    prompt = llm.fill(
        llm.load_prompt("correct_%s" % kind),
        level_block=levels.expectations_block(job.level, job.language),
        # The improved rewrite aims one level above the child's own.
        target_block=levels.target_block(job.level, job.language),
        original_words=original_words,
        max_words=max_words,
        topic_block=_topic_block(job),
        # Both corrections get the marking's findings. The improved rewrite
        # applies all of them; the minimal correction is told in its own prompt
        # to take only the errors and ignore the stylistic suggestions. Without
        # it, IQ2_XXS was observed correcting the first paragraph of a 380-word
        # script and then copying the rest out unchanged.
        improvements=improvements_from_report(job),
        language_instruction=CORRECT_LANGUAGE_INSTRUCTION.get(
            job.language, CORRECT_LANGUAGE_INSTRUCTION["en"]),
        composition=composition,
    )
    started = time.time()
    # The two corrections want opposite things from the model. The minimal one
    # is mechanical -- find the errors, fix them, touch nothing else -- and
    # reasoning buys it nothing. The improved one has to hold a plot, a level,
    # a word ceiling and a paragraph structure in mind at once and invent new
    # material inside all four, which it cannot do without thinking.
    thinking = cfg["thinking"]
    think = bool(thinking.get("correct_%s" % kind,
                              thinking.get("correct", False)))
    text = server.chat(
        prompt,
        thinking=think,
        effort=str(thinking.get("correct_effort", "medium")),
        max_tokens=int(cfg["llm"].get(
            "max_improved_tokens" if kind == "improved" else "max_correct_tokens",
            8000 if kind == "improved" else 4000)),
        temperature=0.7,
        top_p=0.8,
        stage="correct-%s" % kind,
    )
    calibration.record(job.model_key, "correct", time.time() - started)
    return _keep_title(composition, _clean_page(text))


def _keep_title(original: str, rewritten: str) -> str:
    """Put the composition's title back if the model dropped it.

    Both prompts say to keep it and both models drop it perhaps half the time,
    reading the first line as a heading to be stripped rather than as part of
    the composition. Losing it matters more than it looks: the title is often
    the topic, and a corrected version handed back without one no longer
    matches the page it is meant to sit beside.

    Only fires on something that actually looks like a title -- one short line,
    no sentence-ending punctuation -- and only when the rewrite does not
    already open with it.
    """
    lines = [ln.strip() for ln in original.strip().splitlines() if ln.strip()]
    if not lines or not rewritten:
        return rewritten
    title = lines[0]
    if len(title) > 80 or len(title.split()) > 12:
        return rewritten
    if re.search(r"[.!?。！？]\s*$", title):
        return rewritten           # a sentence, not a title

    first = rewritten.strip().splitlines()[0].strip()
    # A model that kept the title sometimes recapitalises it, so compare
    # loosely before deciding it is missing.
    if first.lower().strip(" .") == title.lower().strip(" ."):
        return rewritten
    return "%s\n\n%s" % (title, rewritten.strip())


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def write_record(job: Job) -> None:
    """The sidecar beside the documents: level, language, topic, score, model.

    Written by the pipeline as the last act of a run, not by the endpoint that
    serves the result page. The history list reads it to caption a composition,
    and a run whose result page was never opened -- a tab closed at the wrong
    moment -- would otherwise come back with no level and no mark against work
    that was marked perfectly well.
    """
    import json

    record = {
        "name": job.name,
        "level": job.level,
        "language": job.language,
        "topic": job.topic,
        "model": job.model_key,
        "score": job.score,
        "marked_at": time.time(),
    }
    try:
        config.write_atomic(
            config.composition_dir(job.name) / "composition.json",
            json.dumps(record, indent=2, ensure_ascii=False),
        )
    except OSError:
        pass          # a sidecar is a convenience; the documents are the record


def _header(job: Job, length_label: str = "") -> str:
    bits = ["**Level:** Singapore %s (age %d)"
            % (levels.label(job.level), levels.age(job.level))]
    if (job.topic or "").strip():
        bits.append("**Topic:** %s" % job.topic.strip())
    bits.append("**Language:** %s" % LANGUAGE_NAMES.get(job.language, "English"))
    # Counted by the app, not asked of the model: counting is arithmetic, and a
    # model asked to count words will guess. It sits in the header rather than
    # under the criteria table because it is a fact about the composition, like
    # the level and the language, not part of the judgement.
    # Each document reports its OWN length. On the improved rewrite that is the
    # rewrite's count, not the original's: the prompt requires it to come back
    # no shorter than the composition it improves, and quoting the original's
    # figure on it would hide exactly the thing worth checking.
    label = length_label or job.length_label
    if label:
        bits.append("**Word Count:** %s" % label)
    bits.append("**Marked:** %s" % time.strftime("%d %b %Y, %H:%M"))
    return "\n\n".join(bits)


def write_transcript(job: Job, text: str) -> Path:
    paths = _paths(job)
    body = "# %s\n\n%s\n\n---\n\n%s\n" % (
        job.name,
        _header(job),
        text,
    )
    config.write_atomic(paths["transcript"], body)
    return paths["transcript"]


# The length is a plain fact stated in the header block (see _header), not a
# judgement inserted into the report. The model still weighs length against the
# level in its Content and Language comments -- the expected range is in the
# prompt -- so nothing is lost by keeping the header line bare.


def write_report(job: Job, report: str) -> Path:
    paths = _paths(job)
    # The model writes its own "# Marking - ..." heading. The metadata block is
    # inserted under it so the document identifies itself once it has been
    # copied out of the folder.
    lines = report.splitlines()
    if lines and lines[0].startswith("# "):
        body = lines[0] + "\n\n" + _header(job) + "\n\n" + "\n".join(lines[1:]).lstrip()
    else:
        body = "# Marking - %s\n\n%s\n\n%s" % (job.name, _header(job), report)
    config.write_atomic(paths["report"], body.rstrip() + "\n")
    return paths["report"]


def write_correction(job: Job, kind: str, text: str) -> Path:
    paths = _paths(job)
    path = paths["minimal" if kind == "minimal" else "improved"]
    body = "# %s - %s\n\n%s\n\n---\n\n%s\n" % (
        CORRECTION_LABELS[kind], job.name,
        _header(job, _size_of(text, job.language)), text)
    config.write_atomic(path, body)
    return path


def _clear_stale_corrections(job: Job) -> None:
    """Delete corrected versions belonging to a previous transcript.

    Two compositions with the same topic and level share an output folder, and
    the second run overwrites the transcript and the report but not the
    corrections -- which are then a corrected version of somebody else's
    writing, sitting under the right name with the right date.

    The corrections are always regenerable from the transcript, so deleting
    them is cheap; leaving a mismatched one is not.
    """
    paths = _paths(job)
    for key in ("minimal", "improved"):
        try:
            if paths[key].exists():
                paths[key].unlink()
                job.log("removed %s, left over from an earlier marking"
                        % paths[key].name)
        except OSError:
            pass


def read_transcript(job: Job) -> str:
    """The composition text a finished job left behind.

    The corrected versions are generated later, from the same text the marking
    was done on -- never from the photographs again. Re-reading the images
    would produce a slightly different transcription, and a "corrected version"
    that corrects sentences the report never mentioned is worse than none.
    """
    path = _paths(job)["transcript"]
    if not path.exists():
        raise JobError(
            "The composition this belongs to is no longer in the output folder.",
            "missing %s" % path,
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = text.split("\n---\n", 1)
    return (parts[1] if len(parts) == 2 else text).strip()


# ---------------------------------------------------------------------------
# the two entry points
# ---------------------------------------------------------------------------

def run(job: Job) -> None:
    """Photographs in, marked report out. The whole of the Mark button."""
    cfg = config.load_config()
    job.name = composition_name(job)
    job.set_stage("prepare")
    job.log("marking %d page(s) at %s level, %s"
            % (len(job.pages), levels.label(job.level),
               LANGUAGE_NAMES.get(job.language, "English")))

    server = llm.LlamaServer(job, cfg)
    job.llm_server = server
    try:
        job.set_stage("load")
        server.start()

        job.set_stage("transcribe")
        composition = transcribe_pages(job, server, cfg)
        # Set before the first document is written: every one of them carries
        # the header block, and the transcript is written first.
        job.length_label = _size_of(composition, job.language)
        job.transcript_path = write_transcript(job, composition)
        _clear_stale_corrections(job)
        job.log("transcript: %s" % _size_of(composition, job.language))

        report = mark(job, server, cfg, composition)
        job.score = parse_score(report)
        job.report_path = write_report(job, report)
        job.log("marked: %s"
                % ("%d / 100" % job.score if job.score is not None
                   else "score not stated"))
        write_record(job)
    finally:
        job.llm_server = None
        server.stop()


def run_correction(job: Job) -> None:
    """The Generate and Download button on the finished page."""
    cfg = config.load_config()
    job.set_stage("prepare")
    composition = read_transcript(job)
    # A correction started from the history list has a job rebuilt from disk,
    # so the count has to come from the transcript rather than from the marking
    # run that is long over.
    job.length_label = _size_of(composition, job.language)
    kinds = (["minimal", "improved"] if job.correction == "both"
             else [job.correction])

    server = llm.LlamaServer(job, cfg)
    job.llm_server = server
    try:
        job.set_stage("load")
        server.start()
        job.set_stage("correct")
        written = []
        for position, kind in enumerate(kinds):
            job.check_cancelled()
            job.set_progress(
                position / float(len(kinds)),
                "Writing the %s" % CORRECTION_LABELS[kind].lower(),
            )
            text = correct(job, server, cfg, composition, kind)
            if not text:
                raise JobError(
                    "The corrected version came back empty. Try again.",
                    "empty completion for %s" % kind,
                )
            written.append(write_correction(job, kind, text))
            job.log("wrote %s" % written[-1].name)
        job.corrected_paths = written
    finally:
        job.llm_server = None
        server.stop()
