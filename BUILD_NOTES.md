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

### 3.0 32768 was tried, and it cost twelve times the transcription speed

Raised to 32768 first, on the reasonable worry that the 157-word test script was
under-measuring: a real Primary 4 composition is nearer 400 words and a JC2 essay 800.
Then measured on a real 380-word three-page script, same machine, same model:

| | ctx 16384 | ctx 32768 |
|---|---|---|
| Transcribe, per page | **8-9 s** | **70-73 s** |
| Generation during transcription | 36 tok/s | **2 tok/s** |
| Generation during marking | 36 tok/s | 24 tok/s |
| Whole job | ~3 min | ~6.5 min |

On a 10 GB card the extra ~0.53 GB of KV cache is exactly what the image-encode buffers
needed. Losing it pushes them out of VRAM and the vision path collapses.

And the headroom bought nothing. The 380-word script used **prompt 1,647, reasoning
1,931, report 1,098 — about 4,700 tokens, 29% of a 16k window.** The projection that
justified the change assumed reasoning scales with composition length; it does not. The
same 157-word script produced **2,191 tokens of reasoning on one run and 5,504 on
another**, so run-to-run variance dwarfs the variance with length, and 16k holds the
worst of both comfortably.

Put back to 16384, with `max_mark_tokens` at 12,000 — enough for the worst reasoning seen
plus a long report, still leaving room for the prompt inside the window.

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

Fixing it took four attempts, and the failures are worth recording because three of them
were mine, not the model's:

1. **Told it the two must differ, with a craft requirement per paragraph.** They differed
   — and at Primary 2 the rewrite *simplified the child's writing to match the level*:
   "a willowy woman with long, lustrous hair" came back as "a woman with long hair". The
   level was acting as a ceiling on words the child had already used correctly.
2. **Added "never make it weaker": don't simplify, don't delete, don't shorten.** Now the
   safest way to comply with every rule was to change nothing, so it handed the
   composition back untouched — byte-identical to the minimal correction again. A prompt
   built of prohibitions has inaction as its safest compliance.
3. **Turned the prohibitions into six mandatory, checkable actions** (rewrite the first
   sentence, rewrite the last, add two sentences of detail, three stronger verbs, three
   varied openers, fix every error). Better — it did the first one. It did not do the
   other five.

Then the useful question: is this the prompt or the model? Same prompt, `Q4_K_M`:

> The sky blazed red outside. Waiters darted around the bustling restaurant… My tummy
> rumbled loudly as I leafed through the menu… We were having a **whale** of a time…
> promised to **turn over** a new leaf.

New opening, added sensory detail, stronger verbs throughout, the ending rewritten, and
it repaired two idioms the child had mangled — while keeping "famished", "lustrous",
"profusely" and "chastised". That is the document this feature is supposed to produce.
**So the prompt was adequate and IQ2_XXS was the limit.**

4. **Ground the rewrite in the marking report.** The judgement the rewrite needs has
   already been made minutes earlier by the marking call, *with thinking on*: it named
   the weak sentences and wrote a stronger version of each. `improvements_from_report`
   lifts the "What to work on" and "Sentences to improve" sections out of the report on
   disk and hands them to the rewrite as changes to apply. An open-ended writing task
   becomes a mechanical one, which is what the small model is good at.

With that, IQ2_XXS applies the identified fixes it had been ignoring — "a whole of a
time" → "a great time", "willowy women" → "woman", "promised to turn a new leaf" — and
often rewrites the opening as well. It is still short of Q4_K_M, and it is variable
between runs. That is the honest state: **the improved rewrite is the one feature where
the low-quality model is materially weaker**, and the README should not pretend
otherwise.

Two smaller things fixed alongside:

- The length floor changed from "not double it" to "at least as long as the original".
  The rewrite had been coming back *shorter* than the piece it was improving.
- **The title kept disappearing.** Both prompts say to keep it; both models drop it
  perhaps half the time, reading the first line as a heading to strip. `_keep_title`
  puts it back — deterministically, only when the first line actually looks like a title
  and the rewrite does not already open with it. Same class of fix as `_unwrap`: where a
  requirement is mechanical, do it in code rather than asking harder.

Worth saying plainly: the level was set two or three years below the writing. The app
should behave well anyway, and now does, but the marking is most useful at the level the
child is actually working at.

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

### 7.2a The minimal correction quietly stopped correcting on a long script

Found while verifying the word-count change, on the 380-word three-page script. The
minimal correction fixed `becuase` in the first paragraph and then copied the rest of the
composition out unchanged — `shinning`, `suddently`, `a old`, `I pick it up`,
`there was many`, `walk over`, `ask me`, `a letter arrive` all survived.

Worse than useless: the child is told their later paragraphs were fine when they were
not, and it is the *reliable* one of the two corrections.

