# CLAUDE.md — Offline Composition Marker and Feedback

You are building a **fully offline Windows desktop application** that takes photographs
of a child's handwritten composition and returns a mark out of 100, feedback, a list of
sentences worth improving with rewrites, and a downloadable corrected version.

Read this entire file before writing any code. Every design decision here was made
deliberately. If you think one is wrong, say so explicitly in your response — do not
silently substitute your own approach.

This app is a sibling of the Meeting Summariser and reuses its hardware detection, job
plumbing and packaging almost unchanged. Where a decision was carried over, that is said;
where it was changed, the reason is given. `BUILD_NOTES.md` records what was measured.

---

## 0. NON-NEGOTIABLE CONSTRAINTS

1. **No internet access at runtime.** Not for models, not for fonts, not for CDN scripts,
   not for telemetry. The app must work with the network adapter physically disabled.

   **The one exception, added at the operator's instruction, is the Check for updates
   button in the About overlay.** It was originally specified not to exist, and this
   paragraph said "no outbound request, ever"; that is no longer true and the reversal is
   recorded rather than quietly applied. The exception is narrow and must stay narrow:

   - Nothing is requested unless the user presses the button. No check on launch, no
     periodic poll, no telemetry, no "phone home" of any kind.
   - The only hosts contacted are `api.github.com` and `github.com`, and the only URL
     downloaded is a release asset whose prefix is verified against this app's own
     repository (`updater.is_our_asset_url`).
   - Every other code path still talks to nothing but `127.0.0.1`. A machine whose user
     never presses the button never resolves a hostname, so acceptance test 10 — a full
     marking run with the adapter disabled — remains true and remains required.
   - The child's work is never part of any request. The update path sends nothing but an
     HTTP GET; no composition, no photograph, no report, no identifier.
2. **No installation steps.** The end user unzips a folder and double-clicks `run.bat`.
   No Python installer, no CUDA toolkit, no pip.
3. **No PyTorch.** Anywhere. This is the constraint that shapes the whole architecture.
   All inference happens in pre-built native binaries called over HTTP.
4. **No OCR engine.** Handwriting is read by the same multimodal model that does the
   marking, through llama.cpp's vision path. Tesseract and its relatives are poor at
   children's handwriting and would add a large non-portable dependency to do a worse job.
5. **Non-technical end user.** A parent or a tutor. They will not read a README, edit a
   config file, or understand an error message containing a stack trace.
6. **Windows 11.** NVIDIA, AMD and Intel are all supported — CUDA where there is an
   NVIDIA card, Vulkan for the rest, the CPU build as the last resort and on unified
   memory. The only external dependency is an installed display driver.
7. **This is a child's schoolwork.** The photographs, the handwriting and the marking
   never leave the machine. Nothing is uploaded, logged remotely, or retained anywhere
   except the app's own `output\` folder.

### Scope

One composition per run. Between one and twelve photographed pages, in an order the user
arranges. Two languages: **English and Chinese**. Thirteen Singapore education levels,
Primary 1 through JC2.

**What it produces:** a marked report, the transcribed composition, and — on demand — a
minimally corrected version and an improved rewrite.

**What it is not:** a batch marker, a class gradebook, a plagiarism checker, or a general
essay assistant.

---

## 1. VERIFY BEFORE YOU CODE

The command-line flags in this document are drawn from a recent llama.cpp release. **That
project renames and removes flags frequently.**

Before writing the orchestration code, run:

```
bin\llama-cuda\llama-server.exe --help
```

For every flag this document specifies, confirm it exists with that name in that build.
If a flag is absent or renamed, find the equivalent and **use it** — then add a line to
`BUILD_NOTES.md` recording what you changed and why. Do not silently drop a flag, and do
not guess.

In particular, confirm before building anything else:

- `--mmproj` exists, and the shipped `llama-server` was built with multimodal support.
- The OpenAI-compatible endpoint accepts an `image_url` content part with a `data:` URL.
- `chat_template_kwargs.enable_thinking` still controls thinking on this model.

If a binary or model file is missing, stop and report which one rather than writing code
that assumes it.

---

