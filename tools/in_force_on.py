"""Print an Act of the Northwest Territories, or one section of it, as this repository has it on a date.

    python tools/in_force_on.py 2026-10-28 acts/land-titles            # the whole Act, in order
    python tools/in_force_on.py 2026-10-28 acts/land-titles 46         # section 46 (file s-046.md)
    python tools/in_force_on.py 2026-10-28 acts/land-titles 59.2       # section 59.2
    python tools/in_force_on.py 2026-10-28 acts/land-titles schedule-a

Reads the `In-force:` trailer of each commit (the real date, never the commit date). The source keeps only the
current consolidation, so a date before an Act's consolidation date has no version here. Standard library only.
"""
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def unit_file(unit: str) -> str:
    if unit.endswith('.md'):
        return unit
    m = re.fullmatch(r'(?:s-?)?0*(\d+)((?:\.\d+)*)', unit)
    if m:
        return f's-{int(m.group(1)):03d}{m.group(2)}.md'
    return unit + '.md'


def commit_on(day: str, path: str) -> tuple[str | None, str | None]:
    """The last commit whose In-force day is on or before `day` that touched `path`, and the earliest day known."""
    fmt = '%H%x1f%(trailers:key=In-force,valueonly,separator=)'
    earliest = None
    for line in git('log', '--first-parent', f'--format={fmt}', 'HEAD', '--', path).splitlines():
        h, d = (line.split('\x1f') + [''])[:2]
        d = d.strip()
        if not d:
            continue
        earliest = d
        if d <= day:
            return h, d
    return None, earliest


def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 3 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', sys.argv[1]):
        sys.exit(__doc__)
    day, law = sys.argv[1], sys.argv[2].strip('/')
    unit = sys.argv[3] if len(sys.argv) > 3 else None
    path = f'{law}/{unit_file(unit)}' if unit else law
    h, earliest = commit_on(day, path)
    if not h:
        if earliest:
            sys.exit(f'{path}: no version on or before {day}; the record starts at {earliest}, the consolidation '
                     'the Legislation Division publishes (it keeps no earlier text)')
        sys.exit(f'{path}: not found')
    if unit:
        sys.stdout.write(git('show', f'{h}:{path}'))
        return
    names = [n for n in git('show', f'{h}:{law}/index.txt').splitlines() if n]
    for n in names:
        sys.stdout.write(git('show', f'{h}:{law}/{n}') + '\n')


if __name__ == '__main__':
    main()