A long copy-with-small-edits task is exactly where a 2-bit model drifts into copying. The
fix is the one that already worked for the improved rewrite: give it the marking report's
findings. `improvements_from_report` is now passed to **both** prompts — the improved one
applies everything, and `correct_minimal.txt` is told to take only the errors from it and
ignore the stylistic suggestions, under a heading that says to work to the very end and
names copying-the-rest as the way to get it wrong.

After the change every planted error is fixed through all three pages, the title is kept,
and the two documents still differ.

Not fixed, and worth knowing: the improved rewrite wrote "the sun shined brightly" where
the minimal correction wrote "shining". IQ2_XXS will occasionally introduce an error while
improving a sentence. Another reason the minimal correction is the one to trust on that
model.

### 7.3 IQ3_XXS on the rewrite: no better than IQ2, and 3.6 GB larger

Tried at the operator's request, on the same Primary 2 case. With the report-grounded
prompt it produces a rewrite that is clearly distinct from the minimal correction, opens
with "The sky blazed with a flaming red hue", and applies every identified fix. It also
keeps the title, which IQ2 drops about half the time.

But it does not do what Q4_K_M does: no new sentences of detail, and the ending is left
as it was. That is the same shortfall as IQ2 with grounding, not a step towards Q4.

| | IQ2_XXS + grounding | IQ3_XXS + grounding | Q4_K_M |
|---|---|---|---|
| Differs from the minimal correction | yes | yes | yes |
| Applies the identified fixes | yes | yes | yes |
| Rewrites the opening | usually | yes | yes |
| Adds new detail, rewrites the ending | no | no | **yes** |
| Keeps the title | ~half the time | yes | yes |
| Size | 7.3 GB | 10.9 GB | 16.5 GB |

**Recommendation: keep IQ2_XXS as the low-quality option.** The step change is between
the sub-4-bit quants and Q4_K_M, not between IQ2 and IQ3. IQ3 costs 3.6 GB more, and on a
10 GB card it no longer fits whole where IQ2 does. If a machine has room for IQ3 it very
nearly has room for Q4_K_M, which is the one that actually solves this.

### 7.4 "It's just stuck there"

Reported against `[01:42:09] Marking the composition`, which sat unchanged for minutes.

Nothing was stuck. The marking call runs with thinking on, and reasoning arrives in a
separate `reasoning_content` field that the stream reader was discarding — deliberately,
because it is not part of the document. But it meant that during the 60-150 seconds the
model spends reasoning, **nothing was counted and nothing moved.**

Both streams are now counted, the prompt size comes from `/tokenize` before dispatch, and
the progress line reads:

```
Marking the composition — 1,650 read · thinking, 2,150 tokens · 36 tokens/s
```

switching to `writing, N written` when the report starts. `timings_per_token` was tried
first — this build does not populate intermediate chunks with it, only the final usage
chunk under `stream_options.include_usage`, which is too late to display.

The same line goes to `temp\job.log` every thirty seconds, which is also how the 32k
regression in section 3.0 was spotted: 2 tokens/s where there should have been 36.

### 7.5 A duplicated log line, and a stale corrected version

Two small ones, both visible in the screenshots above.

**`llm: starting llama-server on port 8080` appeared twice.** The SSE endpoint registered
the client's queue and *then* built the backlog inside the async generator, which runs
later — so anything logged in between was in both. The backlog is now snapshotted at
registration.

**A re-mark left the previous composition's corrected versions behind.** Two compositions
with the same topic and level share an output folder; the second run overwrote the
transcript and the report but not `_corrected.md` and `_improved.md`, leaving a corrected
version of somebody else's writing under the right name and date. They are deleted when a
new transcript is written — they are always regenerable, and a mismatched one is not
worth keeping.

---

### 7.6 IQ4_XS on a 16 GB card: the quant this app should have had

Tried at the operator's request on an **RTX 5060 Ti 16 GB**, which is the first 16 GB card
this has run on and closes the "a 16 GB card" item in section 9. Model:
`Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller.gguf`, 13.54 GB.

It was run through the app's own pipeline and prompts with the **Port** option, against a
hand-started `llama-server` on 9931 carrying the same flags the app builds. Nothing in the
model table was touched. All three runs mark the **same two photographs of real
handwriting** — a P4 narrative on ruled school paper, blue ink, with crossings-out and a
caret insertion — so the comparison is like for like. It is also the first section here
measured on real handwriting rather than a rendered font.

### 7.6a Why it is faster, which is arithmetic rather than luck

`OVERHEAD_GB = 2.0` against a 16311 MiB card leaves 15.93 GB available, so:

| Model | File | Needed | `offload_regex` on this card |
|---|---|---|---|
| Q4_K_M | 16.46 GB | 18.46 | `blk\.(49|...|63)\.ffn_.*=CPU` — **15 FFN blocks to system RAM** |
| IQ4_XS | 13.54 GB | 15.54 | **none — fully on the GPU** |
| IQ2_XXS | 7.27 GB | 9.27 | none |