## 2. ARCHITECTURE

```
Photographs of the pages
 └─> browser: EXIF rotate, downscale to 1600px, JPEG          (no server-side imaging)
      └─> one POST per page, streamed to temp\pages\
           └─> llama-server + mmproj
                ├─> TRANSCRIBE   one call per page, image in, verbatim text out
                │     └─> output\<name>\<name>_transcript.md
                └─> MARK         one call, text only, thinking on
                      └─> output\<name>\<name>_marking.md
                           └─> [on demand] CORRECT  one or two calls, text only
                                 └─> <name>_corrected.md / <name>_improved.md
```

### 2.1 Why two passes and not one

Sending the page images straight to the marking call is simpler and is wrong, for three
reasons:

1. **Quoting.** The report must list the child's sentences exactly as written. A model
   quoting from pixels paraphrases; the "what was written" column then does not match
   what was written, and the child is corrected for something they did not do.
2. **Context.** Each page at 1600px is roughly 2,450 visual tokens. Six pages is ~15,000
   tokens before the rubric is read. One page per call keeps every call under 5k.
3. **The transcript is evidence.** Kept as a document, a misreading is visible after the
   fact. Folded into a single call it is invisible, and indistinguishable from a mark.

The corrected versions are generated from **the stored transcript, never from the images
again**. Re-reading would produce a slightly different transcription, and a corrected
version that fixes sentences the report never mentioned is worse than none.

### 2.2 Backends

Every binary ships once per backend and the app picks at startup:

```
bin\llama-cuda\    bin\llama-vulkan\    bin\llama-cpu\
```

The rules are the Meeting Summariser's, measured there and carried over unchanged:

| Machine | Backend | `--n-gpu-layers` | `--override-tensor` |
|---|---|---|---|
| NVIDIA, dedicated | cuda | `99` | FFN blocks that do not fit, computed from VRAM |
| AMD / Intel, dedicated | vulkan | `99` | as above |
| Unified memory (iGPU) | **cpu** | `0` | none |
| No GPU | cpu | `0` | none |

**On unified memory the language model takes the CPU build outright**, not the Vulkan
build with layers switched off. With the Vulkan backend registered the scheduler still
routes prefill to the integrated GPU, which measured 3.3x slower overall on Intel.

**AMD on Vulkan needs `GGML_VK_DISABLE_COOPMAT`.** The KHR_coopmat path hard-crashes with
`0xC0000409` and no message. Scoped to AMD only.

---

## 3. DIRECTORY LAYOUT

```
CompositionMarker\
  run.bat
  DOWNLOAD_MODELS.bat
  config.json
  CLAUDE.md                   <- this file
  BUILD_NOTES.md              <- you write this; flag verification + measurements
  runtime\                    <- standalone CPython, wheels vendored
  bin\
    cudart64_12.dll ...       <- CUDA runtime, shared
    llama-cuda\ llama-vulkan\ llama-cpu\
  models\
    Qwen3.8-27B-UD-Q4_K_M.gguf    <- High Quality, used at 15GB VRAM and above
    Qwen3.8-27B-UD-IQ2_XXS.gguf   <- Low Quality, used below that
    mmproj-F16.gguf               <- the vision projector; ships in the zip
  prompts\
    _shared.txt  transcribe.txt  mark.txt
    correct_minimal.txt  correct_improved.txt
  web\
    index.html  app.js  style.css
  server\
    main.py            <- FastAPI app, routes, SSE, page uploads
    open_browser.py    <- waits for the server, then opens the browser
    config.py          <- paths, config, child environment
    hardware.py        <- GPU detection, model choice, tensor placement
    levels.py          <- the thirteen levels and what each one expects
    calibration.py     <- per-machine timings; the estimate and the bar
    llm.py             <- llama-server lifecycle, chat and vision calls
    pipeline.py        <- stage orchestration, documents
    jobs.py            <- job state, cancellation, cleanup
    version.py         <- app name, author, version, repo. Nothing else
  temp\
    pages\             <- the uploaded photographs; emptied every run
  output\
    <composition name>\
      <name>_marking.md
      <name>_transcript.md
      <name>_corrected.md
      <name>_improved.md
      composition.json      <- level, language, topic, score, model
```

