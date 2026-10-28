"""Build the Northwest Territories law repository from the gnwt.ca cache (fetch.py).

    python build.py C:/Users/alany/Documents/canada-northwest-territories-law --today YYYY-MM-DD [--limit N] [--push]

The Legislation Division (Department of Justice, GNWT) publishes only the current consolidation of each Act, as a
bilingual PDF; it keeps no past versions. So the history here starts at that text:

1. Every consolidated Act is one law, in `acts/<file stem>/` (the stem of the official PDF name, e.g. land-titles).
   Its title page (citation, commencement and the AMENDED BY list) is `title.md`; the preamble and anything before
   section 1 is `intro.md`; each section is `s-<number, whole part padded to 3 digits>.md`; each schedule or form
   is `schedule-<label>.md`. English text only (the left column of the bilingual PDF).
2. Each law is one commit, `Kind: consolidated`, authored by the Legislation Division, dated noon Yellowknife time
   on the day the consolidation PDF was made (its CreationDate). Its amendment history stays in the files as the
   consolidation prints it (the AMENDED BY list and the source note after each provision: "SNWT 2009,c.12,s.11(2).")
   and is listed in data/amendments.csv. No line is credited to an amending Act: the text before each amendment is
   not published.
3. One git fast-import from the cache, so the same cache always gives the same hashes. `--push` pushes `wip`
   after the build (the user wants the remote kept current).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings('ignore')
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import parse  # noqa: E402

CACHE = HERE / '.cache'
PDF = CACHE / 'pdf'
REPO_FILES = HERE / 'repo'
COMMITTER = 'Lexifina <canada-northwest-territories-law@lexifina.com>'
PUBLISHER = ('Legislation Division, Department of Justice (Government of the Northwest Territories)',
             'legislation-division@justice-gov-nt-ca.invalid')
SITE = 'https://gnwt.ca'
CORPUS = 'ca/northwest-territories/acts'
BUILD_FILES = ('fetch.py', 'parse.py', 'build.py', 'check_repo.py', 'test_repo.py')
ACT_RE = re.compile(r'\.a(?:_\d+)?\.pdf$')
# Statute prefixes are printed both bare and dotted: SNWT / S.N.W.T., RSNWT / R.S.N.W.T., SC / S.C., RSC / R.S.C.
NWT = r'(?:R\.?\s?S\.?|S\.?)\s?N\.?\s?W\.?\s?T\.?'
CITE_RE = re.compile(r'\b(' + NWT + r'\s?\d{4},\s?c\.\s?[A-Za-z0-9.\-]+(?:\s?\(Supp\.\))?|R-\d{3}-\d{4}|SI-\d{3}-\d{2,4}|'
                     r'S\.?\s?C\.?\s?\d{4},\s?c\.\s?\d+|R\.?\s?S\.?\s?C\.?\s?\d{4},\s?c\.\s?[A-Z0-9\-]+)')
NOTE_RE = re.compile(r'((?:(?:' + NWT + r'|R-|SI-)[^;]*?;\s*)*(?:' + NWT + r'|R-\d{3}|SI-\d{3})[^;]*?\.)\s*$')


def cite_key(c: str) -> str:
    """A citation as one token: no spaces, and no dots in the statute prefix (S.N.W.T. 1998,c.5 -> SNWT1998,c.5)."""
    c = re.sub(r'\s+', '', c)
    m = re.match(r'([A-Z.]+)(\d{4},.*)$', c)
    return m.group(1).replace('.', '') + m.group(2) if m else c
MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                      'september', 'october', 'november', 'december'], 1)}
INFORCE_RE = re.compile(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
                        r'\s+(\d{1,2}),\s*(\d{4})')


# ---------------------------------------------------------------------------------------------------- time

def nth_sunday(y: int, m: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_sunday(y: int, m: int) -> date:
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def yellowknife_offset(d: date) -> int:
    """UTC offset in hours at noon in Yellowknife (America/Yellowknife: Mountain Time). Hard-coded rules:
    MST (UTC-7) with no daylight time from 1970 until 1979; Canadian daylight time from 1980: last Sunday of April
    to last Sunday of October (1980-1986), first Sunday of April to last Sunday of October (1987-2006), second
    Sunday of March to first Sunday of November (2007 on). The switch is at 02:00, so noon on a switch day is
    already on the new time: start <= day < end."""
    y = d.year
    if y < 1980:
        return -7
    if y <= 1986:
        start, end = last_sunday(y, 4), last_sunday(y, 10)
    elif y <= 2006:
        start, end = nth_sunday(y, 4, 1), last_sunday(y, 10)
    else:
        start, end = nth_sunday(y, 3, 2), nth_sunday(y, 11, 1)
    return -6 if start <= d < end else -7


def git_date(day: str, seq: int) -> str:
    d = datetime.strptime(day, '%Y-%m-%d').date()
    if d.year < 1970:
        return f'{seq} -0700'
    off = yellowknife_offset(d)
    ts = int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone(timedelta(hours=off))).timestamp())
    return f'{ts} {"-" if off < 0 else "+"}{abs(off):02d}00'


# ---------------------------------------------------------------------------------------------------- laws

def iso(yyyymmdd: str) -> str:
    return f'{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}'


def catalogue() -> list[dict]:
    items = {it['href'].rsplit('/', 1)[1]: it for it in json.loads((CACHE / 'items.json').read_text('utf-8'))}
    links = json.loads((CACHE / 'pdf_links.json').read_text('utf-8'))
    laws = []
    for slug, urls in sorted(links.items()):
        for u in urls:
            name = u.rsplit('/', 1)[1]
            if not ACT_RE.search(name):
                continue
            stem = ACT_RE.sub('', name)
            laws.append({'slug': slug, 'url': u, 'file': name, 'stem': stem, 'dir': f'acts/{stem}',
                         'title': re.sub(r'&#0?39;', "'", items[slug]['title']).replace('&amp;', '&'),
                         'published': items[slug]['published']})
    seen = {}
    for l in laws:
        assert l['dir'] not in seen, (l, seen.get(l['dir']))
        seen[l['dir']] = l
    return sorted(laws, key=lambda l: l['dir'])


def ledger(law: dict, r: dict) -> list[list[str]]:
    """Amendment rows: the AMENDED BY list of the title page (each citation with the in-force lines under it),
    and every citation in each provision's source note."""
    rows = []
    lines = r['title_lines']
    try:
        k = next(i for i, t in enumerate(lines) if t.strip().upper().startswith('AMENDED BY'))
    except StopIteration:
        k = None
    if k is not None:
        cur = None
        for t in lines[k + 1:]:
            m = CITE_RE.match(t.strip())
            if m:
                if cur:
                    rows.append(cur)
                cur = [law['dir'], 'title.md', 'amended-by', cite_key(m.group(1)), '', t.strip()]
            elif cur:
                cur[5] = cur[5] + ' / ' + t.strip()
        if cur:
            rows.append(cur)
        for row in rows:
            ds = [f'{int(y):04d}-{MONTHS[mo.lower()]:02d}-{int(d):02d}' for mo, d, y in INFORCE_RE.findall(row[5])]
            row[4] = '; '.join(ds)
    for name in r['order']:
        for line in r['files'][name].splitlines():
            if line.startswith(('## ', '> ')):
                continue
            # a source note is the run of citations at the end of a paragraph
            m = NOTE_RE.search(line)
            if not m:
                continue
            for c in CITE_RE.findall(m.group(1)):
                rows.append([law['dir'], name, 'note', cite_key(c), '', m.group(1)[:300]])
    return rows


