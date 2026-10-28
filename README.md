# Northwest Territories law, change by change

The consolidated Acts of the Northwest Territories as a git repository: 238 Acts, 12022 sections, one
file per section, schedule or form, one paragraph per line, so `git blame` and `git diff` work line by line. The
source is the office consolidation prepared by the Legislation Division of the Department of Justice, Government of
the Northwest Territories, published as bilingual PDFs at [gnwt.ca/resources/legislation](https://gnwt.ca/resources/legislation)
(formerly justice.gov.nt.ca/en/legislation). Only the English text is taken.

**What this repository is not, yet: a history.** The Legislation Division publishes only the current consolidation of
each Act and keeps no earlier versions online. So each Act is a single commit holding its whole current text
(`Kind: consolidated`), and no line is credited to an amending Act. New consolidations will be added as later
commits, so the history grows forward from here. The amendment history the consolidation prints is kept: each Act's
AMENDED BY list is in its `title.md`, the source note after each provision ("SNWT 2009,c.12,s.11(2).") stays in the
text, and both are listed in [`data/amendments.csv`](data/amendments.csv).

The record holds 238 Acts, consolidated between 2004-02-17 and 2025-09-01, in 239 commits.

This is not an official version: the consolidations themselves say they are "not an official statement of the law"
(see [Licence](#licence)).

## The questions, as git commands

| Question | Command |
| --- | --- |
| What does section 46 of the Land Titles Act say? | `python tools/in_force_on.py 2026-10-29 acts/land-titles 46` |
| The whole Act, in order | `python tools/in_force_on.py 2026-10-29 acts/land-titles` |
| Which Acts amended it, and when they came into force | `cat acts/land-titles/title.md` |
| Which Acts a section's source note names | `tail -1 acts/land-titles/s-045.md` |
| Every provision whose note cites one amending Act | `grep -F 'SNWT2009,c.12' data/amendments.csv` |
| Which sections refer to section 59.2 of the same Act | `git grep -E '[Ss]ections? 59\.2([^0-9.]\|\.[^0-9]\|$)' -- acts/land-titles` |
| What changed in an Act since this build (after later builds) | `git log -p -- acts/land-titles` |
| The list of Acts, with dates and sources | [`laws.csv`](laws.csv) |

Each section file starts with any headings above it (`## PART III REGISTRATION`). Each marginal note is on its own
line starting `> `, just before the paragraph it stands beside. Then come the paragraphs, one per line: `46. (1) ...`,
`(2) ...`, `(a) ...`, and definitions (`"judge" means a judge of the Supreme Court; (juge)`, with the French term the
consolidation prints). `title.md` is the title page; `intro.md` is the long title, preamble and enacting words;
`index.txt` lists the files in the order of the Act.

## The dates, and which one we use

The consolidations carry no "current to" date. Each PDF does carry the day it was made (its CreationDate), and that
is the consolidation date (`Consolidated:`). Some PDFs were made a few days before an amendment they already contain
came into force (the AMENDED BY list gives its in-force day); for those the text is the law only from that later day.
So **each commit is dated by its `In-force:` day**: the PDF's creation day, or the latest in-force day the AMENDED BY
list gives when that is later (`Date-source: latest-listed-in-force`; 39 Acts, listed in `data/issues.csv`).
Commits are at noon in Yellowknife (Mountain Time: UTC-7, UTC-6 in daylight time, hard-coded Canadian rules). The
day says "this text, as consolidated, was the law from then"; it is not the day any one amendment took effect. The
in-force dates the title page lists are kept as text, and the latest one is in the `Latest-listed-in-force:` trailer.
`tools/in_force_on.py` answers for any date on or after an Act's consolidation date; for an earlier date it says
there is no version, because the source has none.

## What "author" means

Every law-text commit has the same author, `Legislation Division, Department of Justice (Government of the Northwest
Territories) <legislation-division@justice-gov-nt-ca.invalid>`: the publisher of the consolidation, not a lawmaker. The
address cannot exist. When a later consolidation changes the text, the change will be credited to an amending Act only
where a new source note names exactly one; otherwise it is labelled and authored by the publisher.

The AMENDED BY lists have 1752 entries; the source notes carry 7096 citations.

## What it does not show

- Any text before the current consolidation, and the Acts as enacted (the Annual Statutes are separate PDFs).
- The French text (equally authoritative; the right-hand column of each PDF).
- Regulations (the 238 Acts come first; the 559 consolidated regulations on the same site are not yet included).
- Acts the Legislation Division does not consolidate: appropriation, loan-authorization and write-off Acts and other
  Acts of temporary or limited effect.
- Pictures, tables as tables (their cells are read as lines), and italics.
- 39 Acts have a note in [`data/issues.csv`](data/issues.csv) (for example, an AMENDED BY in-force date
  after the PDF's creation date).

## How it is checked

`tools/build/` holds the build as it was run; [`data/source-sha256.tsv`](data/source-sha256.tsv) has the sha256 of
every PDF it read. The build is deterministic: from the same PDFs it gives the same commit hashes. See
[`REPRODUCE.md`](REPRODUCE.md) for the checks: an independent letters-and-digits comparison of every Act against the
raw PDF (read with pypdf, not PyMuPDF, which the build's parser uses), the on-date tool, no merges, no backwards or future dates, no
CRs in messages, and an identical rebuild.

## Licence

Source: Government of the Northwest Territories, Department of Justice, Legislation Division, consolidated Acts at
https://gnwt.ca/resources/legislation. The consolidations page says: "The legislative material in the consolidations
may be used for a non-commercial purpose without seeking permission, provided that it is accurately reproduced and
includes an acknowledgment of the Government of the Northwest Territories as its source. Reproduction of the
legislative material is permitted, in whole or in part, and by any means." The words were not changed; they were only
split into files and lines. Commercial use needs the Government's consent. **This repository is private.** Lexifina's
own contributions (tools, arrangement, commit messages) are CC0. See [`LICENSE`](LICENSE) and [`SOURCES.md`](SOURCES.md).
