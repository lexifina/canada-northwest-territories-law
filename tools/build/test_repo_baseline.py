"""Functional test of the built Northwest Territories law repository: what a reader does with it works.
Standard library only.

    python -X utf8 test_repo.py <repo> [--record]
    python -X utf8 tools/build/test_repo.py .          # from inside the repository

The source keeps only the current consolidation of each Act, so the history is current-only: one commit per Act.
It asserts that:
1. `git blame` on a section names the Legislation Division (the publisher) as the only author of its lines;
2. tools/in_force_on.py gives a section and a whole Act as main has them on the build day, and says there is no
   version before the Act's In-force day;
3. every law commit touches exactly one Act directory, and every Act in laws.csv has exactly one commit;
4. every commit has an `In-force:` trailer, commits are in In-force order, none after the build day, no merges and
   no CRs in messages, and each commit is dated noon in Yellowknife on its In-force day;
5. only the documentation commit (the last one) is by Lexifina, and Lexifina commits every commit;
6. data/issues.csv lists exactly the Acts dated by a later AMENDED BY in-force day, and data/amendments.csv has the
   AMENDED BY entries of every Act whose title page has that list;
7. every git or python command in the README's table runs and prints something.
With --record the result is written to verification.json next to this script.
"""
import argparse
import csv
import io
import json
import re
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLISHER = 'Legislation Division, Department of Justice (Government of the Northwest Territories)'
SAMPLE = ('acts/land-titles', '46')


def yellowknife_offset(d: date) -> int:
    """UTC offset at noon in Yellowknife: written out again here (not imported from build.py) so a mistake in one
    is caught by the other."""
    def sunday(y, m, n):  # n-th Sunday, n = -1 for the last
        if n > 0:
            first = date(y, m, 1)
            return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
        last = (date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)) - timedelta(days=1)
        return last - timedelta(days=(last.weekday() + 1) % 7)
    y = d.year
    if y < 1980:
        return -7
    if y <= 1986:
        a, b = sunday(y, 4, -1), sunday(y, 10, -1)
    elif y <= 2006:
        a, b = sunday(y, 4, 1), sunday(y, 10, -1)
    else:
        a, b = sunday(y, 3, 2), sunday(y, 11, 1)
    return -6 if a <= d < b else -7


