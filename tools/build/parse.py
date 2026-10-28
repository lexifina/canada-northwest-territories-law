"""Read one consolidated Act of the Northwest Territories (a bilingual PDF from the Legislation Division) into files.

The PDFs are set in two columns: English on the left (marginal notes at x < 92, text to x 307), French on the
right (from x 319). Only the English column is read. Page 1 (and any page before the "This consolidation is not
an official statement of the law" notice) is the title page: the Act's citation and its AMENDED BY list.
Table-of-contents pages are skipped. The body is split into one file per section; headings (PART, Division,
cross-headings) go on top of the next file as `## ` lines, and each marginal note goes on its own `> ` line just
before the paragraph it stands beside. Text after a SCHEDULE heading goes to schedule files.
"""
import re

import pymupdf

COL = 312.0          # English column ends here
MARGIN_X1 = 92.0     # marginal notes end before this
FOOT_Y = 695.0       # page numbers sit below this (some at y 699.6)

NOTICE = 'This consolidation is not an official statement'
SOFT_HYPHEN = '­'
START_RE = re.compile(r'^(?:Whereas\b|WHEREAS\b|Now,? therefore\b|The Commissioner\b|\d+(?:\.\d+)*\.\s|\(\d+(?:\.\d+)?\)\s|\([a-z]{1,5}(?:\.\d+)?\)\s|\([A-Z](?:\.\d+)?\)\s|"|'
                      r'PART\b|DIVISION\b|SCHEDULE\b|Division\b|Part\b|Schedule\b)')
MARKER_RE = re.compile(r'^\((?:\d+(?:\.\d+)?|[a-z]{1,5}(?:\.\d+)?|[A-Z](?:\.\d+)?)\)\s')
SECTION_RE = re.compile(r'^(\d+(?:\.\d+)*)\.(?=\s|\(|$)')
SCHEDULE_RE = re.compile(r'^(SCHEDULE|FORM|APPENDIX)\b\s*([A-Z0-9.\-]*)')
COLOPHON_RE = re.compile(r'^(?:©|\(c\)|©)?\s*\d{4}\s+Territorial Printer|^Yellowknife,\s*N\.?\s*W\.?\s*T\.?$|^Imprimeur')
PRINTER_RE = re.compile(r'(\d{4})\s*Territorial Printer')


def line_text(line: dict) -> tuple[str, float]:
    """Rebuild a line from its characters. A space glyph at least 0.2 em wide is a word space; narrower ones
    (1.5pt at 10pt) are the filler the typesetter puts after every letter of a letter-spaced justified line, and
    are dropped. Under tight justification the next letter overlaps a real space glyph, so gaps between visible
    characters are not used, except across a gap wider than 0.3 em (separate spans with no space glyph)."""
    out = []
    size = 10.0
    prev_x1 = None
    space = False
    for s in line['spans']:
        sz = s['size'] or size
        size = sz
        for c in s['chars']:
            ch = c['c']
            x0, x1 = c['bbox'][0], c['bbox'][2]
            if ch == SOFT_HYPHEN:
                continue
            if ch.isspace():
                if x1 - x0 >= 0.2 * sz:
                    space = True
                continue
            if out and (space or (prev_x1 is not None and x0 - prev_x1 > 0.3 * sz)):
                out.append(' ')
            space = False
            out.append(ch)
            prev_x1 = x1
    t = ''.join(out)
    t = t.replace('ﬁ', 'fi').replace('ﬂ', 'fl')
    return t.strip(), size


def page_lines(page, col: float = COL) -> list[dict]:
    """English-column lines of a page, in reading order."""
    out = []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            x0, y0, x1, y1 = l['bbox']
            if x0 >= col:
                continue
            t, size = line_text(l)
            if not t:
                continue
            if y0 >= FOOT_Y and re.fullmatch(r'\d+', t):
                continue
            if set(t) <= set('_-. '):
                continue
            out.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'text': t, 'size': size,
                        # marginal notes start near x 41; table-of-contents entries start at x 72
                        'margin': x0 < 60 and x1 <= MARGIN_X1 + 1})
    out.sort(key=lambda l: (round(l['y0']), l['x0']))
    return out


