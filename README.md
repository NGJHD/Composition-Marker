# Composition Marker

Photograph a child's handwritten composition, choose the level, press **Mark**. You get a
score out of 100, feedback, a list of sentences worth improving with rewrites, and — on
demand — a corrected version you can download.

Everything runs on your own computer. The photographs, the handwriting and the marking
never leave the machine, and the application makes no internet connection at all.

---

## Using it

1. Double-click **`run.bat`**. A black console window opens and the browser follows a few
   seconds later. Keep the console window open while you work.
2. Drop the photographs of the pages onto the page, or click to choose them.
3. Put them in the right order — drag a page, or use the arrows under it.
4. Choose the **level** (Primary 1 through JC2) and the **language** (English or
   Chinese). Type the **topic** if you know it; it is optional but it makes the marking
   of relevance much better. The level and language come back to whatever you marked
   last time.
5. Press **Mark**.

When it finishes you get:

- a **Transcript** tab showing exactly what the model read from the photographs
- the **score** out of 100, broken into Content, Language and Organisation
- what went well, and what to work on
- a table of **sentences to improve**, each with a stronger version and the reason
- **Generate** for a corrected version — a minimal correction, an improved rewrite, or
  both. When it finishes, the composition's folder opens with everything in it.

Everything is saved as Markdown in `output\`, one folder per composition. Nothing is
downloaded, because nothing needs to be: the files are already there.

### Photographing the pages

- One page per photograph, taken square-on, in good light.
- Portrait photographs are fine; the rotation is handled.
- **iPhone users:** set Settings → Camera → Formats → **Most Compatible**. Windows
  browsers cannot open HEIC files, and the app will tell you so if it meets one.

---

## First-time setup

Everything is in the folder except the two language models, which are 24 GB between them
and too large to ship. On a machine with internet access, run
**`DOWNLOAD_MODELS.bat`** once. It fetches those two files and nothing else, skips
anything already present, and can be re-run safely if the connection drops.

You can then copy the whole folder to a computer with no internet connection at all and
run it there.

Nothing is installed. Nothing is written outside the folder. To remove the application,
delete the folder.

---

## What it needs

| | |
|---|---|
| Windows | 11 |
| Graphics | NVIDIA, AMD or Intel, with a current display driver. It also runs without one, more slowly. |
| Video memory | 8 GB uses the Low Quality model; 16 GB or more uses High Quality. Detected automatically. |
| Disk | About 30 GB |

The model choice is detected from the graphics card at startup and shown on the first
screen. You can override it in the dropdown.

---

## A word about the marking

The score is a band judgement in the style of MOE marking — Content 40, Language 40,
Organisation 20 — measured against what is expected at the level you chose. **It is not
an official marking scheme and should not be read as a predicted grade.** It is a
consistent second opinion, and it is most useful for the things it points at: the
sentences it lists, the tense slips it catches, the paragraph that does two jobs at once.

The handwriting is read by a machine, and it does make mistakes. Check the **Transcript**
tab against the page before taking a language deduction seriously — it opens first for
that reason, and the report has a *Notes on the reading* section where the model flags
words it suspects it misread.

---

## For the operator

`config.json` holds every tunable. There is no settings screen. The values marked
`"auto"` are detected per machine and an explicit value always wins.

`paths.models_dir` can point somewhere else, so a machine that already holds these model
files for another application does not need a second copy.

`CLAUDE.md` is the specification. `BUILD_NOTES.md` records what was measured, what was
verified against the shipped binaries, and where the implementation deviates from the
specification and why.
