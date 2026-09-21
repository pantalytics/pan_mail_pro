#!/usr/bin/env python3
"""The manifest version: read it, raise it, write it back.

Odoo parses `__manifest__.py` with `ast.literal_eval`, so the version has to be
a literal in the file -- it can never be derived from a git tag at import time.
That is the whole reason this repo bumps it by editing the file, and the reason
the edit happens on `19.0` after the merge rather than inside every pull
request: two branches cut from the same base raise the same line to the same
number, and the second one conflicts on a value nobody chose.

This is the one place that knows the format. The pull-request check
(`tools/ci_version_bump.sh`) and the post-merge bump (`tools/release_bump.sh`)
both go through it, so they cannot disagree about what a minor bump is.

    tools/version.py read                       # 19.0.18.3.0
    tools/version.py read --ref=origin/19.0     # the same, out of a git object
    tools/version.py next minor                 # 19.0.19.0.0
    tools/version.py next patch --ref=HEAD^     # from another commit
    tools/version.py write 19.0.19.0.0
    tools/version.py code-changed <from> <to>   # exit 0 when module code moved

The version is `<series>.<major>.<minor>.<patch>`, where the series is Odoo's
own two-part number and is never bumped here: an addon repo carries one Odoo
version per branch, so a 18.0 branch is a branch, not a release.
"""
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / '__manifest__.py'

LEVELS = ('major', 'minor', 'patch')

# What counts as "module code", and so as a change an Odoo host has to be told
# about with a higher version. Docs, tests and CI scripts are deliberately not
# on the list: they change nothing on a customer's database, and a release for
# a typo in a README is noise on the releases page.
CODE_PATHS = (
    'models/', 'views/', 'wizard/', 'controllers/', 'security/',
    'data/', 'migrations/', 'static/', '__manifest__.py', '__init__.py',
)

# The version as it appears in the manifest. Anchored on the key so the pattern
# cannot match a version string in the description, and captured in three parts
# so writing it back preserves whatever quoting and spacing the file uses.
VERSION_RE = re.compile(r"""(['"]version['"]\s*:\s*['"])([^'"]+)(['"])""")


def _manifest_text(ref=None):
    if ref is None:
        return MANIFEST.read_text()
    return subprocess.run(
        ['git', '-C', str(REPO), 'show', f'{ref}:__manifest__.py'],
        capture_output=True, text=True, check=True).stdout


def read(ref=None):
    """The version string, from the working tree or from a git ref."""
    return ast.literal_eval(_manifest_text(ref))['version']


def parse(version):
    parts = version.split('.')
    if len(parts) != 5:
        raise SystemExit(f"version must be <series>.<major>.<minor>.<patch>, got {version!r}")
    try:
        return '.'.join(parts[:2]), [int(p) for p in parts[2:]]
    except ValueError:
        raise SystemExit(f"version segments must be numbers, got {version!r}") from None


def next_version(version, level):
    """The version one `level` above `version`, zeroing everything below it."""
    series, numbers = parse(version)
    index = LEVELS.index(level)
    numbers[index] += 1
    for lower in range(index + 1, len(numbers)):
        numbers[lower] = 0
    return '.'.join([series] + [str(n) for n in numbers])


def write(version):
    """Replace the version in the manifest, in place. Returns the old one."""
    parse(version)
    text = MANIFEST.read_text()
    new_text, count = VERSION_RE.subn(lambda m: m[1] + version + m[3], text, count=1)
    if count != 1:
        raise SystemExit("Could not find a single 'version' line in __manifest__.py")
    old = VERSION_RE.search(text)[2]
    MANIFEST.write_text(new_text)
    return old


def changed_files(base, head):
    out = subprocess.run(
        ['git', '-C', str(REPO), 'diff', '--name-only', base, head],
        capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def code_changed(base, head):
    """Did anything an Odoo host installs move between these two commits?"""
    return sorted(
        path for path in changed_files(base, head)
        if any(path == p or path.startswith(p) for p in CODE_PATHS)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    p_read = sub.add_parser('read', help='print the current version')
    p_read.add_argument('--ref', help='read it out of this git ref instead')

    p_next = sub.add_parser('next', help='print the version one level up')
    p_next.add_argument('level', choices=LEVELS)
    p_next.add_argument('--ref', help='count up from this git ref instead')

    p_write = sub.add_parser('write', help='write a version into the manifest')
    p_write.add_argument('version')

    p_code = sub.add_parser('code-changed', help='exit 0 when module code moved')
    p_code.add_argument('base')
    p_code.add_argument('head')
    p_code.add_argument('--quiet', action='store_true')

    args = parser.parse_args(argv)

    if args.command == 'read':
        print(read(args.ref))
    elif args.command == 'next':
        print(next_version(read(args.ref), args.level))
    elif args.command == 'write':
        old = write(args.version)
        print(f"__manifest__.py: {old} -> {args.version}", file=sys.stderr)
    elif args.command == 'code-changed':
        paths = code_changed(args.base, args.head)
        if not args.quiet:
            for path in paths:
                print(path)
        return 0 if paths else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