Filenames keep the composition prefix inside the folder, so a document still identifies
itself once copied out of it.

---

## 4. CONFIGURATION

All tunables live in `config.json`. **There is no settings screen in the UI.** The
operator edits this file in Notepad; the end user never sees it.

Everything marked `"auto"` is **detected per machine, not guessed**, and an explicit value
always wins.

Three values differ from the Meeting Summariser and the reasons matter:

- **`llm.ctx_size` is 16384, not 32768.** Nothing here is chunked. The largest single
  call is one page image plus its prompt (~5k), or the whole composition with the rubric
  and the model's thinking (~8k). Halving the context returns ~0.5 GB of KV cache, which
  is roughly what the vision projector then occupies.
- **`llm.mmproj`** names the projector. It is not optional: without it llama-server starts
  happily, accepts an image part, and answers about a page it never saw.
- **`paths.models_dir`** may point somewhere else entirely, so a machine that already
  holds these GGUFs for another app need not keep a second copy. `run.bat` therefore
  **warns** about missing weights rather than refusing to start — only the application
  can resolve the real path, and its first screen names anything missing precisely.

---

## 5. PAGE HANDLING

**Resize in the browser, not on the server.** A phone photograph is 4000x3000 and 4 MB.
Downscaling on a canvas keeps `runtime\` free of an imaging library, and matters more for
context than for bandwidth: the vision encoder charges roughly one token per 28x28
pixels, so a full-resolution page costs ~15,000 tokens to read handwriting that is
perfectly legible at 1600.

- `createImageBitmap(file, { imageOrientation: "from-image" })` applies the EXIF
  rotation. **Without it every portrait photograph arrives on its side** and the
  handwriting cannot be read at all. Fall back to an `<img>`, which has applied EXIF
  orientation itself since Chrome 81.
- Long edge 1600px, JPEG quality 0.85.
- **HEIC cannot be decoded by Windows browsers.** Detect the failure and say so in
  words, naming the phone setting that fixes it. Do not fail silently.
- Upload one page at a time as a raw body, in order. Multipart would buffer each image a
  second time, and there is nothing a filename would tell us — the order is the order the
  user arranged, and it arrives as an index.
- Reject anything that is not JPEG or PNG by magic bytes, not by extension.

Twelve pages is the maximum. That is a JC2 essay with room to spare.

---

## 6. STAGE 1 — READING THE HANDWRITING

One call per page, image attached, **thinking off**, temperature 0.2. This is copying,
not composing.

The prompt is the load-bearing part. It must insist on:

- Verbatim text, **including every spelling and grammar mistake**. A single silently
  corrected word makes the marking wrong.
- Paragraph breaks preserved; line breaks within a paragraph joined up.
- Crossed-out words omitted; carets honoured.
- `[?]` for an unreadable word, `guess[?]` for a doubtful one, never a guessed sentence.
- `NO_TEXT_FOUND` for a blank page or a photograph of something else.

Strip code fences and the occasional "Here is the text of the page:" — models emit both
despite being told not to.

**Keep this prompt short.** It grew to four times its length one well-meant rule at
a time, and a 2-bit model cannot hold a long instruction: it began writing preambles,
arguing with itself in the output, and calling a worksheet page blank. Every rule
added here costs reliability on the rules already in it. See BUILD_NOTES section 8a.

**When a page is rejected, log what the model actually replied.** A page dropped in
silence is indistinguishable from a blank sheet, and the two need opposite responses.

**Joining pages:** if the previous page ended without terminal punctuation, the sentence
runs on and the pages are joined with a space. Otherwise with a blank line. Joining
everything on blank lines invents paragraphing that the marking would then reward.

If every page returns `NO_TEXT_FOUND`, fail with advice about lighting and focus — not
with an empty report.

---

## 7. STAGE 2 — MARKING

One call, **text only, thinking on at `medium` effort**. This is the one place in the app
where reasoning earns its cost: it is a single call, and it is a judgement.

### 7.1 The level is the whole problem

The model knows the phrase "Primary 4" and it knows what a ten-year-old is, but neither
holds a standard steady. Asked to mark a P4 script cold it drifts towards adult writing
and scores every child in the fifties.

**So send all three: the level, the age, and an explicit written expectation for that
level.** `server\levels.py` holds the third, per level and per language, in four parts:

- the usual form at that level (narrative, recount, argumentative...)
- the expected length, in words or 字
- what a strong script at that level actually does
- **what is not yet expected at that level** — this one does the most work, because it is
  what stops the model deducting for absent complex clauses in a Primary 3 script

### 7.2 The score

100 marks, in the MOE style: **Content 40, Language 40, Organisation 20**, each with a
band from 1 to 5, and every band descriptor is explicitly relative to the level.

This does not claim to reproduce an official MOE marking scheme, and must not say that it
does. What matters is that the criteria are fixed and written down, so two runs of the
same script are marked against the same thing.

### 7.3 Output shape

Markdown, in a fixed skeleton: Score (a bold `Overall: NN / 100` and a criteria table),
What went well, What to work on, **Sentences to improve**, **Notes on the reading**, and
an Overall comment addressed to the child.

**Sentences to improve is exhaustive, not a top six.** Every sentence that would be
meaningfully better rewritten, from the first to the last — a long composition with many
weak sentences should produce a long table. And not only errors: a sentence belongs in it
if it is clumsy, does not flow from the one before, repeats a shape, tells a feeling
instead of showing it, or is vague where a detail would serve. A sentence is left out
only if not a word of it would change.

**The word count is counted by the app, never asked of the model.** Counting is
arithmetic, and a model asked to count words will guess. It goes in the header block of
every document, between Language and Marked, as a bare fact:

```
**Language:** English
**Word Count:** 380 words
**Marked:** 11 Sep 2026, 02:16
```

Bare on purpose — no comparison against the expected range. It belongs with the level and
the language, which are facts about the composition, not with the judgement. The model
still weighs length against the level in its Content and Language comments, because the
expected range is in the prompt.

Chinese reports characters rather than words, since that is what 字数 means and what the
level expectations are written in.

Parse the headline score with a forgiving regex and treat it as **not load-bearing**: a
missing number costs a badge on the result page, never a failed job. The report is the
document.

**"Notes on the reading" exists because there is no transcript review step.** The user
chose a fully automatic flow — photographs in, report out — so the one mitigation left is
to have the model flag words that look misread rather than mis-written, and to show the
transcript on its own tab beside the marking.

### 7.3a The marking must not judge what the transcript cannot carry

Paragraph indentation does not survive the reading (§6, and BUILD_NOTES §8a), so a
properly paragraphed composition can reach the marking as one unbroken block.

**The prompt therefore forbids commenting on or deducting for paragraphing, layout or
neatness**, and `levels.marks_table` describes Organisation without mentioning
paragraphs — it is judged on the order of events, the opening, the ending and whether
one idea leads to the next, all of which are in the words.

This was found the worst way: a report asked why the whole composition was in one block,
on a page that was correctly paragraphed. Marking a child down for something they did on
paper and the machine failed to see is the single most damaging thing this app can do.

### 7.4 Rewrites must stay at the level

A Primary 3 sentence improved into a Secondary 3 sentence is not a lesson, it is a
replacement. Say so in the prompt, in those words.

---

## 8. STAGE 3 — THE CORRECTED VERSIONS

Generated **on demand** from the result page, never as part of the marking run: it is one
or two more model calls and most users will not want to wait for them every time.

A dropdown beside a **Generate** button offers *Minimal correction*, *Improved rewrite*,
or both — **defaulting to both**. Both writes two files, and the composition's folder
opens when it finishes.

- **Minimal correction**: spelling, grammar, punctuation, capitalisation, and a wrong
  word replaced with the one clearly meant. Nothing else. Same ideas, same words wherever
  they were right, same length, same paragraphing, same title. The test: the child must
  recognise it as their own composition with the mistakes taken out.
- **Improved rewrite**: the same story, reimagined and retold. The topic and the broad
  strokes of the plot are kept — same situation, same main events in the same order, same
  outcome — and everything else may change. Details may be replaced outright and new
  moments or complications invented where they make the piece flow.

  It aims **one level above the child's own** (`levels.NEXT_LEVEL`): a Primary 4 script is
  rewritten to Primary 5, to show the way ahead rather than what they nearly managed.
  Secondary 5 is skipped going up — it is the N-level year, not a step past Secondary 4 —
  so Secondary 4 and Secondary 5 both aim at JC1, and JC2 aims at a capable adult.

  **The word count is capped at 105% of the original**, counted by the app and stated in
  the prompt as a hard ceiling, because a model asked to reimagine will otherwise return
  half as much again. Better in the same room, not longer.

  It must be paragraphed, with blank lines, and it runs with **thinking on** — it has to
  hold a plot, a target level, a word ceiling and a paragraph structure in mind at once
  while inventing new material inside all four.

  **It is not given the marking report.** The minimal correction is, and needs it; the
  rewrite is not, because a list of specific sentence fixes anchors it to the original's
  sentences and is exactly what turns a reimagination back into a polish.

**The two must not converge, and left to itself the model lets them.** Where a
composition is already written well above the level it was submitted at — a strong
Primary 5 piece marked at Primary 2, say — "rewrite this to a good standard for the
level" and "correct the mistakes" become the same instruction, and the two documents come
back near-identical or, once observed, byte-identical below the title.

So the improved prompt states outright that the corrected version already exists and that
its own output must differ by more than corrected errors, then gives **six mandatory,
checkable changes** rather than a list of prohibitions. That distinction matters: a prompt
built of "never do X" has *inaction* as its safest compliance, and an earlier version of
this one duly handed the composition back untouched.

Two hard rules sit under it. The level is a **floor, not a ceiling** — never write down to
it, never replace a word the child already used correctly with a simpler one, never come
back shorter. And **the rewrite is grounded in the marking report**: the "What to work on"
and "Sentences to improve" sections are lifted off disk and handed to the rewrite as
changes to apply. That judgement was already made by a thinking-enabled call minutes
earlier, and reusing it turns an open-ended writing task into a mechanical one — which is
what the small model can actually do.

**The title is restored in code, not asked for in the prompt.** Both models drop it about
half the time, reading the first line as a heading to strip. Where a requirement is
mechanical, do it mechanically.

Both are text-only calls with thinking off.

---

## 9. LANGUAGE

English and Chinese. The composition language is chosen by the user; there is no
detection, because a wrong guess would mark the piece against the wrong rubric.

**For a Chinese composition the feedback is written in English, and the child's quoted
sentences and every rewrite stay in Chinese.** That is deliberate and was the operator's
decision: the adult reading the report is more comfortable in English, while a correction
the child cannot read is useless to them. Never translate the child's writing into
English, and never offer an English replacement for a Chinese sentence.

The Chinese expectations are keyed to 字数, not word counts, and use the Chinese criteria
names (内容 / 语文 / 组织).

---

## 10. THE LLM

### 10.1 Server lifecycle

```
bin\llama-cuda\llama-server.exe ^
  -m models\Qwen3.8-27B-UD-IQ2_XXS.gguf ^
  --mmproj models\mmproj-F16.gguf ^
  --ctx-size 16384 ^
  --flash-attn on ^
  --cache-type-k q8_0 --cache-type-v q8_0 ^
  --jinja ^
  --batch-size 512 --ubatch-size 512 ^
  --parallel 1 ^
  --host 127.0.0.1 --port 8080 ^
  --no-webui ^
  --n-gpu-layers 99 --override-tensor "blk\.(NN|...|63)\.ffn_.*=CPU"