A 16 GB card is exactly the size at which Q4_K_M *almost* fits, and "almost" costs a
quarter of the FFN stack. The threshold this is measured against — now `HQ_MIN_VRAM_MB`,
still 15000 — was not wrong: Q4_K_M does run there, and it marks well. It simply runs at a
third of the speed of a quant that fits whole. Swapping which file "High Quality" names is
therefore the whole fix, and the threshold needs no change: IQ4_XS wants
13.54 + 2.0 = 15.54 GB to sit entirely on the card, so a machine reporting between 15.0
and 15.5 GB takes a small offload instead of none, which is the right answer for it.

### 7.6b Measured, same two pages, same card

| | IQ2_XXS | Q4_K_M | **IQ4_XS** |
|---|---|---|---|
| Fits whole in 16 GB | yes | **no** | **yes** |
| Transcribe, s/page | 10-19 | **100-130** | **14, 15** |
| Transcribe tok/s | ~19 | 8.5 | 17-19 |
| Marking call | 93-581 s | **948 s** | **322 s** |
| Marking tok/s | - | 8.5 | 25.5 |
| Minimal correction | - | 75-157 s | **23 s** |
| Peak VRAM with the projector | - | - | **15.1 GB of 16.3** |
| Load from warm cache | 5.6 s | 8.6 s | 7 s |

Peak VRAM leaves about 1.2 GB spare, so **`OVERHEAD_GB` does not need raising** for this
quant on this card. It is tight enough that it should not be lowered either.

### 7.6c Transcription: content-identical to Q4_K_M

Diffed word by word against Q4_K_M's transcript of the same photographs. **Three
differences in 400 words**, and the same two paragraph breaks in the same places:

| Q4_K_M | IQ4_XS | |
|---|---|---|
| a present-tense verb, as written | corrected to past tense | wrong: silently fixed the child's tense error |
| a possessive with no apostrophe, as written | apostrophe supplied | wrong: silently corrected the child's punctuation |
| a comma before a caret-inserted adverb | no comma | right: the page has no comma there |

Both regressions are the *damaging* direction described in section 6: the error never
reached the marking, so the child was never told about it. Q4_K_M's report flags both of
them (its rows 12 and 13); IQ4_XS's cannot, and does not.

Against the photographs, **both** read correctly: a subject-verb disagreement, an
accidentally doubled word, a stray comma inside a subordinate clause, a wrong modal tense,
one crossed-out word correctly dropped, and one caret-inserted adverb correctly honoured.
Both silently corrected two of the child's misspellings — one a common letter-swap, one a
misspelt dialogue verb — which is section 6's `becuase` finding reproducing on real
handwriting, at every quantisation. Both also retained a *different* crossed-out word,
which the prompt says to omit.

**Both paragraphed the transcript correctly**, which section 8a says does not happen. It
is not a contradiction so much as a limit on 8a's claim: this child's indents are wide and
the ink is clean, and on that page the model preserved them unasked. On the pencil page
under red annotation it still does not. Unpredictable, as 8a says, so nothing changes.

### 7.6d IQ2_XXS on the same page, for contrast

Nine content differences against Q4_K_M's reading, every one of them IQ2's error:

a mid-sentence comma turned into a full stop, inventing a fragment - a misspelt dialogue
verb replaced with a different word entirely - one adverb swapped for a near-homonym - a
crossed-out word kept - an exclamation mark inside dialogue turned into a full stop - a
possessive silently given its apostrophe - **the caret-inserted adverb dropped entirely** -
a lower-case conjunction capitalised, moving the sentence boundary - and **both paragraph
breaks lost**, returning one unbroken block.

The first of those is the worst thing in this file. IQ2 turned the child's opening comma
into a full stop, and row 1 of its own report then quoted the resulting half-sentence back
and told the child it was a fragment — **correcting the child for the machine's error.**
That is the failure section 7.3a exists to prevent, arriving by a route 7.3a does not
cover: not a judgement about layout, but an invented error inside the text itself.

### 7.6e Marking quality

IQ4_XS scored 84/100 where both others scored 78, and the report is the equal of
Q4_K_M's: bands consistent with the marks (35/40 to band 4, 32/40 to 4, 17/20 to 4), every
row of *Sentences to improve* carrying a real change, and it independently caught the same
unnatural figure of speech in the closing paragraph that Q4_K_M did.

It also did something neither other run managed: *Notes on the reading* **flagged the
accidentally doubled word as a probable duplication and told the reader to check the
page.** That is exactly the mitigation that section is for, and on this page it was right.
Q4_K_M said "Nothing to report."

Two small regressions against Q4_K_M: 14 rows against 17, and the rows are numbered
`1-6, 8, 10-13, 15, 16, 18, 19` — the model numbered against every sentence in the
composition and emitted only the ones it changed, so the table reads as though rows went
missing. Cosmetic, and internally consistent with the *What to work on* cross-references.