def read_page(page) -> list[dict]:
    """A page's English lines. A schedule can be set across the whole page width, the English version on one
    page and the French (ANNEXE) on the next (petroleum-products-carbon-tax): a page whose SCHEDULE heading is
    centred on the page (crossing the gutter) is read across its full width, and one whose ANNEXE heading is
    centred so is French and skipped."""
    full = page_lines(page, col=page.rect.width)
    centred = [l for l in full if l['x0'] < COL < l['x1'] and not l['margin']]
    if any(re.match(r'ANNEXE\b', l['text']) for l in centred[:1]):
        return []
    if any(re.match(r'SCHEDULE\b', l['text']) for l in centred[:1]):
        return full
    return page_lines(page)


def is_toc(lines: list[dict]) -> bool:
    return sum(1 for l in lines if 69 <= l['x0'] <= 76) >= 3 and not any(l['margin'] for l in lines)


def join(lines: list[str]) -> str:
    out = ''
    for t in lines:
        if not out:
            out = t
        elif out.endswith('-') and not out.endswith(' -'):
            out += t
        else:
            out += ' ' + t
    return re.sub(r'\s+', ' ', out).strip()


def key(num: str) -> tuple:
    return tuple(int(x) for x in num.split('.'))


def pad(num: str) -> str:
    a, *rest = num.split('.')
    return '.'.join([f'{int(a):03d}', *rest])


