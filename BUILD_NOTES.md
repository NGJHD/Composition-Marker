# BUILD_NOTES.md

What was verified against the shipped binaries, what was measured, and every place the
implementation departs from `CLAUDE.md` and why.

Development machine: **RTX 3080 10 GB**, Ryzen 9 3900X (12C/24T), 64 GB RAM, Windows 11.
Every timing here is from that machine unless it says otherwise. It is a *small* card for
this model — the 16.5 GB Q4_K_M runs with 50 of its 64 FFN blocks in system RAM — so the
High Quality figures below are a worst case, not a typical one.

---

## 1. Component versions

| Component | Version / pin |
|---|---|
| llama.cpp | `b10852`, the same build the Meeting Summariser ships |
| Qwen3.8-27B-UD-Q4_K_M.gguf | unsloth, rev `4ca72078`, 16,464,440,224 bytes |
| Qwen3.8-27B-UD-IQ2_XXS.gguf | unsloth, rev `4ca72078`, 7,266,070,528 bytes |
| mmproj-F16.gguf | unsloth, rev `4ca72078`, 927,607,488 bytes |
| Python runtime | CPython 3.12.14, the Meeting Summariser's assembled environment |

**The release zip carries everything except the two Qwen models.** The Python runtime,
all three backend binary sets, the CUDA runtime and `mmproj-F16.gguf` all ship inside it —
about 2.5 GB zipped. `DOWNLOAD_MODELS.bat` therefore fetches **two files**, not eight:
`Qwen3.8-27B-UD-IQ2_XXS.gguf` and `Qwen3.8-27B-UD-Q4_K_M.gguf`, 24 GB between them and too
large to carry.

The projector is the judgement call in that list — 886 MB is a lot to put in a zip, but
nothing in the app works without it, and a first run that fails on a missing file the user
has never heard of is worse. It is still fetched by the downloader if it is absent, which
is the case of a fresh clone of the source repository: `models\` is gitignored, so a clone
has none of this.

No ffmpeg, no whisper.cpp, no sherpa-onnx, no ONNX Runtime. `bin\` is 1.3 GB here against
2.0 GB there.

---

## 2. Flag verification

### The model really is multimodal, and this build can use it

This was the first thing checked, because the whole architecture rests on it.

- `unsloth/Qwen3.8-27B-GGUF` publishes `mmproj-F16.gguf` and `mmproj-BF16.gguf`. The Qwen
  model card describes image and video understanding and reports 91.1 on OmniDocBench 1.5
  (document intelligence). It makes **no claim about handwriting**, which turned out to
  matter — see section 6.
- The shipped binaries carry `mtmd.dll`, and the strings in `llama-common.dll` include
  `--mmproj`, `--mmproj-auto`, `--mmproj-device`, `--mmproj-offload` and `--mmproj-url`;
  `llama-server-impl.dll` contains `image_url` and the `mtmd_*` entry points.
- Confirmed at runtime: `llama-server` started with `--mmproj` accepts an OpenAI
  `image_url` content part carrying a `data:image/jpeg;base64,...` URL and answers about
  the image.

The GGUF's own metadata reports `general.architecture = qwen35`, `block_count = 65`,
`context_length = 262144`, and a hybrid attention/SSM layout with
`full_attention_interval = 4`. That last one is why the context arithmetic in section 3
comes out as low as it does.

### Everything else in CLAUDE.md section 10.1 exists

`--ctx-size`, `--flash-attn on`, `--cache-type-k/v`, `--jinja`, `--batch-size`,
`--ubatch-size`, `--parallel`, `--host`, `--port`, `--no-webui`, `--n-gpu-layers`,
`--override-tensor`, `--device`, `--threads`. No flag had to be substituted.

---

## 3. Deviations from CLAUDE.md

### 3.1 `ctx_size` is 16384, not the meeting app's 32768 (deliberate)

Nothing here is chunked, so the largest single call is bounded and small:

| Call | What is in the context | Tokens |
|---|---|---|
| transcribe, one page | page image + prompt + verbatim output | ~4,200 |
| mark | rubric + level block + composition + thinking + report | ~8,000 |
| correct | composition + prompt + rewrite | ~6,000 |

Measured on the two-page Primary 4 test script: the transcription calls used **2,656 and
2,641 tokens** total, and the marking call **4,348**. The largest observed call is 27% of
the 16k window.

The page image itself is **2,548 tokens** at 1131x1600 — within 4% of the 2,450 predicted
from Qwen's 14px patches with a 2x2 merge, which is what the 1600px downscale was chosen
against. It encodes at ~590 tok/s on this card, so about 4.3 seconds of the 7-second page
is the image going in.

That number is also the argument against a single-call design: six pages would be
**15,288 tokens of image** before a word of the rubric was read, and twelve pages could
not be done at all inside 32k, let alone 16k.

The saving is real, because this model's KV cache is unusual. `full_attention_interval =
4` over 65 blocks means only ~16 layers carry a cache at all, with 4 KV heads and 256-dim
keys and values: about 32 KB per token with `q8_0` keys and values.

| ctx_size | KV cache |
|---|---|
| 32768 | ~1.05 GB |
| **16384** | **~0.53 GB** |

Half a gigabyte back, on a card that now also has to hold `mmproj-F16.gguf` at 0.86 GB
plus its image-encode buffers.

### 3.2 `OVERHEAD_GB` raised from 1.5 to 2.0 (required)

`hardware.offload_regex` sizes the FFN split from `model size + OVERHEAD_GB` against
measured VRAM. The meeting app allowed 1.5 GB for a 32k KV cache and compute buffers.
Here the context is half the size but the vision projector is new:

```
KV cache at ctx 16384      ~0.53 GB
mmproj-F16.gguf             0.86 GB
compute + image encode     ~0.6  GB
                           --------
                           ~2.0  GB