IQ2_XXS's report, by contrast, carries three defects the other two do not: **two rows
where "What was written" and "A stronger version" are identical** (#16 and #18, with
reasons like "The original is fine"), which the prompt forbids; **Language 33/40 labelled
band 3 while Content 32/40 is band 4**, a higher mark in a lower band; and **wrong
grammatical advice** — it told the child they slip between tenses, citing `I groaned`
against `I'm late`, which is inside direct speech and correct.

### 7.6f The corrected versions

Both minimal corrections do the job. Q4_K_M's is the bolder of the two: alongside the
comma splices and the subject-verb agreement it also swaps one verb for a more precise
one, replaces an unnatural figure of speech with the natural image, and supplies a missing
speaker attribution in a line of dialogue.

**Those are minimal corrections and were ruled so by the operator**, on the standard that
matters: they repair the sentence without changing what the child meant. The figure of
speech is a wrong-word error of exactly the kind section 8 names — "a wrong word replaced
with the one clearly meant" — not a stylistic rewrite, and a missing speaker attribution is
a punctuation repair. The distinction section 8 is drawing is not "few changes" but "the
child's meaning, intact"; a rewrite is what risks replacing a paragraph outright or
inventing plot, and neither correction does that.

IQ4_XS's is more conservative: it keeps all three of those as the child wrote them, and
fixes the comma splices, the agreement error, a pronoun that disagreed with its plural
noun, the doubled word and the speech punctuation. It also leaves one comma splice
standing — the same missing speaker attribution — which Q4_K_M repaired. On this script
that is the one place Q4_K_M's correction is the better document.

So: no change needed to `correct_minimal.txt`, and no quality gap between the two quants
here worth acting on. Recorded because an earlier draft of this section had it the other
way round and called Q4_K_M's version a spec failure; it is not one.

The improved rewrite is a genuine reimagining: **zero paragraphs shared with the corrected
version**, new sensory detail and a new closing reflection that appear nowhere in the
original, properly paragraphed, and pitched at about P5. It came back at 426 words against
the 420-word ceiling — 1.5% over, not worth a guard.

### 7.6g The improved rewrite's budget, and why thinking stays on

On both Q4_K_M and IQ4_XS the `correct-improved` call spent its entire 8,000-token budget
on reasoning and returned no answer, then succeeded on the automatic thinking-off retry.
Same failure, same place, at 8.5 and 25 tok/s alike, so it was never the weights.

`max_tokens` covers the reasoning as well as the answer, and the reasoning here is long
and **unbounded**. Every measurement of it, on the same 399-word P4 script:

| thinking | budget | reasoning spent | finish | answer |
|---|---|---|---|---|
| `medium` | 8,000 | 8,000, capped | `length` | **none** |
| `low` | 8,000 | 8,000, capped | `length` | **none** |
| `medium` | 12,000 | 9,815 | `stop` | 398 words |
| `medium` | 14,000 | 10,563 | `stop` | 418 words |
| `medium` | 12,000 (in app) | 10,125 | `stop` | 409 words |
| `medium` | 12,000 (in app) | 12,000, capped | `length` | none → retry gave 407 |
| `medium` | 12,000 (in app) | 12,000, capped | `length` | none → retry gave **201** |
| off | 4,000 | — | `stop` | 402 / 341 / 341 / 456 words |

**`reasoning_effort` is not a control.** `low` spent the identical 8,000 and returned the
identical nothing.

**No budget inside a 16,384 context reliably contains it.** Observed reasoning runs from
9,815 to over 12,000 tokens with no ceiling found. `max_improved_tokens` is now 12,000 —
strictly better than 8,000, and it catches the shorter reasoning — but roughly a third to
a half of calls still hit the cap and fall through to the retry.

### Thinking earns its place, and the evidence is the word count

This was tested precisely because the obvious response to an unreliable feature is to turn
it off. Against the 399-word original and the 105% ceiling of 419:

| | words | in range? |
|---|---|---|
| thinking on | 398, 409, 418 | **3 of 3** |
| thinking off | 402, 341, 341, 456 | **1 of 4** |
| retry after a blown cap | 407, 201 | 1 of 2 |

**Thinking is what holds the word ceiling.** Without it the rewrite came back 15% short
twice and 9% over the ceiling once — breaking section 8 at both ends, which requires that
it never come back shorter and never exceed 105%. With it, three runs landed within 5% of
the original every time. Section 8's claim that the rewrite "has to hold a plot, a target
level, a word ceiling and a paragraph structure in mind at once" is not a rationalisation;
it is measurable, and the word count is where it shows.

Two smaller marks against thinking-off, both on the same runs that came back short: each
opened with an **invented title** on a composition that has none, and one leaked Markdown
italics into the prose. `_keep_title` restores a dropped title; nothing removes an added
one.

On prose quality alone the honest answer is that the single clean thinking-on sample is
the best of them — it dramatises the misread clock rather than announcing it — but one
sample against one sample is not evidence, and the word count is. **Thinking stays on.**

### What is still wrong, and the two ways out

