# Composition Marker

<img width="932" height="839" alt="overview" src="https://github.com/user-attachments/assets/9987f5d5-73e8-4740-a9f6-cd7ac94959bd" />
<br>
<br>
<img width="1920" height="621" alt="output" src="https://github.com/user-attachments/assets/3ef22849-6792-4322-bd2e-1e677baca397" />
<br>
<br>

Photograph a child's handwritten composition, choose the education level (Primary 1 to JC2), press **Mark**. You get a
score out of 100, feedback, a list of sentences worth improving with rewrites, and - on
demand - a corrected version or reimagination of the whole composition.

Everything runs on your own computer. The photographs, the handwriting and the marking
never leave the machine, and the application makes no internet connection at all.

---

## Using it

1. Download **`Composition-Marker-vX.Y.Z-full.zip`** from the latest release and unzip it
2. Double click on `DOWNLOAD_MODELS.bat`

The full zip carries the app, the Python runtime and every inference binary, so step 2
only has to fetch the models. It needs internet access; nothing after it does.

After that, you can just copy the entire folder (about 22GB with the models) to other machines and it should work. Then,

1. Double-click **`run.bat`**. A black console window opens and the browser follows a few
   seconds later. Keep the console window open while you work.
2. Drop the photographs of the pages onto the page, or click to choose them.
3. Put them in the right order - drag a page, or use the arrows under it.
4. Choose the **level** (Primary 1 through JC2) and the **language** (English or
   Chinese). Type the **topic** if you know it; it is optional but it makes the marking
   of relevance much better. 
5. Press **Mark**.

When it finishes you get:

- a **Transcript** tab showing exactly what the model read from the photographs
- the **score** out of 100, broken into Content, Language and Organisation
- what went well, and what to work on
- a table of **sentences to improve**, each with a stronger version and the reason
- **Generate** for a corrected version - a minimal correction, an improved rewrite, or
  both. When it finishes, the composition's folder opens with everything in it.

Everything is saved as Markdown in `output\`, one folder per composition. Nothing is
downloaded, because nothing needs to be: the files are already there.

### Photographing the pages

- One page per photograph
- Portrait photographs are fine; the rotation is handled.
- **iPhone users:** set Settings → Camera → Formats → **Most Compatible**. Windows
  browsers cannot open HEIC files, and the app will tell you so if it meets one.

---

## What it needs

| | |
|---|---|
| Windows | 11 |
| Graphics | NVIDIA, AMD or Intel, with a current display driver. It also runs without one, just not recommended. |
| Video memory | 8 GB uses the Low Quality model; 16 GB or more uses High Quality. Detected automatically. |
| Disk | About 25 GB |

The model choice is detected from the graphics card at startup and shown on the first
screen. You can override it in the dropdown.

---

## A word about the marking

The score is a band judgement in the style of MOE marking - Content 40, Language 40,
Organisation 20 - measured against what is expected at the level you chose. **It is not
an official marking scheme and should not be read as a predicted grade.** It is a
consistent second opinion, and it is most useful for the things it points at: the
sentences it lists, the tense slips it catches, the paragraph that does two jobs at once.

The **improved rewrite** is the one place where the two models differ noticeably. On High
Quality it is a genuine model answer - a new opening, added detail, stronger verbs. On Low
Quality it applies the specific fixes the marking found but does less of its own writing.
The **minimal correction** is reliable on both.

The handwriting is read by a machine, and it does make mistakes. Check the **Transcript**
tab against the page before taking a language deduction seriously - it is the first tab
for that reason - and the report has a *Notes on the reading* section where the model
flags words it suspects it misread.

---

## For the operator

`config.json` holds every tunable. There is no settings screen. The values marked
`"auto"` are detected per machine and an explicit value always wins.

`paths.models_dir` can point somewhere else, so a machine that already holds these model
files for another application does not need a second copy.

The source is MIT licensed (`LICENSE`); `THIRD_PARTY_NOTICES.md` covers the components
the release zip carries. `CLAUDE.md` is the specification. `BUILD_NOTES.md` records what was measured, what was
verified against the shipped binaries, and where the implementation deviates from the
specification and why.