```

The binary folder, the last line, and `--no-mmproj-offload` are decided at runtime.
`--jinja` is required — without it llama.cpp uses a generic chat template and
`chat_template_kwargs` has nothing to pass through to.

Poll `GET /health` until ready. First load off a cold disk takes 60–120 seconds; surface
it as *"Loading the language model…"* with a bar that keeps moving, or it looks hung.

**Shut the server down when the job finishes.** Do not leave 16 GB of VRAM allocated.

The vision projector follows the weights: when the model is on the processor, pass
`--no-mmproj-offload` rather than shipping every image across to an integrated GPU that
shares the same memory bus.

### 10.2 Sampling

| Call | thinking | temperature | top_p | why |
|---|---|---|---|---|
| transcribe | off | 0.2 | 0.9 | copying, not composing |
| mark | **on**, `medium` | 0.7 | 0.8 | one call, and it is a judgement |
| correct, minimal | off | 0.7 | 0.8 | the shape of the answer is already fixed |
| correct, improved | **on**, `medium` | 0.7 | 0.8 | it holds a plot, a level and a word ceiling at once — see §8 |

The two corrections are listed separately because they want opposite things. The minimal
one is mechanical and reasoning buys it nothing. The improved one needs it — **thinking is
what holds the word ceiling**, measured: with it, three runs landed within 5% of the
original; without it, the rewrite came back 15% short twice and over the 105% ceiling
once, breaking §8 at both ends.

It needs room for it, and **the reasoning is unbounded.** `max_tokens` covers the
reasoning as well as the answer; at 8,000 the cap landed mid-thought and the call returned
an empty `content` — a total failure rather than a truncation. `max_improved_tokens` is
12,000, which is better and is not a cure: measured reasoning ran from 9,815 to over
12,000 tokens on one script, so some calls still fall through to the retry. It cannot be
raised to the context ceiling either, because the prompt carries the whole composition.
`reasoning_effort` does not help: `low` spent the same 8,000 and also returned nothing.
See BUILD_NOTES §7.6g, which sets out the two real fixes.

`reasoning_effort` accepts **`xhigh`, `medium`, `low` only**. Anything else raises inside
the Jinja template and surfaces as HTTP 500, so validate before dispatch.

**Always strip `<think>...</think>` from responses, even when thinking is off.** With
`--jinja` the reasoning arrives in a separate `reasoning_content` field and `content` is
already clean; keep the strip anyway for a truncated stream.

### 10.3 Robustness

- Stream every call. A single total timeout has no good value: a marking call on unified
  memory can legitimately run for minutes, while a hung server should be caught in
  seconds. Stream, and time out on **silence between tokens** instead.
- Retry a failed or empty call twice with backoff before failing the job.
- A page that returns nothing is skipped with a log line, not a fatal error. One bad
  photograph out of six must not destroy the run.

---

## 11. USER INTERFACE

Single page, no framework, no bundler, no CDN. Everything served locally.

1. **Drop zone** — accepts drag-and-drop and a file picker, multiple files at once.
   Sorts what it is given by filename, numerically, because phone galleries hand files
   over in whatever order the OS felt like.
2. **Page strip** — a thumbnail per page, numbered, with drag-to-reorder, arrow buttons
   and a remove button. The arrows are not decoration: dragging is unusable by keyboard
   and fiddly on a laptop trackpad.

   **Clicking a thumbnail opens the page full size**, with paging and arrow keys.
   A 132px thumbnail is enough to see that a page is upside down and not enough to tell
   page 3 from page 4 — which is exactly the judgement the reordering controls exist to
   support, so the strip has to be able to answer it.
3. **Level dropdown** — thirteen levels, JC2 down to Primary 1, defaulting to Primary 5.
4. **Language dropdown** — English or Chinese.
5. **Topic** — one optional text field. Without it the model cannot judge relevance to
   task, which is a large part of the content mark; with a wrong guess it would be worse,
   so an empty topic explicitly tells the model not to speculate.
6. **Model dropdown** — High Quality, Low Quality, or **Port**. The first two default to
   what this machine can hold; detection picks that default and does not overrule anyone.

   **Port** points the app at a llama-server the operator is already running on
   `127.0.0.1`, and loads nothing locally. A port box appears beside the dropdown,
   defaulting to 9931. Whether that server has a vision projector is deliberately not
   probed — the operator chose the option and knows what they are running — but without
   `--mmproj` it will answer about a page it never saw, so the model note says so.

   **The app's own llama-server runs on 8719, not 8080.** 8080 is llama.cpp's default,
   which is precisely where an operator's own server will be sitting, and two servers
   fighting over one port is a confusing failure for no benefit.

   The model choice is remembered **as it is chosen**, not when a job starts, because
   choosing Port and typing a number is setup rather than a per-composition decision.
   Only "Port" is remembered: High and Low go back to being detected, since detection is
   right about this machine and a remembered choice would outlive the card it was made
   for. A machine set to Port is not held at the door by a health check demanding 21 GB
   of weights it will never load.
7. **Mark button** — disabled until there is at least one page.
8. **Progress** — stage, percentage, elapsed, live estimate, scrolling log, Cancel.
9. **Result** — the score as a large number, the level and topic beside it, tabs for
   **Transcript / Marking / Corrected / Improved**, the correction dropdown and a
   **Generate** button, and Open output folder.

   The transcript tab comes **first**, before the marking. It is what the marking is an
   opinion about, and the one thing worth checking against the page before anything else
   is believed.

   There is **no download button** anywhere. Every document a run produces is already a
   file in the composition's own folder, so a download would only put a second copy in
   `Downloads\`. When Generate finishes it opens that folder instead, with all four
   documents in it. The button reads **Regenerate** once the selected files exist, so it
   says whether pressing it will produce something new or overwrite something.
10. **History** — every composition still in `output\` with a marking report, showing the
    name and the level and nothing else. No score: a mark out of 100 on a list of one
    child's work reads as a league table. The files are the record; a folder deleted from
    `output\` disappears from the list.
11. **The level and language are remembered** between runs, in `preferences.json` in the
    app folder. A parent marks one child's work for a year at a time, so defaulting to
    Primary 5 forever is a small daily annoyance. Stored server-side rather than in
    `localStorage` so that it moves with a copied folder, survives a change of browser,
    and leaves nothing behind when the folder is deleted. What is remembered is what was
    actually **marked**, not what was clicked past.

**Reattaching.** Closing the tab must not orphan a job. On load the page asks
`/api/current` and, if something is running, jumps to the progress view and attaches to
its event stream — which is also the only way back to Cancel after a reload.

**The time estimate is measured, never assumed.** Before the first completed run on this
machine with this model, **show no number at all** and say so in words: the same job
varies by more than ten times between a 16 GB NVIDIA card and an integrated GPU running
on the processor, and a guess that is five times out is worse than admitting ignorance.

**Progress inside a call must not be `tokens / max_tokens`.** The cap is 6000 and a real
report is 800–1200, so that formula crawls to a third and jumps. Use
`1 - e^(-elapsed/expected)` against the measured cost of that stage, blended with the
page count during transcription. However wrong the expectation is, the bar cannot stall
and cannot overshoot.

**The bar and the estimate must come from the same number.** They did not, and it showed:
the bar used the curve above while the estimate subtracted elapsed time from the median,
so a run that outlasted its median read *"73%"* and *"about 0s left"* at the same moment.
The remaining time is `expected × e^(-elapsed/expected)` — the same curve, differentiated
— which falls as the job runs and never reaches zero while it is still running. Below
twenty seconds the UI stops giving a number at all and says *finishing up*.

**Say what the model is doing, not just which stage it is in.** The marking call spends a
minute or more producing reasoning before the first word of the report appears, and with
only content tokens counted the stage line sat unchanged throughout — which reads as a
hang, and was reported as one. Count the `reasoning_content` deltas as well as `content`,
take the prompt size from `/tokenize` before dispatch, and show all three live:

> Marking the composition — 1,650 read · thinking, 2,150 tokens · 36 tokens/s

switching to *writing, N written* once the report starts. The same line goes to the log
every thirty seconds, so a finished job's log still shows what the long call was doing.

**Never show a raw exception.** Map failures to plain sentences, write the traceback to
`temp\job.log`, and offer "Copy diagnostic info".

Visual design: clean, calm, high-contrast, system font stack. This is a utility.

---

## 12. CANCELLATION AND CLEANUP

- **During a model call** — llama-server is a persistent server for the life of the job.
  Abort the in-flight HTTP request and set the flag; the loop checks it before dispatching
  the next page.
- **Always, on cancel** — stop llama-server so the VRAM comes back, and empty
  `temp\pages\`.
- Register the identical cleanup on the cancel endpoint, FastAPI shutdown, `atexit`, and
  a `SIGTERM`/`SIGBREAK` handler. **SIGINT is deliberately not handled**: uvicorn installs
  its own and uses it for a clean shutdown.
- A Windows **job object with `KILL_ON_JOB_CLOSE`** is the only mechanism the OS honours
  when the console window is force-quit. Without it a stranded `llama-server.exe` holds
  VRAM until reboot.
- Sweep `temp\` on startup in case a previous run died badly.

Cancelling means different things for the two kinds of job: a cancelled marking has
nothing behind it and returns to the form; a cancelled correction still has the marked
composition behind it and returns to that.

---

## 13. THINGS NOT TO DO

- Do not add a settings screen, model picker beyond the two, or temperature control.
  Those live in `config.json`.
- Do not add user accounts, a job queue, or batch marking of a whole class.
- Do not add an OCR engine, and do not add Pillow to do the resizing — the browser does
  it, and adding either is a step away from a folder that runs by being unzipped.
- Do not re-read the photographs to produce the corrected versions. Use the transcript.
- Do not translate a child's Chinese composition into English anywhere.
- Do not let a rewrite drift above the child's level. Equally, do not let the improved
  rewrite collapse *into* the minimal correction — see §8.
- Do not use `localStorage` or `sessionStorage`. Remembered choices go in
  `preferences.json` in the app folder (§11), which is the same rule, not an exception to
  it: nothing is written outside the folder.
- Do not add any dependency that pulls in torch, transformers, or the HuggingFace hub
  client. If you find yourself needing one, stop and report it.
- Do not reference any external URL from the frontend, with the single exception of
  the update button's link to this app's own GitHub release page (§0). No fonts, no
  CDN scripts, no analytics, no images.
- Do not bind the server to anything except `127.0.0.1`.
- Do not write to `%APPDATA%`, `%USERPROFILE%` or the registry. Everything stays inside
  the app folder so the whole thing is portable and deletable.
- Do not claim the marking is an official MOE scheme.

---

## 14. ACCEPTANCE TESTS

The build is done when all of these pass:

1. A two-page Primary 4 English composition photographed on a phone produces a
   transcript that matches the page word for word, including its mistakes, and a report
   whose quoted sentences all appear in the transcript verbatim.
2. The same script marked at Primary 2 and at Secondary 4 produces materially different
   scores and different advice. If it does not, the level block is not doing its job.
3. A Chinese composition produces English feedback with Chinese quotations and Chinese
   rewrites, and no English replacement sentences.
4. Pages added out of order and reordered in the UI are read in the order shown.
5. A portrait photograph straight from a phone, with EXIF rotation, is read correctly.
6. A photograph of something that is not a composition produces a plain-language message,
   not an empty report.
7. Generate with "both" writes two files and opens the folder, and the two differ by more
   than corrected errors — including when the level is set below the writing's standard.
8. Cancel during transcription stops within one page, llama-server exits, VRAM is
   released, and a new job starts cleanly.
9. Force-quit the console mid-job: no orphaned `llama-server.exe` in Task Manager.
10. **With the network adapter disabled**, a full run succeeds — pages in, report out,
    corrections generated. Only Check for updates may fail, and it must fail with a
    sentence about the connection rather than a traceback.
11. Copy the entire folder to a different drive letter and run it: works unchanged.
12. Deleting a composition's folder from `output\` removes it from the history list.

---

## 15. REPORT BACK ON COMPLETION

- Any flag from this document that did not exist in the shipped binaries, and what was
  used instead.
- Measured seconds per page for transcription, and seconds for the marking call, on each
  backend available.
- Peak VRAM with the projector loaded, and whether `OVERHEAD_GB` needs adjusting.
- How accurate the transcription actually was on real handwriting, page by page — this is
  the number the whole app rests on.
- Whether the low-quality model is fit for this task, with evidence.
- Final unzipped folder size.