The defect that reaches the child is the retry: when the cap blows, the fallback is a
thinking-off call, which inherits thinking-off's length variance, and one of them returned
**201 words against a 399-word original**. A half-length rewrite is worse than a late one.

1. **A mechanical length check.** Section 8 already establishes the principle for exactly
   this — "the title is restored in code, not asked for in the prompt... where a
   requirement is mechanical, do it mechanically." A rewrite outside roughly 90-105% of
   the original is a failed rewrite and should be re-asked, whatever produced it. This
   fixes the harm without touching the context or the backend.
2. **A larger context.** `ctx_size` 16384 was chosen in CLAUDE.md section 4 on the
   grounds that the largest call is "the whole composition with the rubric and the model's
   thinking (~8k)". That is now out of date: this call wants 1,354 + 12,000 and is the
   largest in the app by a wide margin. Raising the context to 24576 would allow a
   16,000-token budget, but the KV cache grows by ~0.26 GB and the MTP draft context has
   only 474 MB of headroom to give (7.6j) — so it trades against MTP, or against a fully
   resident model on a 16 GB card.

Option 1 is the cheaper and better-targeted fix and does not trade against anything. Not
implemented: it is new behaviour rather than a corrected measurement, and that is the
operator's call.

Note also that `max_improved_tokens` cannot simply be pushed to the context ceiling. At
14,000 only ~2,300 tokens are left for the prompt, and the improved prompt carries the
composition plus two sections lifted off the marking report — comfortable for a 400-word
P4 script, not for a JC2 essay.

### 7.6h Recommendation

**On a card in the 15-16 GB band, IQ4_XS is the better High Quality option**, by a wide
margin: seven to eight times the transcription speed and three times the marking speed of
Q4_K_M, with a transcript differing in three words out of four hundred and a report that
is its equal or better.

That is a different result from section 7.3's finding on IQ3_XXS, and it does not
contradict it. 7.3 measured a *sub-4-bit* quant on a *10 GB* card, where nothing fits and
IQ3 bought nothing but 3.6 GB. IQ4_XS is a 4-bit quant that fits a 16 GB card whole, which
is the only thing that was ever wrong with Q4_K_M here. The step change 7.3 identified —
between sub-4-bit and 4-bit — is intact; IQ4_XS is on the right side of it.

**Adopted, at the operator's instruction.** "High Quality" now names IQ4_XS. The dropdown
is still two models plus Port, so CLAUDE.md section 13 is unaffected — only which file the
first entry points at has changed. What moved:

- `hardware.MODELS` — the `q4_k_m` row becomes `iq4_xs`, `size_gb: 13.54`
- `Q4_MIN_VRAM_MB` renamed `HQ_MIN_VRAM_MB`, value unchanged at 15000; it no longer
  refers to a Q4_K_M-sized model and the old name would have been a lie
- the `hardware.py` module docstring
- `app.js`, which compared `d.recommended === "q4_k_m"` to decide whether the note says
  High or Low Quality — a silent wrong-label bug if left
- `run.bat`'s missing-weights warning
- `DOWNLOAD_MODELS.bat`, which needed a second repository: this quant is not Unsloth's.
  The revision was taken from the HF API and the pinned URL checked for a 302 to the CDN
  carrying the right filename, rather than assumed.

Verified after the change: `choose_key` returns `iq4_xs` at 16311 MiB and `iq2_xxs` at
12288 and 8192; `offload_regex('iq4_xs', 16311)` is empty (fully on the GPU) and at
15000 MiB it is a seven-block offload. The threshold logic needed no change — on a 10 GB
card IQ4_XS would offload heavily and the 15000 gate keeps it off, exactly as it kept
Q4_K_M off.

The two silent corrections in 7.6c are the price, and they are the same class of error
section 6 already documents at every quantisation — one instance more, not a new failure.

### 7.6i MTP was not enabled, and enabling it is worth 1.77x

The model card for this quant quotes "64k context with MTP at 50 t/s" against "128k
without MTP at around 30 t/s" and gives no flag, so the first run here did not have it on.
The server log said so plainly and it was missed:

```
W model has unused tensor blk.64.nextn.eh_proj.weight (size = 27852800 bytes) -- ignoring
```

`blk.64` is the multi-token-prediction layer — the comment above `NUM_LAYERS` in
`hardware.py` already says the 65th block is the MTP layer — and thirteen of those
warnings mean the whole thing was loaded and thrown away.

**The flag in this build is `--spec-type draft-mtp`**, from the speculative-decoding
family (`--spec-type none,draft-simple,draft-eagle3,draft-mtp,...`). With it the thirteen
warnings drop to zero and the server logs
`common_speculative_init_result: creating MTP draft context against the target model`.

Measured, identical request, one server on the card at a time, 2,500 generated tokens:

| | tok/s | VRAM with the projector loaded |
|---|---|---|
| IQ4_XS, no MTP | 25.8 | 14.9 GB |
| **IQ4_XS, `--spec-type draft-mtp`** | **45.7** | **15.7 GB** |

