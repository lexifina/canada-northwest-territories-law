"""Functional test of the built Northwest Territories law repository: what a reader does with it works.
Standard library only.

    python -X utf8 test_repo.py <repo> [--record]
    python -X utf8 tools/build/test_repo.py .          # from inside the repository

The corpus has one consolidated baseline per Act and may append explicitly registered historical
correction snapshots and documentation commits. The gate checks the entire history:
1. Sample blame still names the source publisher.
2. The reader gives the current baseline and every registered commencement boundary correctly.
3. Each Act has exactly one ordinary baseline; every correction matches its declared snapshot,
   legal interval, Act path, complete Files coverage and required provenance. Documentation touches no law.
4. Every commit has a real ISO In-force date, is not after the checked day, is noon in Yellowknife,
   has no merge or CR; ordinary baseline dates remain ordered and correction dates match their intervals.
5. Every documentation author is Lexifina; every law author is the publisher; all committers are Lexifina.
6. The original issues and amendment ledger checks still cover every Act.
7. Every git/python command in the README's table runs and prints output.
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
        self.stats = json.loads(self.git('show', 'main:data/stats.json'))
        self.today = self.stats['today']
        raw = self.git('log', '--format=%x1d%H%x1e%B%x1e', '--name-only', 'main')
        self.records = {}
        for block in raw.split('\x1d')[1:]:
            h, message, paths = block.split('\x1e', 2)
            h = h.strip()
            trailer_block = message.strip().rsplit('\n\n', 1)[-1]
            trailers = {}
            for line in trailer_block.splitlines():
                match = re.fullmatch(r'([A-Za-z][A-Za-z-]*): (.*)', line)
                if match:
                    trailers.setdefault(match.group(1), []).append(match.group(2))
            kind = (trailers.get('Kind') or [''])[0]
            self.records[h] = {'kind': kind, 'message': message, 'trailers': trailers,
                               'paths': [p for p in paths.splitlines() if p]}
        present = self.git('ls-tree', '--name-only', 'main', 'data/historical-commencement.json').strip()
        self.history = json.loads(self.git('show', 'main:data/historical-commencement.json')) if present else None
        self.snapshots = {}
        if self.history:
            for state in self.history['snapshots']:
                assert state['commit'] not in self.snapshots, 'duplicate historical snapshot commit'
                self.snapshots[state['commit']] = state
        assert set(self.records) == {c[0] for c in self.log}, 'incomplete commit classification'


    def git(self, *args: str) -> str:
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'git {" ".join(args)}: {out.stderr.strip()[:300]}')
        return out.stdout

    def tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, 'tools/in_force_on.py', *args], cwd=self.repo, capture_output=True,
                              text=True, encoding='utf-8')

    def files_at(self, commit: str, paths: list[str]) -> dict[str, str]:
        specs = ''.join(f'{commit}:{p}\n' for p in paths).encode('utf-8')
        done = subprocess.run(['git', 'cat-file', '--batch'], cwd=self.repo,
                              input=specs, capture_output=True, check=True)
        data, offset, result = done.stdout, 0, {}
        for path in paths:
            end = data.index(b'\n', offset)
            header = data[offset:end].split()
            assert len(header) == 3 and header[1] == b'blob', f'{commit}:{path} missing'
            size = int(header[2])
            result[path] = data[end + 1:end + 1 + size].decode('utf-8')
            offset = end + size + 2
        return result

    def ordinary_for(self, act: str) -> str:
        matches = [h for h, record in self.records.items() if record['kind'] == 'consolidated'
                   and any(p.startswith(act + '/') for p in record['paths'])]
        assert len(matches) == 1, f'{act}: expected one ordinary baseline, got {len(matches)}'
        return matches[0]

    def whole_at(self, commit: str, act: str) -> str:
        names = self.git('show', f'{commit}:{act}/index.txt').splitlines()
        paths = [f'{act}/{name}' for name in names]
        files = self.files_at(commit, paths)
        return ''.join(files[path] + '\n' for path in paths)

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
        history_detail = ''
        if self.history:
            act = self.history['act']
            states = self.history['snapshots']
            assert len(states) == 2, 'commencement record requires partial and full snapshots'
            first, full = states
            assert first['through'] == (date.fromisoformat(full['in_force']) - timedelta(days=1)).isoformat()
            assert full['through'] is None
            baseline = self.ordinary_for(act)
            s5 = self.git('show', f'{baseline}:{act}/s-005.md')
            full_text = self.whole_at(baseline, act)
            earliest_before = (date.fromisoformat(first['in_force']) - timedelta(days=1)).isoformat()
            no_state = self.tool(earliest_before, act)
            assert no_state.returncode != 0 and 'no version' in no_state.stderr
            for day in [first['in_force'], first['through']]:
                partial = self.tool(day, act)
                section = self.tool(day, act, '5')
                absent = self.tool(day, act, '1')
                assert partial.returncode == 0 and s5 in partial.stdout
                assert section.returncode == 0 and section.stdout == s5
                assert '\n1. ' not in partial.stdout and '\n20. ' not in partial.stdout
                assert absent.returncode != 0 and 'no version' in absent.stderr
            for day in [full['in_force'], self.today]:
                complete = self.tool(day, act)
                assert complete.returncode == 0 and complete.stdout == full_text, f'{act} on {day}'
            history_detail = '; registered commencement boundaries and missing section checked'
        return f'{path} on {self.today} == main; whole Act has s. {n}; {before}: no version; schedule-a read{history_detail}'

    # 3
    def one_commit_per_act(self):
        want = {law['dir'].rstrip('/') for law in self.laws}
        seen, corrections, documents = {}, set(), []
        for h, record in self.records.items():
            kind, paths = record['kind'], record['paths']
            acts = {'/'.join(p.split('/')[:2]) for p in paths if p.startswith('acts/')}
            assert kind in {'consolidated', 'correction', 'documentation'}, f'{h[:10]}: unknown Kind {kind!r}'
            if kind == 'documentation':
                assert not acts, f'{h[:10]}: documentation touches law text'
                documents.append(h)
                continue
            assert len(acts) == 1, f'{h[:10]} touches {sorted(acts)}'
            act = next(iter(acts))
            assert act in want and all(p.startswith(act + '/') for p in paths), f'{h[:10]}: mixed paths'
            if kind == 'consolidated':
                assert act not in seen, f'{act} in two ordinary commits'
                seen[act] = h
                continue
            assert h in self.snapshots, f'{h[:10]}: unregistered correction'
            state = self.snapshots[h]
            trailers = record['trailers']
            required = ['In-force', 'Corrects', 'Supersedes', 'Reason', 'Act-path', 'In-force-basis', 'Source', 'Files']
            for key in required:
                assert trailers.get(key), f'{h[:10]}: missing {key}'
            for key in required[:-2]:
                assert len(trailers[key]) == 1, f'{h[:10]}: repeated {key}'
            assert self.history and act == self.history['act'] == trailers['Act-path'][0]
            assert trailers['In-force'][0] == state['in_force']
            expected = f"{act} {state['in_force']}..{state['through'] or ''}"
            assert trailers['Corrects'][0] == expected, f'{h[:10]}: correction interval differs from registered state'
            assert trailers['Reason'][0].strip() and trailers['In-force-basis'][0] == self.history['in_force_basis']
            assert set(trailers['Source']) == set(self.history['source_urls'])
            supersedes = trailers['Supersedes'][0]
            assert supersedes == 'none' or re.fullmatch(r'[a-f0-9]{40}', supersedes), f'{h[:10]}: Supersedes'
            if supersedes != 'none':
                assert self.git('cat-file', '-t', supersedes).strip() == 'commit'
            live = set(self.git('ls-tree', '-r', '--name-only', h, '--', act).splitlines())
            deleted = set(self.git('diff-tree', '--no-commit-id', '--name-only', '--diff-filter=D', '-r', h).splitlines())
            declared = trailers['Files']
            assert len(declared) == len(set(declared)), f'{h[:10]}: repeated Files'
            assert set(declared) == live | deleted, f'{h[:10]}: Files must cover retained and removed snapshot paths'
            assert all(p.startswith(act + '/') for p in declared)
            index = self.git('show', f'{h}:{act}/index.txt').splitlines()
            assert {f'{act}/{name}' for name in index} | {f'{act}/index.txt'} == live, f'{h[:10]}: snapshot index incomplete'
            corrections.add(h)
        assert set(seen) == want, f'laws.csv vs ordinary commits: {sorted(want ^ set(seen))[:5]}'
        assert corrections == set(self.snapshots), 'registered history vs correction commits'
        assert documents and self.records[self.log[0][0]]['kind'] == 'documentation', 'tip must be documentation'
        tree = {'/'.join(p.split('/')[:2]) for p in self.git('ls-tree', '-r', '--name-only', 'main', 'acts').split()}
        assert tree == want, f'tree vs laws.csv: {sorted(tree ^ want)[:5]}'
        assert self.stats['commits'] == len(self.log), 'stats commit count differs from complete history'
        assert self.stats.get('historical_snapshots', 0) == len(corrections)
        assert self.stats.get('historical_acts', 0) == (1 if self.history else 0)
        return f'{len(seen)} Acts, one ordinary baseline each; {len(corrections)} fully validated corrections; {len(documents)} documentation commits'

    # 4
    def dates(self):
        ordinary_days, documentation_days, days = [], [], []
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.today), 'invalid checked day'
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
            kind = self.records[h]['kind']
            if kind == 'consolidated':
                ordinary_days.append(inf)
            elif kind == 'documentation':
                documentation_days.append(inf)
            elif kind == 'correction':
                assert h in self.snapshots, f'{h[:10]}: unregistered correction date'
                state = self.snapshots[h]
                assert inf == state['in_force'], f'{h[:10]}: correction date mismatch'
                if state['through'] is not None:
                    end = date.fromisoformat(state['through'])
                    assert d <= end and state['through'] <= self.today, f'{h[:10]}: invalid correction end'
            else:
                raise AssertionError(f'{h[:10]}: unknown commit kind')
            days.append(inf)
        assert ordinary_days == sorted(ordinary_days, reverse=True), 'ordinary baselines not in In-force order'
        assert documentation_days == sorted(documentation_days, reverse=True), 'documentation dates moved backwards'
        assert self.git('rev-list', '--merges', '--count', 'main').strip() == '0', 'merges'
        raw = subprocess.run(['git', 'log', '--format=%B', 'main'], cwd=self.repo, capture_output=True, check=True).stdout
        assert raw.count(b'\r') == 0, 'CRs in messages'
        if self.history:
            assert self.today == max(self.stats['baseline_checked_on'], self.history['checked_on']), 'checked-day metadata mismatch'
        return f'{len(days)} commits individually dated/checked at noon; ordinary baselines ordered; historical intervals validated; 0 merges; 0 CRs'

    # 5
    def lexifina(self):
        documents = []
        for h, author, committer, ad, inf, parents in self.log:
            assert committer == 'Lexifina', f'{h[:10]}: committer other than Lexifina'
            kind = self.records[h]['kind']
            if kind == 'documentation':
                assert author == 'Lexifina', f'{h[:10]}: documentation author'
                assert not any(p.startswith('acts/') for p in self.records[h]['paths']), f'{h[:10]}: documentation changes law'
                documents.append(h)
            else:
                assert kind in {'consolidated', 'correction'} and author == PUBLISHER, f'{h[:10]}: law author {author!r}'
        assert documents and self.log[0][0] in documents, 'latest commit is not documentation'
        return f'all {len(documents)} documentation commits authored by Lexifina; all law commits by the publisher; every committer Lexifina'

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
        title_paths = [l['dir'].rstrip('/') + '/title.md' for l in self.laws]
        titles = self.files_at('main', title_paths)
        for l in self.laws:
            d = l['dir'].rstrip('/')
            title = titles[d + '/title.md']
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
    t.check('one ordinary baseline per Act and complete correction provenance', t.one_commit_per_act)
    t.check('In-force dates, noon Yellowknife, no merges or CRs', t.dates)
    t.check('only documentation authors are Lexifina', t.lexifina)
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