def parse(path: str) -> dict:
    doc = pymupdf.open(path)
    meta = doc.metadata or {}
    pages = [read_page(p) for p in doc]
    notice = next((i for i, ls in enumerate(pages) if any(NOTICE in l['text'] for l in ls)), None)
    # the title pages run to the notice; a long AMENDED BY list ends above the notice on its page (evidence, education)
    title_pages = list(range(0, notice + 1 if notice is not None else 1))
    title_lines = []
    for i in title_pages:
        for l in pages[i]:
            if NOTICE in l['text']:
                break  # the notice shares the title page in newer PDFs; it is not part of the Act
            if l['x0'] >= 250:
                continue  # the French title can start left of the column on a title page (x 298, mine-health)
            title_lines.append(l['text'])
    printer = None
    for ls in pages[::-1]:
        for l in ls:
            m = PRINTER_RE.search(l['text'])
            if m:
                printer = m.group(1)
                break
        if printer:
            break

    # body lines
    body = []
    toc_pages = []
    for i, ls in enumerate(pages):
        if i in title_pages or i == notice:
            continue
        # the table of contents is one run of pages: from its heading to the first page with a marginal note
        # (or the enacting words); body pages without marginal notes later on are not contents
        if any(l['text'].startswith('TABLE OF CONTENTS') for l in ls) or (
                toc_pages and toc_pages[-1] == i - 1 and
                # (the contents can also follow the Act, as in expropriation)
                not any(l['margin'] or 'enacts as follows' in l['text'] for l in ls) and
                # a preamble page ("Whereas ...") ends the contents too (environmental-rights)
                not any(re.match(r'(?:And )?[Ww]hereas\b|WHEREAS\b', l['text']) for l in ls)):
            toc_pages.append(i)
            continue
        for l in ls:
            if COLOPHON_RE.search(l['text']):
                continue
            l['page'] = i
            body.append(l)

    # paragraphs, with marginal notes attached by position
    paras: list[dict] = []
    margins: list[dict] = []
    prev = None
    for l in body:
        if l['margin']:
            if margins and margins[-1]['page'] == l['page'] and 0 <= l['y0'] - margins[-1]['y1'] < 4:
                margins[-1]['lines'].append(l['text'])
                margins[-1]['y1'] = l['y1']
            else:
                margins.append({'page': l['page'], 'y0': l['y0'], 'y1': l['y1'], 'lines': [l['text']]})
            continue
        new = prev is None
        caps = bool(not re.search(r'[a-z]', l['text']) and re.search(r'[A-Z]{2}', l['text']) and l['x0'] >= 98)
        if prev is not None:
            prev_first = len(paras[-1]['lines']) == 1
            if l['page'] != prev['page']:
                new = bool(START_RE.match(l['text'])) or (caps and paras[-1]['upper'] is False)
            elif abs(l['y0'] - prev['y0']) < 2:
                new = False  # same baseline (a split line)
            elif l['y0'] - prev['y1'] > 5:
                new = True
            elif l['x0'] >= 110 and MARKER_RE.match(l['text']) and (
                    l['x0'] < prev['x0'] - 2 or l['x0'] > prev['x0'] + 2 or prev_first):
                # a paragraph marker at its own indent; a wrapped line that begins "(2)" sits at the hanging
                # indent of the line before it and stays a continuation
                new = True
        if new:
            paras.append({'page': l['page'], 'y0': l['y0'], 'x0': l['x0'], 'lines': [l['text']], 'margins': [],
                          'upper': True})
        else:
            paras[-1]['lines'].append(l['text'])
        if re.search(r'[a-z]', l['text']):
            paras[-1]['upper'] = False
        prev = l
    for m in margins:
        # the paragraph on the same page whose first line is nearest at or below the note's top
        cands = [p for p in paras if p['page'] == m['page'] and p['y0'] >= m['y0'] - 3]
        target = min(cands, key=lambda p: p['y0']) if cands else None
        if target is None:
            cands = [p for p in paras if p['page'] == m['page']]
            target = max(cands, key=lambda p: p['y0']) if cands else None
        if target is None:
            target = paras[-1] if paras else None
        if target is not None:
            target['margins'].append(join(m['lines']))

    # files
    files: dict[str, list[str]] = {}
    order: list[str] = []
    pending: list[str] = []
    cur = 'intro.md'
    last_sec: tuple = ()
    in_schedule = False
    seen_sched: dict[str, int] = {}
    in_french = False

    def put(name, lines):
        if name not in files:
            files[name] = []
            order.append(name)
        files[name].extend(lines)

    for p in paras:
        text = join(p['lines'])
        heading = p['upper'] and not MARKER_RE.match(text) and not SECTION_RE.match(text) and p['x0'] >= 95 \
            and len(text) < 200
        sm = SCHEDULE_RE.match(text) if (heading or p['x0'] >= 95) else None
        # a schedule set across the full page width prints its French version (ANNEXE) below the English one,
        # inside the left column's area (petroleum-products-carbon-tax): skip it, up to the next English schedule
        if heading and re.match(r'ANNEXE\b', text):
            in_french = True
            continue
        if in_french and not (sm and (heading or len(text) < 80)):
            continue
        in_french = False
        if sm and (heading or len(text) < 80):
            label = (sm.group(2) or '').strip('.').lower() or str(len(seen_sched) + 1)
            name = f"schedule-{re.sub(r'[^a-z0-9.]+', '-', label)}"
            if name in seen_sched:
                seen_sched[name] += 1
                name = f'{name}~{seen_sched[name]}'
            else:
                seen_sched[name] = 1
            cur = name + '.md'
            in_schedule = True
            put(cur, [*[f'> {m}' for m in p['margins']], f'## {text}'])
            pending = []
            continue
        if heading:
            # a marginal note beside a heading (e.g. "COLUMN ONE" beside "COLUMN TWO" in a schedule) is kept
            notes = [f'> {mm}' for mm in p['margins']]
            if not last_sec and not in_schedule and cur == 'intro.md' and not files and not pending:
                put('intro.md', [*notes, f'## {text}'])  # the Act's title over its first page
            else:
                pending.extend([*notes, f'## {text}'])
            continue
        m = SECTION_RE.match(text)
        if m and not in_schedule and p['x0'] < 110 and key(m.group(1)) > last_sec:
            last_sec = key(m.group(1))
            cur = f's-{pad(m.group(1))}.md'
        put(cur, [*pending, *[f'> {mm}' for mm in p['margins']], text])
        pending = []
    if pending:
        put(cur, pending)

    out = {name: '\n'.join(lines) + '\n' for name, lines in files.items()}
    return {
        'title_lines': title_lines,
        'files': out,
        'order': order,
        'pages': doc.page_count,
        'toc_pages': toc_pages,
        'created': (meta.get('creationDate') or '')[2:10],
        'modified': (meta.get('modDate') or '')[2:10],
        'pdf_title': meta.get('title') or '',
        'printer': printer,
        'sections': sum(1 for n in out if n.startswith('s-')),
    }


if __name__ == '__main__':
    import sys
    r = parse(sys.argv[1])
    for k in ('pages', 'toc_pages', 'created', 'printer', 'sections', 'pdf_title'):
        print(k, r[k])
    print('\n'.join(r['title_lines'][:12]))
    for n in r['order'][: int(sys.argv[2]) if len(sys.argv) > 2 else 6]:
        print('=====', n)
        print(r['files'][n])