1.77x, which is close enough to the card's 50-against-30 to believe the mechanism.

**A caution about four contaminated readings**, recorded because they nearly became a
finding. Controls came back at 7.8, 9.7, 11.9 and 6.1 tok/s before one came back at 25.8.
Nothing was wrong with the model: the server runs `--parallel 1`, and every one of those
low numbers was taken while a second request — another benchmark of mine, or a second
resident server — was sharing the card. Measured alone it is 25.8, which is also what the
full pipeline had independently logged before any of this.

The lesson is procedural: on this card **one request to one server at a time**, or the
number measures the queue. A 2-4x understatement from concurrency looks identical to the
genuine 3x penalty Q4_K_M pays in 7.6a, and the two are easy to confuse.

**It is gated, because IQ2_XXS has no MTP layer.** Scanning the GGUF tensor names
directly: IQ4_XS and Q4_K_M both carry `blk.64.*`, and the Unsloth IQ2_XXS does not, so
Low Quality would otherwise be handed a flag its weights cannot honour. `model_has_mtp`
reads the file rather than trusting a table, and **two things about that scan cost a
cycle each:**

- **The discriminator must be `blk.64.`, not `nextn`.** The string `nextn` sits around
  offset 1,400 of *both* quantisations, in the architecture metadata — the model declares
  that it has an MTP design whether or not this particular file kept the weights for it.
  A scan for `nextn` enables MTP on IQ2_XXS and is wrong in the dangerous direction.
- **The tensor names are about 11 MB in**, behind the tokenizer vocabulary. An 8 MB
  window was tried first and reported *no MTP layer* for every model on disk — which
  fails silently, costs 1.77x, and looks exactly like a model that simply has no MTP. It
  now reads in chunks to a 64 MB ceiling, overlapping by the pattern length so a name
  straddling a chunk boundary is still found.

It is also off on the CPU build: drafting spends compute to save memory bandwidth, which
is the wrong way round when there is no GPU being starved.

### 7.6j MTP measured on the real pipeline, images included

The benchmark in 7.6i is text-only, so the vision encoder's image buffer was never
allocated — and transcription is the path the whole app rests on. Re-measured through the
app itself on the same two photographs:

| | without MTP | with MTP |
|---|---|---|
| Transcribe | 14, 15 s/page at 17-19 tok/s | **11, 11 s/page at 22-26 tok/s** |
| Marking call | 322 s at 25 tok/s | **150 s at 45 tok/s** |
| Correction | 23 s at 22 tok/s | **at 55 tok/s** |
| Peak VRAM, both pages encoded | — | **15,837 MiB of 16,311** |

**474 MB spare at peak**, sampled every second across both image encodes, and no
allocation failure. `OVERHEAD_GB` stays at 2.0: raising it to cover the draft context
would compute an FFN offload for a model that demonstrably fits, which is the worse
mistake of the two per the note on that constant.

474 MB is not much. It is enough here because the projector, the KV cache at ctx 16384 and
the encode scratch are all sized up front, so the peak is reached on the first page and
does not grow with the composition. A card reporting appreciably under 16 GB takes an FFN
offload instead and has room by construction.

### 7.6k q4_1 KV at 32k context: fits, and is far too slow

Asked whether dropping the KV cache to `q4_1` would pay for a 32k context. The memory
arithmetic says yes and the clock says no, decisively.

**The memory is free.** `q8_0` is 8.5 bits per element and `q4_1` is 5.0, so doubling the
context at 0.59x the cost per element is 1.18x overall — the ~0.53 GB cache becomes
~0.62 GB. Measured with MTP and the projector loaded:

| | VRAM | context |
|---|---|---|
| ctx 16k, KV `q8_0` | 15,837 MiB | 16,384 |
| ctx 32k, KV `q4_1` | **15,759 MiB** | 32,768 |

Doubling the context came out ~78 MB *ahead*, because the other buffers shrink slightly
at the same time. `q4_1` is in this build's allowed list and `n_ctx_slot = 32768` was
granted.

**The speed is not free. It is a 2.7x loss.**

| | `q8_0` @ 16k | `q4_1` @ 32k |
|---|---|---|
| Transcribe | 11 s/page, 22-26 tok/s | 29-31 s/page, **8.1-9.3 tok/s** |
| Marking | 45 tok/s | **14-19 tok/s** |

Almost certainly the CUDA flash-attention kernels: there is a fast path for `q8_0` and
`f16`, and a dequantise-on-the-fly fallback for `q4_1`. Which knob costs it — the cache
type or the context length — was not isolated, because the decision does not turn on it:
`q8_0` at 32k needs about +530 MB of KV against 474 MB of headroom, so it only fits by
giving up MTP, and MTP is worth 1.77x. Either way `q8_0` at 16k wins.

**Reverted at the operator's instruction after two of three test runs.** `ctx_size` stays
16384, `cache_type_k/v` stay `q8_0`.

