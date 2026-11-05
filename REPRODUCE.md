# Reproducing this repository

The original baseline was built on 2026-10-29 from the gnwt.ca PDFs retrieved on 2026-10-28.
The first table records the original baseline script hashes; the current synchronized tools are listed below. The sha256 of every PDF the build read is in [`data/source-sha256.tsv`](data/source-sha256.tsv), so a copy
of those files can be checked byte for byte before rebuilding. How the files were retrieved is not part of this
record. No AI model writes or changes legal text, and none runs at build time; the scripts were written by an AI
coding agent (Claude, Anthropic) working for Lexifina.

| Script | sha256 |
| --- | --- |
| parse.py | `2596a7591679c194136f6f598bbbee1dec4c85e3e7c20a139e6bc2996794d3b2` |
| build.py | `928f6458ce627a5a0529a262f08639463fc368230da8b8b6fd467cbd3b2e825e` |
| check_repo.py | `03d912a6c9b29d08d9f94ff062cd5da3246b2a3c23db6c1ca666826a0c0b6461` |
| test_repo.py | `36163023caa6e86ccd4f47b49235bc65b8c2bf430383f208d4f87606f85fa30b` |

## Steps

With the retrieved files in place where `build.py` reads them (`CACHE`: the listing as `items.json` and
`pdf_links.json`, the retrieval day in `fetched_on.txt`, and the PDFs under `pdf/`, each matching its sha256 in
`data/source-sha256.tsv`):

```sh
python build.py ../canada-northwest-territories-law --today 2026-10-29
python check_repo.py ../canada-northwest-territories-law
python test_repo.py ../canada-northwest-territories-law
```

Needs Python 3.12 with PyMuPDF (`pymupdf`) for the build, and pypdf (6.x) for the independent check.

## What the build produced

- 238 Acts, 12022 sections, 12059 section and schedule files.
- 239 commits: one per Act (`Kind: consolidated`) and one documentation commit.
- Consolidation dates from 2004-02-17 to 2025-09-01.
- 39 notes in `data/issues.csv`.

## How the PDFs are read

`parse.py` reads the English column (x < 312 pt) of each page with PyMuPDF, character by character. A space glyph at
least 0.2 em wide is a word space; narrower ones are the filler the typesetter puts after every letter of a
letter-spaced justified line, and are dropped. Marginal notes (x < 92 pt) become `> ` lines before the paragraph
beside them. Paragraphs break at a vertical gap, at a paragraph marker at its own indent, and at page tops only where
a marker, heading or "Whereas" begins. The table of contents (one run of pages, from its heading to the first page
with a marginal note), the "not an official statement" notice, page numbers and the printer's colophon are skipped.

The title page runs to the "not an official statement" notice (a long AMENDED BY list ends above the notice on its
own page). A marginal note beside a heading (e.g. "COLUMN ONE" beside "COLUMN TWO" in a schedule) is kept as a `> `
line. A preamble page ("Whereas ...") ends the table of contents.

The independent check (`check_repo.py`) does not use PyMuPDF. It reads each page with pypdf's content-stream parser
and font tables, through a text-state machine of its own that places every glyph by its own advance width, and keeps
the glyphs that start left of x 312 pt (or continue an English word, touching the glyph before it, up to 340 pt).
Placing each glyph matters: a centred bilingual heading is one text operator whose French half starts right of the
gutter, and newer PDFs show one glyph per string.

## Checks

```sh
python tools/build/check_repo.py .   # every Act: letters and digits of the repo == those of the raw PDF (pypdf)
python tools/build/test_repo.py .    # blame, the on-date tool, one commit per Act, noon dates, ledgers, README commands
git rev-list --merges --count main                                   # 0
git log --format=%B main | tr -cd '\r' | wc -c                       # 0
python tools/in_force_on.py 2026-10-29 acts/land-titles 46
```

Rebuilding from the same PDFs gives the same `git rev-parse main`.


## Délîne historical commencement additions (2026-11-05)

Two correction snapshots extend published baseline
`aca9da1d1b990ea21e6ef337e873ed8d302de19a`: section 5 alone on 2015-03-12,
and the complete Act on 2016-09-01. Every current Act file retains its baseline bytes.
The original build describes the baseline. Its original functional test is preserved as
`tools/build/test_repo_baseline.py`. The shipped `tools/build/test_repo.py` validates the complete
history: one ordinary baseline per Act, every explicitly registered correction and its full
provenance/interval/path coverage, and every documentation commit.

