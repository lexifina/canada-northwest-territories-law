# Reproducing this repository

Built on {{TODAY}} from the gnwt.ca cache retrieved on {{RETRIEVED}}, by the scripts in `tools/build/` (copied there
as run). No AI model writes or changes legal text, and none runs at build time; the scripts were written by an AI
coding agent (Claude, Anthropic) working for Lexifina.

| Script | sha256 |
| --- | --- |
| fetch.py | `{{SHA_FETCH_PY}}` |
| parse.py | `{{SHA_PARSE_PY}}` |
| build.py | `{{SHA_BUILD_PY}}` |
| check_repo.py | `{{SHA_CHECK_REPO_PY}}` |
| test_repo.py | `{{SHA_TEST_REPO_PY}}` |

## Steps

```sh
python fetch.py list     # the 41 pages of https://gnwt.ca/resources/legislation (20 resources each), one at a time
python fetch.py pages    # one resource page per Act or regulation (798)
python fetch.py pdfs     # the PDF each page links (Acts first); 798 PDFs
python build.py ../canada-northwest-territories-law --today {{TODAY}}
python check_repo.py ../canada-northwest-territories-law
python test_repo.py ../canada-northwest-territories-law
```

Needs Python 3.12 with PyMuPDF (`pymupdf`) for the build, and pypdf (6.x) for the independent check.

## Fetch rate

gnwt.ca's robots.txt sets no Crawl-delay. `fetch.py` asks for the listing pages one at a time, and for resource pages
and PDFs with at most 4 requests in parallel. Near the end of the PDF run, at 4 in parallel, the server answered HTTP
429; the fetcher was then changed to 2 in parallel, and to wait as long as a 429's Retry-After asks (a minute if it
gives none). Each answer is cached under `.cache/` (git-ignored), negative answers too (retried after a day).
[`data/source-sha256.tsv`](data/source-sha256.tsv) lists the sha256 of every PDF the build read.

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

Rebuilding from the same cache gives the same `git rev-parse main`.