class Test:
    def __init__(self, repo: Path):
        self.repo = repo
        self.results: list[tuple[str, bool, str]] = []
        rows = self.git('log', '--format=%H%x1f%an%x1f%cn%x1f%ad%x1f%(trailers:key=In-force,valueonly,separator=)'
                        '%x1f%P%x1e', '--date=raw', 'main').split('\x1e')
        self.log = [r.strip('\n').split('\x1f') for r in rows if r.strip()]
        self.laws = list(csv.DictReader(io.StringIO(self.git('show', 'main:laws.csv'))))
        self.today = json.loads(self.git('show', 'main:data/stats.json'))['today']

    def git(self, *args: str) -> str:
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'git {" ".join(args)}: {out.stderr.strip()[:300]}')
        return out.stdout

    def tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, 'tools/in_force_on.py', *args], cwd=self.repo, capture_output=True,
                              text=True, encoding='utf-8')

    def check(self, name: str, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
            print(f'PASS {name}: {detail}')
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            print(f'FAIL {name}: {exc}')

    # 1
    def blame(self):
        law, n = SAMPLE
        path = f'{law}/s-{int(n):03d}.md'
        out = self.git('blame', '--line-porcelain', 'main', '--', path)
        authors = {l[7:] for l in out.splitlines() if l.startswith('author ')}
        assert authors == {PUBLISHER}, f'{path}: authors {authors}'
        return f'{path}: every line by the Legislation Division'

    # 2
    def on_date(self):
        law, n = SAMPLE
        path = f'{law}/s-{int(n):03d}.md'
        r = self.tool(self.today, law, n)
        assert r.returncode == 0 and r.stdout, f'tool failed: {r.stderr.strip()}'
        assert r.stdout == self.git('show', f'main:{path}'), f'{path} on {self.today} is not the file at main'
        assert r.stdout.startswith(('## ', '> ', f'{n}.')), f'{path} starts {r.stdout[:40]!r}'
        whole = self.tool(self.today, law)
        assert whole.returncode == 0 and f'\n{n}. ' in whole.stdout, 'the whole Act lacks section 46'
        row = next(l for l in self.laws if l['dir'].rstrip('/') == law)
        before = (date.fromisoformat(row['in_force']) - timedelta(days=1)).isoformat()
        early = self.tool(before, law, n)
        assert early.returncode != 0 and 'no version' in early.stderr, f'{before}: {early.stderr.strip()[:200]}'
        sch = self.tool(self.today, law, 'schedule-a')
        assert sch.returncode == 0 and 'COLUMN ONE' in sch.stdout, 'schedule-a lacks its COLUMN ONE heading'
        return f'{path} on {self.today} == main; whole Act has s. {n}; {before}: no version; schedule-a read'

    # 3
    def one_commit_per_act(self):
        out = self.git('log', '--format=%x1e%H', '--name-only', 'main')
        dirs_by_commit = {}
        for block in out.split('\x1e'):
            lines = [l for l in block.splitlines() if l]
            if lines:
                dirs_by_commit[lines[0]] = {'/'.join(p.split('/')[:2]) for p in lines[1:] if p.startswith('acts/')}
        law_commits = [c for c in self.log[1:]]
        seen = {}
        for c in law_commits:
            ds = dirs_by_commit[c[0]]
            assert len(ds) == 1, f'{c[0][:10]} touches {sorted(ds)[:4]}'
            d = next(iter(ds))
            assert d not in seen, f'{d} in two commits'
            seen[d] = c[0]
        assert not dirs_by_commit[self.log[0][0]], 'the documentation commit touches acts/'
        want = {l['dir'].rstrip('/') for l in self.laws}
        assert set(seen) == want, f'laws.csv vs commits: {sorted(want ^ set(seen))[:5]}'
        tree = {'/'.join(p.split('/')[:2]) for p in self.git('ls-tree', '-r', '--name-only', 'main', 'acts').split()}
        assert tree == want, f'tree vs laws.csv: {sorted(tree ^ want)[:5]}'
        return f'{len(seen)} Acts, one commit each'

    # 4
    def dates(self):
        days = []
        for h, an, cn, ad, inf, parents in self.log:
            inf = inf.strip()
            assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', inf), f'{h[:10]}: In-force {inf!r}'
            assert len(parents.split()) <= 1, f'{h[:10]} is a merge'
            assert inf <= self.today, f'{h[:10]}: In-force {inf} after {self.today}'
            d = date.fromisoformat(inf)
            off = yellowknife_offset(d)
            ts = int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone(timedelta(hours=off))).timestamp())
            want = f'{ts} {"-" if off < 0 else "+"}{abs(off):02d}00'
            assert ad == want, f'{h[:10]}: date {ad}, want noon Yellowknife {want} for {inf}'
            days.append(inf)
        assert days == sorted(days, reverse=True), 'commits not in In-force order'
        assert self.git('rev-list', '--merges', '--count', 'main').strip() == '0', 'merges'
        cr = self.git('log', '--format=%B', 'main').count('\r')
        assert cr == 0, f'{cr} CRs in messages'
        return f'{len(days)} commits, {days[-1]} to {days[0]}, all at noon Yellowknife; 0 merges; 0 CRs'

    # 5
    def lexifina(self):
        by_lex = [c for c in self.log if c[1] == 'Lexifina']
        assert len(by_lex) == 1 and by_lex[0] is self.log[0], f'{len(by_lex)} commits by Lexifina'
        assert all(c[2] == 'Lexifina' for c in self.log), 'a committer other than Lexifina'
        assert {c[1] for c in self.log[1:]} == {PUBLISHER}, 'law commits by someone else'
        return f'only {self.log[0][0][:10]} (documentation) is by Lexifina'

    # 6
    def ledgers(self):
        issues = list(csv.DictReader(io.StringIO(self.git('show', 'main:data/issues.csv'))))
        later = {l['dir'].rstrip('/') for l in self.laws if l['in_force'] != l['consolidated']}
        listed = {r['law'] for r in issues if 'AMENDED BY' in r['issue']}
        assert later == listed, f'issues.csv vs laws.csv: {sorted(later ^ listed)[:5]}'
        msgs = self.git('log', '--format=%(trailers:key=Date-source,valueonly,separator=)', 'main').split()
        assert msgs.count('latest-listed-in-force') == len(later), 'Date-source trailers vs issues.csv'
        amend = list(csv.DictReader(io.StringIO(self.git('show', 'main:data/amendments.csv'))))
        with_list = {r['law'] for r in amend if r['source'] == 'amended-by'}
        missing = []
        for l in self.laws:
            d = l['dir'].rstrip('/')
            title = self.git('show', f'main:{d}/title.md')
            k = title.upper().find('AMENDED BY')
            # some title pages print an empty AMENDED BY heading (an Act never amended)
            if k >= 0 and re.search(r'\d{4}', title[k:]) and d not in with_list:
                missing.append(d)
        assert not missing, f'{len(missing)} Acts with an AMENDED BY list but no rows, e.g. {missing[:4]}'
        return f'{len(later)} later-dated Acts == issues.csv; AMENDED BY rows for {len(with_list)} Acts'

    # 7
    def readme(self):
        text = self.git('show', 'main:README.md')
        table = text[text.index('## The questions'):text.index('## The dates')]
        cmds = [c.replace('\\|', '|') for c in re.findall(r'`([^`]+)`', table) if c.startswith(('git ', 'python '))]
        assert len(cmds) >= 4, f'only {len(cmds)} commands found'
        for c in cmds:
            args = shlex.split(c)
            if args[0] == 'python':
                args[0] = sys.executable
            out = subprocess.run(args, cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
            assert out.returncode == 0, f'{c}: exit {out.returncode} {out.stderr.strip()[:200]}'
            assert out.stdout.strip(), f'{c}: printed nothing'
        return f'{len(cmds)} README commands ran and printed output'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo', nargs='?', default=None)
    ap.add_argument('--record', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    repo = Path(args.repo) if args.repo else (HERE.parents[1] if (HERE.parents[1] / '.git').exists() else Path('.'))
    t = Test(repo)
    t.check('blame names the publisher', t.blame)
    t.check('the on-date tool', t.on_date)
    t.check('one commit per Act', t.one_commit_per_act)
    t.check('In-force dates, noon Yellowknife, no merges or CRs', t.dates)
    t.check('only the documentation commit is by Lexifina', t.lexifina)
    t.check('issues and amendments ledgers', t.ledgers)
    t.check('README commands run', t.readme)
    ok = all(r[1] for r in t.results)
    print(f'{sum(r[1] for r in t.results)}/{len(t.results)} passed')
    if args.record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['smoke'] = {'passed': sum(r[1] for r in t.results), 'total': len(t.results),
                        'results': [{'test': n, 'ok': o, 'detail': d} for n, o, d in t.results]}
        p.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