One useful by-product: a **20,000-token budget did contain the rewrite's reasoning**
(finish `stop`, 6,962 completion tokens). So the unbounded reasoning in 7.6g is bounded
somewhere above 12,000 and below 20,000 — but reaching it needs a 32k context, and at
8-9 tok/s a 20,000-token reasoning block is three-quarters of an hour. The budget was
never the binding constraint; the token rate is. Which leaves the mechanical length check
below as the right fix.

### 7.6l The rewrite's length is now checked, not trusted

Section 7.6g measured the harm: when the improved rewrite's reasoning blows its budget,
the retry is a thinking-off call, and one of those returned 201 words against a 399-word
original. Section 8 gives two length rules — never shorter than the original, never past
105% of it — and both are arithmetic.

`pipeline._hold_the_length` now checks the finished rewrite and re-asks if it misses. The
band is 95-105% of the original, three attempts, all three values in `config.json`. The
floor is 0.95 rather than 1.00 because "not shorter" counted to the word would reject a
rewrite one word down, which is not what the rule is protecting against.

**The retries run with thinking off**, at the operator's instruction, and the economics
agree: a thinking retry is three to four minutes and a plain one is about twenty seconds,
so several cheap attempts buy more than one expensive one. Roughly a quarter of
thinking-off draws land inside the band on their own, which is why there is more than one
attempt.

If no attempt lands, **the closest to the band is returned, not the last** — so a rewrite
2% over the ceiling beats one 20% under the floor. A rewrite slightly outside the band is
a document; no rewrite is not.

This is the same argument section 8 already makes for the title: where a requirement is
mechanical, do it mechanically.

### 7.6m Thinking on the rewrite is now per-model, and off for IQ2_XXS

`thinking.correct_improved` was a single global boolean, so the low-quality quant was
getting the reasoning path too. That is the configuration section 8a records destabilising
it: with a longer instruction IQ2_XXS emitted a hallucinated closing tag, argued with
itself in the output and locked into a repetition loop. Section 7.3 separately measured it
producing a perfectly acceptable grounded rewrite *without* reasoning.

It is also the model chosen for the smallest cards, where ten thousand tokens of thinking
before the first word of output is many minutes.

So `hardware.MODELS` carries `rewrite_thinking`, true for IQ4_XS and false for IQ2_XXS,
and `config.json` remains the master switch: setting `correct_improved` false turns it off
for everything, setting it true does not turn it on for a model that cannot use it. The
external Port option takes the configured default, since the operator chose it and knows
what they are running.

### 7.6n The update button, and the constraint it reverses

CLAUDE.md section 0 said there would be no update button and therefore "no outbound
request, ever". That was reversed at the operator's instruction, and section 0 now records
the reversal rather than having it applied quietly. The exception is narrow: nothing is
requested unless the button is pressed, the only hosts are `api.github.com` and
`github.com`, the download URL's prefix is verified against this app's own repository, and
the child's work is never part of any request. Acceptance test 10 — a full marking run
with the adapter disabled — is unchanged and still required.

The workflow is the operator's UPDATE_BUTTON.md, adapted from an Electron app to a folder
of Python. All five of its traps applied and all five are handled:

| Trap | What it becomes here |
|---|---|
| `spawn` refuses a `.cmd` | `cmd.exe /c <script>` with the path as its own argument, never `shell=True` |
| `tasklist \| find` hangs when detached | wait on the **file lock** of `runtime\python.exe` |
| unzip without a dependency | `%SystemRoot%\System32\tar.exe`, absolute — `tar` on PATH may be Git's GNU tar, which cannot read zip |
| copy with robocopy, not xcopy | `/E /R:3 /W:2`, `if errorlevel 8` as the failure test, and **no `/MIR`** |
| verify before trusting | `APP_VERSION` read out of the downloaded `version.py` by regex, never by importing it |

Two adaptations the guide could not anticipate:

- **The release asset is the source, not the application.** `bin\`, `models\` and
  `runtime\` are 23 GB of payload that an update never needs to touch, and robocopy
  leaves what it does not carry alone. So the asset is a few hundred kilobytes and the
  weights are never re-downloaded.
- **The version is read by regex rather than by import.** Importing would execute a file
  just downloaded off the internet *before* it has been verified, which is the wrong
  order to do those two things in.

`updater.is_our_asset_url` is the one piece with no counterpart in the guide's checklist
and it earns its place: the download URL arrives at the install endpoint from the page,
and the path it feeds ends in code being copied over the application. It is checked
against the repository prefix, and a `..` anywhere in it is refused.

Installing is refused while a job is running. The update ends with this process exiting,
and taking a marking run and its llama-server down behind the user's back is not a thing
to do.

## 8. Acceptance tests (CLAUDE.md section 14)

| # | Test | Status |
|---|---|---|
| 1 | Transcript matches the page including its mistakes; quotes verbatim | **Partly** — quotes verbatim, but silent corrections persist on real handwriting: two misspellings at every quant, plus a verb tense and a possessive on IQ4_XS (sections 6, 7.6c) |
| 2 | Same script at different levels scores differently | **Pass** — 79 / 71 / 39 across P2 / P4 / S4 |
| 3 | Chinese: English feedback, Chinese quotes and rewrites | **Pass** (section 5.6) |
| 4 | Pages reordered in the UI are read in the order shown | **Pass** — verified in the browser: two pages uploaded in reverse, sorted by filename on arrival, reordered with the arrows, numbering followed |
| 5 | Portrait phone photograph with EXIF rotation | **Pass** — two portrait phone photographs of a real P4 script went through the browser resize and were read the right way up, twice (sections 7.6b-c) |
| 6 | A photograph that is not a composition gives a plain message | **Not tested** |
| 7 | Generate with "both" writes two files that differ, and opens the folder | **Pass** after the section 7.1 fix; it failed before it |
| 8 | Cancel during transcription | **Not tested** |
| 9 | Force-quit leaves no orphaned llama-server | **Not tested** — the job object is carried over unchanged from the Meeting Summariser, where it was verified |
| 10 | Runs with the network adapter disabled | **Not tested** — no outbound call exists in the code |
| 11 | Copy the folder to another drive and run | **Not tested** |
| 12 | Deleting an output folder removes it from history | **Pass** — the list is built from the folders |

Tests 6, 8, 10 and 11 are the ones worth running before this is given to anybody. None of
them needs a model download; 6 needs a phone.

---

## 8a. Paragraph indentation and red ink: tried, and withdrawn

Both were asked for and both are gone again. Recorded here so nobody spends the
afternoon on them twice.

**Paragraph indentation.** Singapore school compositions mark a new paragraph by
indenting the first line; there is no blank line, because ruled paper has none
to spare. So a transcript comes back as one unbroken block. Four approaches were
tried: asking for a blank line, asking for a `[P]` marker, asking for one ruled
line per output line with the indents preserved, and asking for nothing at all.

The `[P]` marker worked on a clean blue-ink page — three of three indents, and
the transcript came back correctly paragraphed — and not at all on a pencil page
under heavy red annotation, on the low quantisation and the high one alike. The
line-per-line version made the low model abandon the page and answer
`NO_TEXT_FOUND`.

Partial and unpredictable, so withdrawn. What replaces it is one instruction in
`mark.txt`: **the marking may not comment on or deduct for paragraphing, layout
or neatness**, because none of it survives the reading, and `levels.marks_table`
describes Organisation without naming paragraphs. That is the half worth having
and it costs nothing — a report was observed asking why a composition was in one
block, on a page that was properly paragraphed.

**Red ink.** A teacher's marking should not become the child's words. Q4_K_M
managed the easy half — it ignored marginal comments and an inserted word — but
still absorbed a correction written directly over a word, taking the teacher's
`exploded` for the child's `exploding`. IQ2_XXS copied the teacher's closing
comment into the composition, and the instruction destabilised it badly: on one
page it transcribed correctly to the end, emitted a hallucinated closing tag,
and continued *"Wait, I need to re-examine the image. Let us look at the red
ink. Line 1: … Line 2: …"* before locking into a repetition loop. Another run
opened by announcing that the image was rotated 90 degrees, which it was not.

Withdrawn at the operator's call — it was a nice-to-have, and the guards it
needed (deliberation trimming, repetition truncation, a widened preamble
stripper) were more code than the feature was worth. All of it is gone.

**The lesson worth keeping is about prompt length.** The transcription prompt
had grown to four times its original size, one well-meant rule at a time, and
every failure above appeared only after it did — including a page silently
dropped half the time, because a rule about skipping printed worksheet headers
led the model to call the whole sheet blank. A 2-bit model cannot hold a long
instruction. The reliable prompt is the short one, and it is back to what it was.

One line survives the episode: when a page is rejected, the log now says what
the model actually replied. That turned the next bug from a mystery into a
one-line diagnosis, and it is six lines of logging rather than cleaning.

## 9. Still not measured

- **Real handwriting** — first measured in section 7.6, on one P4 script. Section 6's
  numbers are still font-rendered. The prediction that real handwriting would be worse
  held for IQ2_XXS and did not for the 4-bit quants, which read that page almost exactly.
  One script is not a sample; more pages, more hands, and pencil.
- ~~**A 16 GB card**~~ — measured, section 7.6. The offload is not small: 15 FFN blocks,
  and Q4_K_M runs at a third of the speed of a quant that fits whole.
- **Unified memory.** The CPU path is inherited from the Meeting Summariser and should
  work, but the vision encode on the processor is new and unmeasured. A UMA machine is
  quoted per page in the UI for this reason.
- **Chinese**, end to end, with a real 华文 script.
- **AMD and Intel via Vulkan**, including whether the coopmat workaround is still needed
  for the vision path specifically.
