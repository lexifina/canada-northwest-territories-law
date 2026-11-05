"""Append the two sourced Délîne commencement snapshots to the published baseline.

Requires Python, pypdf and the repository's tools/build/check_repo.py. Run only in
a clean scratch clone whose main is the documented baseline. No network actions.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

BASE = 'aca9da1d1b990ea21e6ef337e873ed8d302de19a'
ACT = 'acts/deline-final-self-government-agreement'
URLS = [
    'https://gnwt.ca/sites/default/files/legacy_justice/files/bills/17/2015.5/bill_46.pdf',
    'https://gnwt.ca/sites/default/files/legacy_justice/files/legislation/deline-final-self-government-agreement/deline-final-self-government-agreement.a.pdf']
BASIS = ('Certified Act section 20(2); consolidation title: section 5 in force March 12, 2015; '
         'Act except section 5 in force September 1, 2016, SI-007-2016.')

def git(repo, *args, data=None):
    return subprocess.run(['git', '-c', 'gc.auto=0', '-C', str(repo), *args], input=data,
                          capture_output=True, check=True).stdout

def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', re.sub(r'-\s*\n\s*', '-', text)).strip()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--official-current', type=Path, required=True)
    ap.add_argument('--official-certified', type=Path, required=True)
    ap.add_argument('--source-check', type=Path, help='Reuse an existing independent comparison for the exact source hashes and baseline.')
    args = ap.parse_args()
    repo = args.repo.resolve()
    assert git(repo, 'rev-parse', 'HEAD').decode().strip() == BASE, 'Requires the exact published baseline.'
    assert git(repo, 'symbolic-ref', '--short', 'HEAD').decode().strip() == 'main'
    assert not git(repo, 'status', '--porcelain'), 'Requires a clean scratch worktree.'
    assert hashlib.sha256(args.official_current.read_bytes()).hexdigest() == 'e3ba058027ee1efbfa4659143027381f399d0cc9f1d94870789c680acfcd1fa7'
    assert hashlib.sha256(args.official_certified.read_bytes()).hexdigest() == '25b6033e1dc91f585e60519ff7dc40672371569240979675a3af33ed55b4e194'
    sys.path.insert(0, str(repo / 'tools/build'))
    import check_repo
    import pypdf
    receipt = json.loads(args.source_check.read_text(encoding='utf-8')) if args.source_check else None
    if receipt:
        checked = receipt['baseline_pdf_comparison']
        verified = receipt['certified_vs_published_running_text']
        assert checked['missing'] == checked['extra'] == 0 and checked['raw_chars'] == 11331
        assert verified['equal'] and verified['source_sha256'] == verified['published_sha256'] == '3958806b7b68cb4fba118f1ba1fd6bb65fec1ca122a2337802c2c5112011e8e1'
    else:
        checked = check_repo.check(repo, BASE, ACT, args.official_current)
        assert checked['missing'] == checked['extra'] == 0
        reader = pypdf.PdfReader(str(args.official_certified))
        source = '\n'.join(check_repo.page_text([g for g in check_repo.glyphs(reader, p) if g[0] >= 92])
                           for p in reader.pages[2:])
        source = source[source.index('Whereas'):]
    names = git(repo, 'show', f'{BASE}:{ACT}/index.txt').decode().splitlines()
    objects = git(repo, 'cat-file', '--batch', data=''.join(f'{BASE}:{ACT}/{n}\n' for n in names).encode())
    original, offset = {}, 0
    for n in names:
        end = objects.index(b'\n', offset)
        header = objects[offset:end].split()
        assert header[1] == b'blob'
        size = int(header[2])
        original[n] = objects[end + 1:end + 1 + size].decode()
        offset = end + size + 2
    original['index.txt'] = git(repo, 'show', f'{BASE}:{ACT}/index.txt').decode()
    body = '\n'.join(original[n] for n in names if n != 'title.md')
    body = '\n'.join(l[3:] if l.startswith('## ') else l for l in body.splitlines() if not l.startswith('> '))
    body = body[body.index('Whereas'):]
    if receipt:
        assert hashlib.sha256(normalize(body).encode()).hexdigest() == verified['published_sha256']
    else:
        assert normalize(source) == normalize(body), 'Certified running text differs from the baseline.'
    partial = {'title.md': '\n'.join(original['title.md'].splitlines()[:2]) + '\n',
               's-005.md': original['s-005.md'], 'index.txt': 'title.md\ns-005.md\n'}
    old_version = git(repo, 'log', '-1', '--format=%H', BASE, '--', ACT).decode().strip()
    author = 'Legislation Division, Department of Justice (Government of the Northwest Territories) <legislation-division@justice-gov-nt-ca.invalid>'
    committer = 'Lexifina <canada-northwest-territories-law@lexifina.com>'
    os.environ.update(GIT_AUTHOR_NAME=author.split(' <')[0],
                      GIT_AUTHOR_EMAIL='legislation-division@justice-gov-nt-ca.invalid',
                      GIT_COMMITTER_NAME='Lexifina', GIT_COMMITTER_EMAIL='canada-northwest-territories-law@lexifina.com')
    stream = bytearray()
    def data(value):
        value = value.encode('utf-8')
        stream.extend(f'data {len(value)}\n'.encode() + value + b'\n')
    snapshot, parent = original, BASE
    for i, (day, end, current) in enumerate([('2015-03-12', '2016-08-31', partial),
                                            ('2016-09-01', '', original)], 1):
        removed = sorted(set(snapshot) - set(current))
        changed = sorted(n for n in current if current[n] != snapshot.get(n))
        reason = ('Back-fill section 5-only commencement state from certified section 20(2).'
                  if i == 1 else 'Back-fill complete commencement state; restore unchanged published consolidation.')
        message = (f'Deline Final Self-Government Agreement Act: historical commencement state {day}\n\n'
                   f'Kind: correction\nIn-force: {day}\nCorrects: {ACT} {day}..{end}\n'
                   f'Supersedes: {old_version if i == 1 else "none"}\nReason: {reason}\nAct-path: {ACT}\n'
                   f'In-force-basis: {BASIS}\nSource: {URLS[0]}\nSource: {URLS[1]}\n'
                   + ''.join(f'Files: {ACT}/{n}\n' for n in sorted(set(current) | set(removed))))
        stamp = int(datetime.fromisoformat(day + 'T12:00:00-06:00').timestamp())
        stream.extend(f'commit refs/heads/main\nmark :{i}\nauthor {author} {stamp} -0600\ncommitter {committer} {stamp} -0600\n'.encode())
        data(message)
        stream.extend(f'from {parent}\n'.encode())
        for n in removed:
            stream.extend(f'D {ACT}/{n}\n'.encode())
        for n in changed:
            stream.extend(f'M 100644 inline {ACT}/{n}\n'.encode())
            data(current[n])
        stream.extend(b'\n')
        snapshot, parent = current, f':{i}'
    stream.extend(b'done\n')
    git(repo, 'fast-import', '--quiet', data=bytes(stream))
    git(repo, 'reset', '--hard', 'HEAD')
    print(json.dumps({'base': BASE, 'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
                      'commits': git(repo, 'rev-list', '--reverse', f'{BASE}..HEAD').decode().splitlines()}, indent=2))

if __name__ == '__main__':
    main()