def build_law(law: dict) -> dict:
    r = parse.parse(str(PDF / law['file']))
    files = {f"{law['dir']}/title.md": '\n'.join(r['title_lines']) + '\n'}
    for name, text in r['files'].items():
        files[f"{law['dir']}/{name}"] = text
    files[f"{law['dir']}/index.txt"] = '\n'.join(['title.md', *r['order']]) + '\n'
    inforce = sorted({f'{int(y):04d}-{MONTHS[mo.lower()]:02d}-{int(d):02d}'
                      for mo, d, y in INFORCE_RE.findall('\n'.join(r['title_lines']))})
    citation = r['title_lines'][next((i for i, t in enumerate(r['title_lines']) if CITE_RE.match(t)), 0)] \
        if r['title_lines'] else ''
    return {'files': files, 'created': iso(r['created']) if len(r['created']) == 8 else '', 'printer': r['printer'],
            'sections': r['sections'], 'pages': r['pages'], 'toc_pages': r['toc_pages'], 'amend': ledger(law, r),
            'latest_in_force': inforce[-1] if inforce else '', 'citation': citation if CITE_RE.match(citation) else ''}


# ---------------------------------------------------------------------------------------------------- write

def message(law: dict, day: str, b: dict) -> tuple[str, str]:
    name, email = PUBLISHER
    subject = f"{law['title']}: as consolidated by the Legislation Division, {day}"
    n_listed = sum(1 for r in b['amend'] if r[2] == 'amended-by')
    n_notes = sum(1 for r in b['amend'] if r[2] == 'note')
    body = [f"The {law['title']}{' (' + b['citation'] + ')' if b['citation'] else ''} as the Legislation Division of",
            "the Department of Justice consolidates it, in the PDF made on " + b['created'] + " (its creation date).",
            *([f"That PDF already contains an amendment its AMENDED BY list puts in force on {day}, so the text is",
               f"dated {day}, the day it is all in force."] if day != b['created'] else []),
            'No earlier consolidation is published, so this commit holds the whole text and nobody is credited',
            'with single lines. The amendment history the consolidation prints stays in the files: the AMENDED BY',
            f'list in title.md ({n_listed} entries) and the source notes after provisions ({n_notes} citations).']
    trailers = ['Kind: consolidated', f"Consolidated: {b['created']}", f'In-force: {day}', f"Law: {law['title']}"]
    if b['citation']:
        trailers.append(f"Citation: {b['citation']}")
    trailers.append('Date-source: ' + ('latest-listed-in-force' if day != b['created'] else 'pdf-created'))
    if b['printer']:
        trailers.append(f"Printed: {b['printer']}")
    if b['latest_in_force']:
        trailers.append(f"Latest-listed-in-force: {b['latest_in_force']}")
    trailers += [f"Source: {law['url']}", f"Resource: {SITE}/resources/{law['slug']}"]
    return f'{name} <{email}>', '\n'.join([subject, '', *body, '', *trailers]) + '\n'


