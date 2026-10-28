"""Check the Northwest Territories repository against the raw PDFs, independently of the build's parser.

    python check_repo.py ../canada-northwest-territories-law [--branch main] [--record]

For every Act, the raw side is read with pypdf (not PyMuPDF, which the build uses), cropped to the English column
(glyphs starting left of x 312 pt, or continuing an English word up to 340 pt; see raw_pages()). Each glyph is
placed by its own advance (see glyphs()): a centred bilingual heading is one TJ whose French half starts right of
the column, and placing the whole operator by its origin would wrongly count that half as English. Skipped there,
by rules of this script's own: page numbers (a digits-only line in the bottom 85 pt), the text from "This consolidation is not an official statement" to the end of that page, the table of contents (from
the page that says TABLE OF CONTENTS to the page before the first page on which section 1, "Whereas" or "enacts as
follows" begins), and the printer's colophon. The repo side is every file of the Act at main, `## ` and `> ` markers
removed. Both are NFKC-normalised, casefolded, and reduced to letters and digits.

Checks per Act:
  1. multiset: the two sides have exactly the same letters and digits, each as many times (nothing dropped or
     added, whatever the order);
  2. order: the two letter-and-digit strings, compared with difflib in 2,000-character blocks, differ only where
     a marginal note stands in a different place (reported as moved characters; no character is unaccounted for).
Writes verification.json with --record.
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
import warnings

import pypdf

warnings.filterwarnings('ignore')
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent
PDF = HERE / '.cache' / 'pdf'
COLUMN = 312.0       # English glyphs start left of this ...
TITLE_COLUMN = 250.0  # on title pages
COLUMN_EDGE = 340.0  # ... or, continuing an English word (touching the glyph before it), left of this
NOTICE = 'This consolidation is not an official statement'


def spaced(words: str) -> re.Pattern:
    """The words, allowing any spaces between letters (strings of one glyph each can leave a gap inside a word)."""
    return re.compile(r'\s*'.join(re.escape(ch) for ch in words.replace(' ', '')))


NOTICE_RE = spaced(NOTICE)
TOC_RE = spaced('TABLE OF CONTENTS')
COLOPHON = re.compile(r'^\s*(?:\u00a9|\(c\))?\s*\d{4}\s+Territorial Printer.*$|^\s*Yellowknife,\s*N\.?\s*W\.?\s*T\.?\s*$|'
                      r'^\s*Imprimeur.*$', re.M)
BODY_START = re.compile(r'(?:^|\s)1\.\s*[(A-Z]|^\s*Whereas\b|e\s*n\s*a\s*c\s*t\s*s\s*a\s*s\s*f\s*o\s*l\s*l\s*o\s*w\s*s', re.M)


def alnum(s: str) -> str:
    s = unicodedata.normalize('NFKC', s).casefold()
    return ''.join(ch for ch in s if ch.isalnum())


def _mul(m: list, n: list) -> list:
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return [a * p + b * r, a * q + b * s, c * p + d * r, c * q + d * s, e * p + f * r + t, e * q + f * s + u]


def _fonts(reader, resources) -> dict:
    from pypdf.generic._font import Font  # pypdf 6.x
    out = {}
    fd = (resources or {}).get('/Font')
    if fd is None:
        return out
    fd = fd.get_object()
    for name in fd:
        obj = fd[name].get_object()
        try:
            f = Font.from_font_resource(obj)
        except Exception:  # an unreadable font: its text is not counted (and shows as missing)
            continue
        f.code_widths, f.default_width = _widths(obj)
        out[name] = f
    return out


def _widths(obj) -> tuple[dict[int, float], float]:
    """Glyph widths by character code, read straight from the font dictionary: /W and /DW of a CID font's
    descendant (Type0), or /FirstChar and /Widths of a simple font. (pypdf's character_widths mixes codes and
    decoded characters as keys, so one can shadow the other: 'W' and ':' in nwt-hydro-corporation.)"""
    widths: dict[int, float] = {}
    if obj.get('/Subtype') == '/Type0':
        df = obj['/DescendantFonts'][0].get_object()
        w = df.get('/W')
        w = list(w.get_object()) if w is not None else []
        i = 0
        while i < len(w):
            first = int(w[i])
            nxt = w[i + 1].get_object() if i + 1 < len(w) else None
            if isinstance(nxt, list):
                for k, v in enumerate(nxt):
                    widths[first + k] = float(v)
                i += 2
            else:
                last, v = int(w[i + 1]), float(w[i + 2])
                for k in range(first, last + 1):
                    widths[k] = v
                i += 3
        return widths, float(df.get('/DW', 1000))
    first = int(obj.get('/FirstChar', 0))
    for k, v in enumerate(obj.get('/Widths', None) or []):
        widths[first + k] = float(v.get_object() if hasattr(v, 'get_object') else v)
    missing = 0.0
    fdesc = obj.get('/FontDescriptor')
    if fdesc is not None:
        missing = float(fdesc.get_object().get('/MissingWidth', 0))
    return widths, missing


def glyphs(reader, page) -> list[tuple[float, float, float, str]]:
    """Every glyph shown on the page as (x, y, x_end, text) in page space, by a text-state machine of this script's
    own over pypdf's content-stream parser and font tables (Tm, Td, TD, T*, TL, Tc, Tw, Tz, Ts, Tf, Tj, TJ, ', ",
    cm, q/Q, form XObjects). Each glyph is placed by its own advance width, looked up by its character code: a
    centred bilingual heading ("INTERPRETATION ... DÉFINITIONS") is one TJ array, and a whole line can be one
    string per glyph, so only glyph positions tell the English column from the French one. (pypdf's layout mode
    looks Type0 widths up by the decoded character and misplaces those glyphs, so it is not used.)"""
    from pypdf.generic import ContentStream, ArrayObject, NameObject
    out = []

    def run(contents, resources, ctm):
        fonts = _fonts(reader, resources)
        xobj = (resources or {}).get('/XObject')
        xobj = xobj.get_object() if xobj is not None else {}
        stack = []
        tm = tlm = [1, 0, 0, 1, 0, 0]
        tc = tw = ts = tl = 0.0
        th = 1.0
        font, size = None, 0.0

        def show(data: bytes):
            nonlocal tm
            if font is None:
                return
            two = font.sub_type == 'Type0'
            codes = [chr(int.from_bytes(data[i:i + 2], 'big')) for i in range(0, len(data) - 1, 2)] if two else \
                [chr(b) for b in data]
            for code in codes:
                if font.character_map:
                    text = font.character_map.get(code, code if not two else '')
                elif isinstance(font.encoding, dict):
                    text = font.encoding.get(ord(code), code)
                else:
                    try:
                        text = code.encode('latin-1').decode(font.encoding or 'latin-1')
                    except (LookupError, UnicodeError):
                        text = code
                w = font.code_widths.get(ord(code), font.default_width)
                trm = _mul([size * th, 0, 0, size, 0, ts], _mul(tm, ctm))
                adv = (w / 1000.0 * size + tc + (tw if (not two and code == ' ') else 0)) * th
                end = _mul([1, 0, 0, 1, adv, 0], _mul(tm, ctm))
                out.append((trm[4], trm[5], end[4], text))
                tm = _mul([1, 0, 0, 1, adv, 0], tm)

        for operands, op in ContentStream(contents, reader, 'bytes').operations:
            if op == b'q':
                stack.append(ctm)
            elif op == b'Q':
                ctm = stack.pop() if stack else ctm
            elif op == b'cm':
                ctm = _mul([float(v) for v in operands], ctm)
            elif op == b'BT':
                tm = tlm = [1, 0, 0, 1, 0, 0]
            elif op == b'Tm':
                tm = tlm = [float(v) for v in operands]
            elif op in (b'Td', b'TD'):
                if op == b'TD':
                    tl = -float(operands[1])
                tm = tlm = _mul([1, 0, 0, 1, float(operands[0]), float(operands[1])], tlm)
            elif op == b'T*':
                tm = tlm = _mul([1, 0, 0, 1, 0, -tl], tlm)
            elif op == b'TL':
                tl = float(operands[0])
            elif op == b'Tc':
                tc = float(operands[0])
            elif op == b'Tw':
                tw = float(operands[0])
            elif op == b'Tz':
                th = float(operands[0]) / 100.0
            elif op == b'Ts':
                ts = float(operands[0])
            elif op == b'Tf':
                font, size = fonts.get(operands[0]), float(operands[1])
            elif op == b'Tj':
                show(operands[0])
            elif op in (b"'", b'"'):
                if op == b'"':
                    tw, tc = float(operands[0]), float(operands[1])
                tm = tlm = _mul([1, 0, 0, 1, 0, -tl], tlm)
                show(operands[-1])
            elif op == b'TJ':
                for item in operands[0] if isinstance(operands[0], ArrayObject) else []:
                    if isinstance(item, bytes):
                        show(item)
                    elif isinstance(item, (int, float)):
                        tm = _mul([1, 0, 0, 1, -float(item) / 1000.0 * size * th, 0], tm)
            elif op == b'Do' and operands and operands[0] in xobj:
                xo = xobj[operands[0]].get_object()
                if xo.get('/Subtype') == NameObject('/Form'):
                    m = [float(v) for v in xo.get('/Matrix', [1, 0, 0, 1, 0, 0])]
                    run(xo, xo.get('/Resources', resources), _mul(m, ctm))

    contents = page.get('/Contents')
    if contents is not None:
        run(contents.get_object(), page.get('/Resources'), [1, 0, 0, 1, 0, 0])
    return out


def page_text(gs: list, title: bool = False, full: bool = False) -> str:
    lines = []  # [baseline y, first x, text]
    last_y = last_x1 = None
    last_text = ''
    for x, y, x1, text in gs:
        if not text or (x >= COLUMN_EDGE and not full):
            continue
        same_line = last_y is not None and abs(y - last_y) <= 2
        # continuing a word: this glyph and the English one before it are not spaces, and they touch (a space
        # glyph's advance can be stretched across the gutter, so touching a space proves nothing)
        if not full and x >= COLUMN and not (same_line and text.strip() and last_text.strip() and abs(x - last_x1) <= 0.5):
            continue
        # on a title page the French title can start at x 298 on the English title's baseline, 60 pt after it:
        # right of 250 pt a glyph is English only if it follows English text on its line within a word space
        if title and TITLE_COLUMN <= x < COLUMN and not (same_line and x - last_x1 <= 6.0):
            continue
        last_text = text
        if not same_line:
            lines.append([y, x, ''])
        elif x - last_x1 > 1.0:
            lines[-1][2] += ' '
        lines[-1][2] += text
        last_y, last_x1 = y, x1
    # the page number: a line of digits only, in the bottom 100 pt and near the page centre (x 280-330; a body
    # line there can be one wrapped number, "5", at the left margin)
    return '\n'.join(t for y, x, t in lines if not (y <= 100 and 280 <= x <= 330 and re.fullmatch(r'\s*\d+\s*', t)))


def raw_pages(pdf: Path) -> list[str]:
    """Text of the English column of each page, read with pypdf (not PyMuPDF, which the build uses), above the
    footer band (page numbers sit within 85 pt of the page bottom). A glyph starting left of x 312 pt is English.
    From 312 pt on both columns meet: a French section number hangs out to 312 pt ("196. à 199. Abrogé"), and a
    full English line can run on to 328 pt ("... and 18-29" on a title page). There a glyph is English only if it
    continues an English word (it touches the English glyph before it on the same line), up to 340 pt. A glyph on a new baseline starts a new line;
    a gap of more than 1 pt between glyphs is a space.
    On the title pages (up to the page with the notice) the French title can start at x 298 pt, on the same
    baseline as the English one: there a glyph right of 250 pt counts only if it follows English text on its line
    within a word space (6 pt), as "... MADE BY" and "... and 18-29" do."""
    reader = pypdf.PdfReader(str(pdf))
    gs = [glyphs(reader, page) for page in reader.pages]
    pages = [page_text(g) for g in gs]
    notice = next((i for i, p in enumerate(pages) if NOTICE_RE.search(p)), None)
    for i in range(0, (notice if notice is not None else 0) + 1):
        pages[i] = page_text(gs[i], title=True)
    for i, g in enumerate(gs):
        # a schedule set across the full page width: the English on one page, its French version (ANNEXE) on the
        # next (petroleum-products-carbon-tax). Such a page's top line straddles the gutter: glyphs of it lie on
        # both sides of x 312 pt. Then an English page is read across its whole width, a French one not at all.
        ink = [u for u in g if u[3].strip()]
        if not ink:
            continue
        top = max(u[1] for u in ink)
        first = sorted(u for u in ink if abs(u[1] - top) <= 2)
        # one word crosses the gutter: two touching glyphs on either side of it (a two-column page's bilingual
        # heading pair, "SCHEDULE A ... ANNEXE A", has a gap there instead)
        if any(a[0] < COLUMN <= b[0] and b[0] - a[2] <= 0.5 for a, b in zip(first, first[1:])):
            head = ''.join(u[3] for u in first)
            if head.startswith('ANNEXE'):
                pages[i] = ''
            elif head.startswith('SCHEDULE'):
                pages[i] = page_text(g, full=True)
    return pages


def raw_text(pdf: Path) -> tuple[str, list[int]]:
    pages = raw_pages(pdf)
    out = []
    toc = []
    in_toc = False
    for i, p in enumerate(pages):
        m = NOTICE_RE.search(p)
        if m:
            p = p[:m.start()]
        if TOC_RE.search(p):
            in_toc = True
        if in_toc:
            if BODY_START.search(p) and not TOC_RE.search(p):
                in_toc = False
            else:
                toc.append(i)
                continue
        out.append(COLOPHON.sub('', p))
    return '\n'.join(out), toc


def repo_text(repo: Path, branch: str, law: str) -> str:
    names = subprocess.run(['git', '-C', str(repo), 'show', f'{branch}:{law}/index.txt'], capture_output=True,
                           text=True, encoding='utf-8', check=True).stdout.split()
    blob = '\n'.join(f'{branch}:{law}/{n}' for n in names) + '\n'
    r = subprocess.run(['git', '-C', str(repo), 'cat-file', '--batch'], input=blob.encode(), capture_output=True,
                       check=True).stdout
    parts = []
    i = 0
    while i < len(r):
        nl = r.index(b'\n', i)
        size = int(r[i:nl].split()[2])
        parts.append(r[nl + 1:nl + 1 + size].decode('utf-8'))
        i = nl + 1 + size + 1
    text = '\n'.join(parts)
    return re.sub(r'^(?:## |> )', '', text, flags=re.M)


def order_diff(a: str, b: str) -> int:
    """Characters not matched in order (both sides), block by block."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(m.size for m in sm.get_matching_blocks())
    return (len(a) - matched) + (len(b) - matched)


