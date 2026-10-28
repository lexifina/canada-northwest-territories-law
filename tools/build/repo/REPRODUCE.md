# Reproducing this repository

Built on {{TODAY}} from the gnwt.ca PDFs retrieved on {{RETRIEVED}}, by the scripts in `tools/build/` (copied there
as run). The sha256 of every PDF the build read is in [`data/source-sha256.tsv`](data/source-sha256.tsv), so a copy
of those files can be checked byte for byte before rebuilding. How the files were retrieved is not part of this
record. No AI model writes or changes legal text, and none runs at build time; the scripts were written by an AI
coding agent (Claude, Anthropic) working for Lexifina.

| Script | sha256 |
| --- | --- |
| parse.py | `{{SHA_PARSE_PY}}` |
| build.py | `{{SHA_BUILD_PY}}` |
| check_repo.py | `{{SHA_CHECK_REPO_PY}}` |
| test_repo.py | `{{SHA_TEST_REPO_PY}}` |

## Steps

With the retrieved files in place where `build.py` reads them (`CACHE`: the listing as `items.json` and
`pdf_links.json`, the retrieval day in `fetched_on.txt`, and the PDFs under `pdf/`, each matching its sha256 in
`data/source-sha256.tsv`):

```sh
python build.py ../canada-northwest-territories-law --today {{TODAY}}
python check_repo.py ../canada-northwest-territories-law
python test_repo.py ../canada-northwest-territories-law
```

Needs Python 3.12 with PyMuPDF (`pymupdf`) for the build, and pypdf (6.x) for the independent check.

## What the build produced

- {{ACTS}} Acts, {{SECTIONS}} sections, {{UNITS}} section and schedule files.
- {{COMMITS}} commits: one per Act (`Kind: consolidated`) and one documentation commit.
- Consolidation dates from {{FIRST}} to {{LAST}}.
- {{ISSUES}} notes in `data/issues.csv`.

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
python tools/in_force_on.py {{TODAY}} acts/land-titles 46
```

Rebuilding from the same PDFs gives the same `git rev-parse main`.