def fast_import(repo: Path, commits: list[tuple], docs: dict[str, bytes], today: str, retrieved: str) -> None:
    buf = io.BytesIO()
    w = buf.write
    seq = 0
    for day, law, b in commits:
        seq += 1
        author, msg = message(law, day, b)
        when = git_date(day, seq)
        w(b'commit refs/heads/main\n')
        w(f'author {author} {when}\ncommitter {COMMITTER} {when}\n'.encode('utf-8'))
        m = msg.encode('utf-8')
        w(f'data {len(m)}\n'.encode() + m + b'\n')
        for path, text in sorted(b['files'].items()):
            t = text.encode('utf-8')
            w(f'M 100644 inline {path}\ndata {len(t)}\n'.encode() + t + b'\n')
        w(b'\n')
    when = git_date(retrieved, seq + 1)
    msg = (f'Documentation: README, REPRODUCE, SOURCES, tools, laws.csv and data\n\n'
           f'Built from the gnwt.ca cache retrieved on {retrieved}.\n\nKind: documentation\nIn-force: {retrieved}\n'
           ).encode('utf-8')
    w(b'commit refs/heads/main\n')
    w(f'author {COMMITTER} {when}\ncommitter {COMMITTER} {when}\n'.encode())
    w(f'data {len(msg)}\n'.encode() + msg + b'\n')
    for path, data in sorted(docs.items()):
        w(f'M 100644 inline {path}\ndata {len(data)}\n'.encode() + data + b'\n')
    w(b'\n')
    r = subprocess.run(['git', '-C', str(repo), 'fast-import', '--quiet', '--force'], input=buf.getvalue(),
                       capture_output=True)
    if r.returncode:
        sys.exit(r.stderr.decode('utf-8', 'replace')[-3000:])