Official sources:

- Certified Act: https://gnwt.ca/sites/default/files/legacy_justice/files/bills/17/2015.5/bill_46.pdf
  (SHA256 `25b6033e1dc91f585e60519ff7dc40672371569240979675a3af33ed55b4e194`).
- Consolidation: https://gnwt.ca/sites/default/files/legacy_justice/files/legislation/deline-final-self-government-agreement/deline-final-self-government-agreement.a.pdf
  (SHA256 `e3ba058027ee1efbfa4659143027381f399d0cc9f1d94870789c680acfcd1fa7`).

The consolidation prints the two commencement dates; certified section 20 specifies section 5 on
assent and the remainder by proclamation. The source's 2013 deemed effect is retained in section 5.
Certified pages 3–10 running text and headings were compared independently with pypdf glyph positions
against the unchanged published running text: 12,666 characters, exactly equal after line-wrap
normalization, SHA256 `3958806b7b68cb4fba118f1ba1fd6bb65fec1ca122a2337802c2c5112011e8e1`.
The current consolidation comparison returned 11,331 letters/digits, missing 0, extra 0, out of order 0.
Both PDFs have zero normative images.

`tools/append_deline_history.py` (SHA256 `5fe4f29b82424f8e909adffbb2a50292fdac784afc0ae1387370c609e365b810`) verifies both source hashes and these
independent text comparisons before appending. Given a clean scratch clone with main at the baseline,
run the script from this record against that scratch clone and the two official PDFs:

```sh
python tools/append_deline_history.py --repo <baseline-scratch> --official-current <consolidation.pdf> --official-certified <certified-act.pdf>
```

The reproduced law commits are `8718c971424dbc0fa747544a25e1635039e951b2` and
`346b26aabea61d23c5fb850fad85953eda844139`. A separate scratch replay reproduced both hashes.
The documentation commit describes those states and updates the date reader; it does not change law text.
The six boundary/current-date checks passed, including no snapshot before 2015-03-12, section 5 only
through 2016-08-31, and the unchanged complete Act from 2016-09-01.
The required functional gate is the unfiltered `python -X utf8 tools/build/test_repo.py .`.
It checks every commit, including both documentation commits and the two historical corrections.
`data/stats.json` records the current history checked day separately from the baseline checked day
and the original baseline retrieval day; every commit must be on or before that checked day.
Ordinary baselines retain chronological ordering, while explicitly registered historical corrections
are checked against their source-derived legal intervals. All authors and committers are checked.
The current functional-gate SHA256 is `6d8c4def6ae1938498073097ce45966579c8c07e05a421362d4316a1a502f7cb`. GPT-6 selected the two commencement states and implemented the reader and checks;
zero legal words were generated or corrected, and no OCR was used. No model runs in the replay script.

The ordinary-baseline uniqueness, noon/date/future/merge/CR checks and publisher authorship rules
remain enforced. Historical corrections must match the declared commit, Act path, date interval,
complete Files set, index, source URLs and in-force basis; undeclared or malformed corrections fail.

## Current own-builder tools (2026-11-05)

These files match the own-builder sources and its reader template. The baseline builder includes
the archived original test and the separate historical append script in its documentation output.
The baseline builder produces the baseline; the append script then adds the two verified snapshots
to the published baseline as described above. It does not rewrite published history.

| Shipped script | SHA256 |
| --- | --- |
| `tools/build/test_repo.py` | `6d8c4def6ae1938498073097ce45966579c8c07e05a421362d4316a1a502f7cb` |
| `tools/build/test_repo_baseline.py` | `36163023caa6e86ccd4f47b49235bc65b8c2bf430383f208d4f87606f85fa30b` |
| `tools/build/build.py` | `b1e562f38d9cedd1feeb205282427ae0111db0a557d555feb485d8dc58c02498` |
| `tools/build/parse.py` | `2596a7591679c194136f6f598bbbee1dec4c85e3e7c20a139e6bc2996794d3b2` |
| `tools/build/check_repo.py` | `03d912a6c9b29d08d9f94ff062cd5da3246b2a3c23db6c1ca666826a0c0b6461` |
| `tools/append_deline_history.py` | `5fe4f29b82424f8e909adffbb2a50292fdac784afc0ae1387370c609e365b810` |
| `tools/in_force_on.py` | `591f41b9bd821fa21174ebf9b71ded0d3a768ccce6dfbc36348d0135917d39d6` |