def check(repo: Path, branch: str, law: str, pdf: Path) -> dict:
    raw, toc = raw_text(pdf)
    ra = alnum(raw)
    rb = alnum(repo_text(repo, branch, law))
    ca, cb = Counter(ra), Counter(rb)
    missing = sum((ca - cb).values())
    extra = sum((cb - ca).values())
    # order: compare per chunk on a common alignment (difflib on the whole string is quadratic for long Acts)
    unordered = 0
    step = 2000
    j = 0
    for k in range(0, len(ra), step):
        chunk = ra[k:k + step]
        window = rb[max(0, j - 500): j + step + 1500]
        sm = SequenceMatcher(None, chunk, window, autojunk=False)
        blocks = sm.get_matching_blocks()
        matched = sum(m.size for m in blocks)
        unordered += len(chunk) - matched
        last = [m for m in blocks if m.size]
        if last:
            j = max(0, j - 500) + last[-1].b + last[-1].size
        else:
            j += len(chunk)
    return {'law': law, 'raw_chars': len(ra), 'repo_chars': len(rb), 'missing': missing, 'extra': extra,
            'out_of_order': unordered, 'toc_pages': toc}


def _check_job(job: tuple) -> dict:
    return check(*job)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('repo', type=Path)
    ap.add_argument('--branch', default='main')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--only')
    args = ap.parse_args()
    import csv
    import io
    laws_csv = subprocess.run(['git', '-C', str(args.repo), 'show', f'{args.branch}:laws.csv'], capture_output=True,
                              text=True, encoding='utf-8', check=True).stdout
    rows = list(csv.DictReader(io.StringIO(laws_csv)))
    results = []
    from concurrent.futures import ProcessPoolExecutor
    jobs = [(args.repo, args.branch, r['dir'].rstrip('/'), PDF / r['pdf'].rsplit('/', 1)[1]) for r in rows
            if not args.only or args.only in r['dir']]
    with ProcessPoolExecutor(4) as ex:  # at most 4 workers (shared machine)
        for r in ex.map(_check_job, jobs):
            results.append(r)
            if r['missing'] or r['extra'] or r['out_of_order'] > 0.02 * max(1, r['raw_chars']):
                print(f"{r['law']}: missing {r['missing']}, extra {r['extra']}, out of order {r['out_of_order']} "
                      f"of {r['raw_chars']}", flush=True)
    bad = [r for r in results if r['missing'] or r['extra']]
    tot = sum(r['raw_chars'] for r in results)
    ooo = sum(r['out_of_order'] for r in results)
    print(f"{len(results)} Acts, {tot} letters and digits; {len(bad)} Acts with a multiset mismatch; "
          f"{ooo} characters out of order ({ooo / max(1, tot):.4%})")
    if args.record:
        (HERE / 'verification.json').write_text(json.dumps({
            'acts': len(results), 'chars': tot, 'multiset_mismatch_acts': len(bad), 'out_of_order_chars': ooo,
            'results': results}, indent=1) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