def _csv(rows: list[list]) -> bytes:
    out = io.StringIO()
    wr = csv.writer(out, lineterminator='\n')
    for r in rows:
        wr.writerow(r)
    return out.getvalue().encode('utf-8')


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def docs(laws: list[dict], built: dict, commits: list[tuple], issues: list[list[str]], today: str,
         retrieved: str) -> dict[str, bytes]:
    days = sorted(c[0] for c in commits)
    amend = sorted(r for l in laws for r in built[l['dir']]['amend'])
    listed = {r[3] for r in amend if r[2] == 'amended-by'}
    units = sum(sum(1 for p in built[l['dir']]['files'] if p.rsplit('/', 1)[1].startswith(('s-', 'schedule-')))
                for l in laws)
    sections = sum(built[l['dir']]['sections'] for l in laws)
    record_to = max(d for d in days if d <= today)
    stats = {'today': today, 'retrieved': retrieved, 'acts': len(commits), 'sections': sections, 'units': units,
             'commits': len(commits) + 1, 'first': days[0], 'last': record_to,
             'amended_by_rows': sum(1 for r in amend if r[2] == 'amended-by'), 'amending_cited': len(listed),
             'note_citations': sum(1 for r in amend if r[2] == 'note'), 'issues': len(issues),
             'by_year': {y: sum(1 for d in days if d[:4] == y) for y in sorted({d[:4] for d in days})}}
    corpus = {
        'corpus': CORPUS,
        'parent': 'ca/federal/acts',
        'name': 'Consolidated Acts of the Northwest Territories',
        'label': 'Northwest Territories',
        'language': 'en',
        'acts': len(commits),
        'history': 'current-only',
        'history_from': days[0],
        'record_to': record_to,
        'record_note': ('The Legislation Division publishes only the current consolidation of each Act (a bilingual '
                        'PDF, English column read), so each Act has one version here, dated by the day its PDF was '
                        'made. Earlier text is not available; the AMENDED BY lists and source notes are kept as text '
                        f'and in data/amendments.csv. Checked against the source PDFs on {today}.'),
        'checked_on': today,
        'built': today,
        'baseline_trailer': 'Consolidated',
        'in_force_trailer': 'In-force',
        'dates': {'commit': ('in-force day of the consolidated text: the CreationDate of the Act\'s consolidation '
                             'PDF, or the latest in-force day its AMENDED BY list gives when that is later'),
                  'baseline_trailer': 'Consolidated',
                  'other_trailers': ['Latest-listed-in-force', 'Printed'],
                  'time': '12:00 America/Yellowknife (UTC-7; UTC-6 in daylight time, hard-coded Canadian rules)'},
        'author': {'consolidated': PUBLISHER[0], 'amendment': 'none yet', 'unexplained': PUBLISHER[0]},
        'scope': {
            'trailer': 'Applies',
            'when_absent': ('this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                            'statement that the change reaches every case'),
            'default_reach': {
                'rule': 'presumption against retrospectivity; effect of repeal saved by the Interpretation Act',
                'meaning': ('an enactment is presumed not to apply to facts completed before it came into force, or '
                            'to interfere with vested rights, unless the Legislature says so expressly or by '
                            'necessary implication; a repeal does not affect rights acquired, or proceedings begun, '
                            'under the repealed enactment'),
                'authority': ('Interpretation Act, RSNWT 1988, c. I-8 (effect of repeal); R. v. Dineley, 2012 SCC '
                              '58, [2012] 3 S.C.R. 272'),
                'decided_here': False,
            },
        },
        'tool': 'tools/in_force_on.py',
        'citation': {
            'file': 'acts/([a-z0-9-]+)/s-0*([0-9]+(?:\\.[0-9]+)*)\\.md',
            'cites': '([Ss]ections?|[Ss]ubsections?|[Pp]aragraphs?|ss?\\.) ?{n}([^0-9.]|\\.[^0-9]|$)',
        },
        'path_pattern': ('acts/<official PDF stem>/s-<section, whole part padded to 3 digits>.md; '
                         'acts/<stem>/schedule-<label>.md; acts/<stem>/intro.md; acts/<stem>/title.md'),
    }
    laws_csv = _csv([['title', 'citation', 'dir', 'in_force', 'consolidated', 'printed', 'latest_listed_in_force', 'sections',
                      'pages', 'pdf', 'resource']] +
                    [[l['title'], built[l['dir']]['citation'], l['dir'] + '/', built[l['dir']]['in_force'],
                      built[l['dir']]['created'],
                      built[l['dir']]['printer'] or '', built[l['dir']]['latest_in_force'],
                      built[l['dir']]['sections'], built[l['dir']]['pages'], l['url'],
                      f"{SITE}/resources/{l['slug']}"] for l in laws])
    sums = ''.join(f"pdf/{l['file']}\t{_sha(PDF / l['file'])}\n" for l in laws)
    subs = {k.upper(): str(v) for k, v in stats.items() if isinstance(v, (str, int))}
    for n in BUILD_FILES:
        subs['SHA_' + n.upper().replace('.', '_')] = _sha(HERE / n) if (HERE / n).exists() else '(not present)'

    def fill(name: str) -> bytes:
        t = (REPO_FILES / name).read_text('utf-8')
        for k, v in subs.items():
            t = t.replace('{{' + k + '}}', v)
        return t.encode('utf-8')
    out = {
        'corpus.json': (json.dumps(corpus, indent=2, ensure_ascii=False) + '\n').encode('utf-8'),
        'README.md': fill('README.md'),
        'REPRODUCE.md': fill('REPRODUCE.md'),
        'SOURCES.md': fill('SOURCES.md'),
        'LICENSE': (REPO_FILES / 'LICENSE').read_bytes(),
        'laws.csv': laws_csv,
        'data/amendments.csv': _csv([['law', 'file', 'source', 'citation', 'in_force_dates', 'text']] + amend),
        'data/issues.csv': _csv([['law', 'issue']] + issues),
        'data/stats.json': (json.dumps(stats, indent=1, sort_keys=True) + '\n').encode('utf-8'),
        'data/source-sha256.tsv': sums.encode('utf-8'),
        'tools/in_force_on.py': (REPO_FILES / 'tools' / 'in_force_on.py').read_bytes(),
    }
    for n in BUILD_FILES:
        if (HERE / n).exists():
            out[f'tools/build/{n}'] = (HERE / n).read_bytes()
    for n in ('README.md', 'REPRODUCE.md', 'SOURCES.md', 'LICENSE'):
        out[f'tools/build/repo/{n}'] = (REPO_FILES / n).read_bytes()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('repo', type=Path)
    ap.add_argument('--today', required=True)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--push', action='store_true', help='push the result to origin wip (force: wip is rebuilt)')
    ap.add_argument('--branch', default='main')
    args = ap.parse_args()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.today):
        sys.exit('--today must be YYYY-MM-DD')
    retrieved = (CACHE / 'fetched_on.txt').read_text().strip()
    if retrieved > args.today:
        sys.exit(f'retrieval day {retrieved} is after --today')

    laws = catalogue()
    if args.limit:
        laws = laws[:args.limit]
    built = {}
    issues = []
    for i, law in enumerate(laws):  # serial: a process pool under this shell broke; 238 Acts take about 90 s
        b = build_law(law)
        built[law['dir']] = b
        if not b['created']:
            issues.append([law['dir'], 'PDF has no CreationDate; dated by the retrieval day'])
            b['created'] = retrieved
        # Some PDFs were made a few days before an amendment they already contain came into force. The text is
        # then the law from that later day, so the In-force day (and the commit date) is the later of the two.
        b['in_force'] = b['created']
        if b['latest_in_force'] and b['latest_in_force'] > b['created']:
            if b['latest_in_force'] > args.today:
                sys.exit(f"{law['dir']}: in-force day {b['latest_in_force']} is after --today (needs a future branch)")
            b['in_force'] = b['latest_in_force']
            issues.append([law['dir'], f"the PDF (made {b['created']}) already contains an amendment its AMENDED BY "
                                       f"list puts in force on {b['latest_in_force']}; dated {b['latest_in_force']}"])
        if b['sections'] == 0:
            issues.append([law['dir'], 'no numbered sections found'])
        if (i + 1) % 50 == 0:
            print(i + 1, 'parsed', flush=True)
    commits = sorted(((built[l['dir']]['in_force'], l, built[l['dir']]) for l in laws),
                     key=lambda c: (c[0], c[1]['dir']))
    for day, law, _ in commits:
        if day > args.today:
            sys.exit(f"{law['dir']}: consolidation date {day} after --today")
    d = docs(laws, built, commits, issues, args.today, retrieved)

    repo = args.repo
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / '.git').exists():
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.autocrlf', 'false'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.longpaths', 'true'], check=True)
    # write the history into a scratch ref, then point the wanted branch at it
    fast_import(repo, commits, d, args.today, retrieved)
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'refs/heads/main'], capture_output=True,
                          text=True).stdout.strip()
    if args.branch != 'main':
        subprocess.run(['git', '-C', str(repo), 'update-ref', f'refs/heads/{args.branch}', head], check=True)
    count = subprocess.run(['git', '-C', str(repo), 'rev-list', '--count', head], capture_output=True,
                           text=True).stdout.strip()
    print(f'built {repo}: {count} commits, {len(issues)} issues, head {head}', flush=True)
    if args.push:
        r = subprocess.run(['git', '-C', str(repo), 'push', '-q', '-f', 'origin', f'{head}:refs/heads/wip'],
                           capture_output=True, text=True)
        print('push wip:', r.returncode, r.stderr.strip()[-300:], flush=True)


if __name__ == '__main__':
    main()