```

### 3.3 One engine, so `config.backend()` lost its argument (simplification)

The meeting app resolved the backend **per engine**, because whisper.cpp and llama.cpp
ship separately and can legitimately end up on different backends. There is one engine
here, so `backend()` takes no argument and `bin\` has three folders instead of six.

### 3.4 `paths.models_dir` added (new)

A machine that already holds these exact GGUFs for the Meeting Summariser has no reason
to keep a second 16.5 GB copy. An absolute path is taken as given; a relative one
resolves against the app folder, so `..\Meeting-Summarizer\models` works and still moves
with a copied pair of folders.

The consequence is that **`run.bat` cannot check the weights** — it cannot read JSON. It
hard-fails on `runtime\python.exe` and `llama-server.exe`, and only *warns* about the
model files. `config.missing_files()` resolves the real path and the first screen names
anything missing precisely, which it did anyway.

### 3.5 `max_tokens` covers the reasoning, and that broke the first run (required)

**This is the most important finding in the file.**

The marking call runs with `chat_template_kwargs.enable_thinking: true`. At the
originally specified `max_tokens: 3000`, the very first end-to-end run produced:

```
[22:45:08] Marking the composition
[22:46:27] llm: mark call failed (the model returned an empty completion) - retrying
[22:47:47] marked: 64 / 100
```

The cap applies to **everything the model generates, reasoning included**. The model
spent all 3000 tokens reasoning, emitted zero content tokens, and the call came back with
an empty `content` — indistinguishable from a broken server. The retry then produced a
report that was itself cut off mid-table:

```
| # | What was written | A stronger version | Why it is better |
|---|---|---|
```

Note the malformed divider: it stopped in the middle of writing it. A truncated table is
worse than an obvious failure, because it renders as a plausible-looking report with the
most useful section missing.

Three changes:

1. `llm.max_mark_tokens` is **6000**, `max_correct_tokens` 4000. A finished report is
   800–1,200 tokens and the reasoning runs 2,000–3,000, so 6000 leaves real headroom.
2. `_sse_event` now reads **`finish_reason`** as well as content. `"length"` is treated
   as a failure even when text came back.
3. On a `length` finish the retry **turns thinking off** rather than repeating an
   identical call. Repeating it cost 80 seconds to fail the same way; without thinking
   the whole budget goes to the answer. If every attempt truncates, the longest partial
   answer is returned rather than nothing.

After the change the same job completed in **one** marking call with no retry.

### 3.6 Page line-breaks are joined in code, not in the prompt (required)

`prompts\transcribe.txt` says, in as many words, not to preserve the line breaks within a
paragraph. The model does it anyway — on ruled exercise paper it copies the ruling. The
first run returned a composition as twenty one-line "paragraphs".

That is not cosmetic. Organisation is 20 of the 100 marks and paragraphing is most of it,
so invented line breaks get marked as invented paragraphs.

`pipeline._unwrap` joins lines within a block and keeps blank lines as paragraph breaks.
It handles two cases the naive version gets wrong:

- a line ending in `-` is a word split across the break, so the hyphen is dropped and the
  halves are joined directly
- **no space is inserted between two CJK characters**, because Chinese does not put
  spaces between them, and one inserted at every line break would show up in the quoted
  sentences and in both corrected versions

Applied only in the transcription path, deliberately: `_clean_page` is shared with the
correction calls, and a rewritten composition may separate its paragraphs with a single
newline, which unwrapping would collapse into one block.

### 3.7 Uploads are per-page raw bodies (carried over)

Streamed from the raw request body, not multipart, so nothing buffers the image a second
time. There is nothing a filename would tell us: the order is the order the user arranged
in the browser and arrives as an `index` query parameter. Rejected by magic bytes, not by
extension.

### 3.8 A second instance used to delete the first one's pages (required)

Found by causing it. `run.bat` scans ports 8000–8005 and starts on whichever is free, so
a second copy launches happily while the first is mid-job — and the startup sweep,
inherited from the meeting app, then deleted `temp\pages\` while the first instance was
still reading from it. The first job would fail with a missing file and no visible cause.

`temp\running.lock` now records the PID. A startup that finds a **live** PID in it leaves
`temp\` alone and says so in the job log; a stale one, from a run that was force-quit, is
ignored and overwritten — which is the case the sweep exists for in the first place.

The liveness check tests the process **name** as well as the number. Windows reuses PIDs,
and a recycled number would otherwise disable the sweep permanently.

### 3.9 Image preparation is done in the browser (new)

No Pillow, no imaging library in `runtime\`. `createImageBitmap(file, { imageOrientation:
"from-image" })` applies the EXIF rotation and a canvas does the downscale to 1600px on
the long edge at JPEG quality 0.85.

The EXIF part is not optional: without it every portrait photograph arrives on its side
and the handwriting cannot be read at all.

HEIC cannot be decoded by Windows browsers at all. The failure is caught and reported in
words, naming the iPhone setting that fixes it, rather than failing silently.

---

## 4. Measurements

### 4.1 The test script

Two pages of a Primary 4 narrative, "A Kind Deed", 157 words, rendered in Ink Free on
ruled paper at 1240x1754 and saved as JPEG — close enough to a phone photograph of a
child's page for the plumbing, and **easier than real handwriting**, which section 6 is
honest about.

The script carries deliberate, level-typical errors: `saturday`, tense slips into the
present throughout, `alot`, `a old man`, `siting`, `becuase`, `more better`, `was send`,
`After sometime`.

### 4.2 Wall time, RTX 3080 10 GB, CUDA

| Stage | IQ2_XXS | Q4_K_M |
|---|---|---|
| Model load, warm disk | 6 s | 15 s |
| Transcribe, page 1 | 7 s | 50 s |
| Transcribe, page 2 | 7 s | 45 s |
| Marking call | 156 s | see 4.3 |
| **Whole job** | **~3 min** | see 4.3 |

Throughput behind those figures, IQ2_XXS fully resident on the GPU:

| | |
|---|---|
| Image encode + prompt | ~590 tok/s |
| Generation | **37.4 tok/s** |

So the 150-second marking call is ~5,000 generated tokens: the reasoning block plus a
1,000-token report. That is the whole reason `max_mark_tokens` had to go up rather than
the reasoning being turned off — at 37 tok/s the thinking costs about a minute, and it is
the difference between a banded judgement and a guess.

The gap between the two models below is the offload, not the model. IQ2_XXS at 7.3 GB fits this card whole
(`fully on the GPU` in the log); Q4_K_M at 16.5 GB runs with **50 of 64 FFN blocks in
system RAM**. On the 16 GB machine this app is aimed at, Q4_K_M would offload a handful
of blocks at most, and the gap should close to something much smaller. **Re-measure there
before drawing conclusions about the model choice.**

### 4.3 Q4_K_M is unusable on a 10 GB card, and that is the expected result

The marking call was **abandoned after 25 minutes** without completing. Transcription had
already finished, so the figures above are real; the marking call is simply not viable
with 50 of 64 FFN blocks paged over PCIe and 6,000 tokens of budget to generate.

This is not a finding about the model. It is a finding about running a 16.5 GB model on a
10 GB card, and it is exactly the situation `hardware.choose_key` exists to avoid: at
10,240 MiB it selects IQ2_XXS, so **no user on this machine would ever be in this
position** unless they overrode the dropdown deliberately.

The consequence for the release is that the High Quality path is **verified for
correctness but not for speed**. It must be timed on a 16 GB card before any claim is
made about it.

---

## 5. Marking quality

### 5.1 The level block does its job

The score came out at **71 / 100** for the Primary 4 script — Content 33/40, Language
23/40, Organisation 15/20 — with Language correctly identified as the weak criterion.

More to the point, the feedback stayed at the level. The rewrites offered were
`He looked very tired`, `I quickly ran to him and asked him if he was okay`,
`his leg was hurting him` — plain corrections a ten-year-old can act on, not
literary upgrades. That is what `levels.py`'s "what is NOT yet expected at this level"
paragraph is for, and on this evidence it works.

### 5.2 The same script at three levels

Acceptance test 2. One transcript, three levels, IQ2_XXS:

| Level marked at | Overall | Content | Language | Organisation |
|---|---|---|---|---|
| Primary 2 | **79 / 100** | 34 / 40 | 28 / 40 | 17 / 20 |
| Primary 4 | **71 / 100** | 33 / 40 | 23 / 40 | 15 / 20 |
| Secondary 4 | **39 / 100** | 16 / 40 | 15 / 40 | 8 / 20 |

A 40-mark spread on one unchanged transcript. The level block is doing the work it was
built to do.

**Caveat, and it matters for how the number is presented:** the Primary 4 case was run
twice and scored **71** and **67**. Sampling is at `temperature: 0.7`, so the mark is not
deterministic and a few points of run-to-run movement is normal. The spread between
levels is many times larger than the spread between runs, so the comparison above holds —
but a single score should be read as a band, not a measurement, which is why the report
leads with bands and why the README says it is not a predicted grade.

The *reasons* moved with the marks, which matters more than the numbers. At Secondary 4
the Content comment reads:

> at 150 words it is far short of the 350–500 range, and the old man is never described,
> the setting never breathed

Both halves of that come straight from `levels.py`: the length range, and the expectation
that a Secondary 4 piece shows rather than tells. Neither was mentioned at Primary 2 or
Primary 4, where neither is expected. The Organisation comment at S4 objects to
"and… and… and…" links — again a level-appropriate complaint that does not appear lower
down.

The closing advice differed the same way: Primary 2 was told to write the piece in past
tense and read it back; Primary 4 to add a sentence of description before the rescue.

### 5.3 Two prompt bugs the runs exposed

**The model copied a placeholder into the report.** `mark.txt` illustrated the criteria
table with `| Content | NN / 40 | N | one sentence |`. On the Primary 2 run the model
emitted the literal words `one sentence` in the Comment cells *and* dropped the Comment
header, producing a three-column header above four-column rows. The template now uses
`...` for the cell and states in prose what belongs there, with an explicit instruction
never to write an instruction from the prompt into the table.

`renderMarkdown`'s table builder was also widened to the widest row rather than to the
header, so a malformed table loses a heading rather than a whole column of marking.

**The minimal correction dropped the title.** "A Kind Deed" is the first line of the
transcript and came back missing from `_corrected.md`, the model having treated it as a
heading rather than as part of the composition. Both correction prompts now say to keep
the title as the first line.

**And one bug that was not the prompt's fault: the history list captioned everything
"Primary 5".** `composition.json` was written by the endpoint that serves the result
page, so a run whose result page was never opened had no sidecar — and `levels.label("")`
fell back to `DEFAULT_KEY`, captioning a Secondary 4 marking as Primary 5 with no score.
Two changes: the sidecar is now written by the pipeline as the last act of a run, where
it belongs with the documents; and `levels.label` returns `""` for a key it does not
recognise, so an unknown level is shown as nothing rather than as a confident wrong
answer. `expectations_block` still defaults, because a marking run always has a real
level and needs *something* to mark against.

### 5.4 Every quoted sentence was verbatim

All five rows of the "Sentences to improve" table quoted the transcript exactly. This is
the property the two-pass architecture exists to protect, and it held.

### 5.5 The corrected versions

Both were produced from the stored transcript in about 6 seconds each with thinking off.

The **minimal correction** fixed `Saturday`, `was`, `a lot`, `an old man`, `sitting`,
`looked`, `ran`, `asked`, `was okay`, `could not`, `called`, `waited`, `gave`, `drank`,
`said`, `was sent` — and left `more better`, `After sometime` and the last paragraph's
tense slips untouched. It is a good pass, not a complete one; that is an IQ2_XXS
limitation and the report's own "What to work on" section did name the tense problem
explicitly.

The **improved rewrite** stayed at the level, which was the thing most at risk: it fixed
`more better`, tightened the sentence joins, and did **not** reach for figurative
language or complex clauses. At Primary 2 it is barely longer than the original, which is
correct — the rewrite is capped by the level, not by ambition.

### 5.6 Chinese, end to end

A one-page Primary 5 华文 script, 《一件难忘的事》, 205 characters, with three planted
faults: 「太阳晒**的**我满头大汗」 (should be 得), 「我**在**也不会忘记」 (should be 再),
and the variant character 「柺杖」.

Marked **67 / 100**. The language split the operator asked for held exactly:

- every heading, comment, bullet and reason in **English**
- every quotation in **Chinese**, verbatim
- every rewrite in **Chinese** — no English replacement was offered for a Chinese
  sentence anywhere in the report

Both grammatical faults were caught and correctly explained: 得 as the complement form,
and 再 as the right particle for "never again". The rewrite column stayed at the level —
「我们陪着老公公，一起等救护车」 is a P5 sentence, not a literary one.

The CJK branch of `_unwrap` did its job: no spaces appear anywhere in the transcript or
the quotations.

The variant character was **not** preserved — 「柺杖」 came back as 「拐杖」 — which is the
same silent normalisation as `becuase` in section 6, in a case where a human marker might
well not have flagged it either.

### 5.7 "Notes on the reading" over-flags, which is the right direction

The model flagged `alot` and `siting` as possible misreadings. Both were in fact the
child's own errors, deliberately planted. The section is worded as *worth checking the
original*, so a false positive costs the reader a glance at the page; a false negative
would cost the child a mark they did not deserve. Erring this way round is correct.

---

## 6. Transcription fidelity — the number the whole app rests on

The test page carries eight planted, level-typical errors. Three runs:

| The page says | IQ2_XXS run 1 | IQ2_XXS run 2 | Q4_K_M |
|---|---|---|---|
| `saturday` | ✓ | ✓ | ✓ |
| `alot` | ✓ | ✓ | ✓ |
| `a old man` | ✓ | ✓ | ✓ |
| `siting` | ✓ | ✓ | ✓ |
| `He look` | ✓ | ✓ | ✓ |
| `was send` | ✓ | ✓ | ✓ |
| `After sometime` | ✓ | ✓ | ✓ |
| `Suddenly` | **`Suddenlly`** | ✓ | ✓ |
| `becuase` | **`becase`** | **`because`** | **`because`** |

### Both quantisations silently corrected the same misspelling

This was the surprise, and it is the more important half of the result. `becuase` came
back as `because` from **Q4_K_M as well as IQ2_XXS**. It is therefore not a quantisation
artefact: the model normalises a misspelling it recognises, in the same way a fluent
reader does, despite a prompt that says in as many words not to.

That is the damaging direction of error. The child's mistake never reached the marking,
so they were never told about it — and unlike an invented mistake, nothing downstream can
catch it, because the transcript is self-consistent and reads perfectly.

The opposite error is IQ2-only and less serious: `Suddenlly` and `becase` in run 1 invent
mistakes that were not made. Those are visible, and *Notes on the reading* is there to
flag them — which, on the P4 run, it did.

### What follows from this

**Do not present the transcript as authoritative, and do not let a language deduction go
unchecked.** The three mitigations in place are:

1. the transcript is a document and a tab, not an invisible intermediate
2. *Notes on the reading* asks the model to flag its own suspect words, and it errs
   towards over-flagging, which is the right direction
3. `_shared.txt` tells the marking pass that nonsense in an otherwise careful script is
   more likely a misreading than a mistake

None of them recovers a *silently corrected* error. The only thing that does is a person
comparing the transcript with the page, which is why the README says so plainly and the
tab is called "Transcript" and opens first.

**On the model choice:** IQ2_XXS was less stable across runs — two errors on run 1, one
on run 2, against Q4_K_M's one — which matches what the Meeting Summariser measured for
this quantisation on names and figures. But the gap here is narrower than expected, and
on the one error that matters most they behaved identically. Detection's rule stands
(Q4_K_M at 15 GB and above, IQ2_XXS below), and on the evidence available IQ2_XXS is fit
for this task in a way it was **not** fit for meeting minutes: a marking report is a
judgement about writing, not a record of names and numbers.

---

## 7. Faults found in use, and what they turned out to be

### 7.1 The two corrected versions came back identical

Reported from a real run: `_corrected.md` and `_improved.md` differed only in their title
line — the compositions inside were byte-identical.

The composition was a strong piece of writing marked at **Primary 2**: "The sky was
flaming red… waiters balancing trays of food… Embarrassment stained our cheeks." That is
Primary 5 or 6 work. And at Primary 2, the two prompts say the same thing:

- minimal: *fix the mistakes, change nothing else*
- improved: *rewrite it to the standard of a strong Primary 2 script*

The piece is already far above a strong Primary 2 script, so the second instruction
reduces to the first, and both calls converge on "fix the small errors".

Re-running the pair twice did **not** reproduce byte-identical output — the two runs
differed, but only in a word or two, which is the same fault a shade less visible. So this
is not a plumbing bug (prompt hashes and output hashes were logged and confirmed distinct
per call); it is the prompts collapsing into each other.

`correct_improved.txt` now opens by saying that the corrected version already exists and
that its own output must not come out the same, requires **one deliberate improvement in
craft per paragraph**, and says explicitly what to do when the composition already exceeds
its level: bring the weakest paragraph up to the best one rather than handing it back. The
length floor changed from "not double it" to "at least as long as the original" — the
improved rewrite had been coming back *shorter* than the piece it was improving.

Worth saying plainly: the level was set two or three years below the writing. The app
should behave well anyway, and now does, but the marking will be most useful at the level
the child is actually working at.

### 7.2 "73%" and "about 0s left" at the same moment

From a screenshot of a live run, 2m31s into the marking call.

Two displays, two different formulas. The bar used `1 - e^(-t/T)` against the measured
median; the estimate subtracted elapsed time from that same median and floored at zero. So
the moment a run outlasted its median — half of all runs, by construction — the estimate
pinned to zero while the bar carried on climbing. A countdown resting on zero beside a
moving bar reads as a stall.

The remaining time is now `T × e^(-t/T)`: the same curve the bar draws, so the two cannot
disagree, and it approaches zero without arriving while the stage is still running. Below
twenty seconds the UI stops quoting a number and says *finishing up*; below a minute,
*less than a minute left*.

The bar itself was not wrong. At `t = T` the curve is at 63% of the stage, which is the
whole point of an asymptote — it can be late without ever overshooting.

---

## 8. Acceptance tests (CLAUDE.md section 14)

| # | Test | Status |
|---|---|---|
| 1 | Transcript matches the page including its mistakes; quotes verbatim | **Partly** — quotes verbatim, one misspelling silently corrected (section 6) |
| 2 | Same script at different levels scores differently | **Pass** — 79 / 71 / 39 across P2 / P4 / S4 |
| 3 | Chinese: English feedback, Chinese quotes and rewrites | **Pass** (section 5.6) |
| 4 | Pages reordered in the UI are read in the order shown | **Pass** — verified in the browser: two pages uploaded in reverse, sorted by filename on arrival, reordered with the arrows, numbering followed |
| 5 | Portrait phone photograph with EXIF rotation | **Not tested** — no phone photo available; the code path is `imageOrientation: "from-image"` |
| 6 | A photograph that is not a composition gives a plain message | **Not tested** |
| 7 | Generate with "both" writes two files that differ, and opens the folder | **Pass** after the section 7.1 fix; it failed before it |
| 8 | Cancel during transcription | **Not tested** |
| 9 | Force-quit leaves no orphaned llama-server | **Not tested** — the job object is carried over unchanged from the Meeting Summariser, where it was verified |
| 10 | Runs with the network adapter disabled | **Not tested** — no outbound call exists in the code |
| 11 | Copy the folder to another drive and run | **Not tested** |
| 12 | Deleting an output folder removes it from history | **Pass** — the list is built from the folders |

Tests 5, 6, 8, 10 and 11 are the ones worth running before this is given to anybody. None
of them needs a model download; 5 and 6 need a phone.

---

## 9. Still not measured

- **Real handwriting.** Everything above used a rendered font. A child's actual
  handwriting is the real test and the numbers in section 6 will get worse, not better.
- **A 16 GB card**, where Q4_K_M is the default and the offload is small.
- **Unified memory.** The CPU path is inherited from the Meeting Summariser and should
  work, but the vision encode on the processor is new and unmeasured. A UMA machine is
  quoted per page in the UI for this reason.
- **Chinese**, end to end, with a real 华文 script.
- **AMD and Intel via Vulkan**, including whether the coopmat workaround is still needed
  for the vision path specifically.
