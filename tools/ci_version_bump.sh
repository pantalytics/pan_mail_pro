#!/usr/bin/env bash
#
# Odoo hosts decide whether to run an upgrade by comparing the manifest version
# to the installed one. A code change that ships without a bump is simply never
# applied on the target database.
#
#   tools/ci_version_bump.sh <base-ref>     # e.g. origin/19.0
#
# Called by the version-bump job in ci.yml, and by tools/ci_lint.sh when
# BASE_REF is set.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF=${1:?usage: ci_version_bump.sh <base-ref>}

# Compares against the merge base with the mainline rather than the PR's base
# SHA, so this also runs on a plain branch push — before a PR exists, which is
# when the bump is easiest to forget.
if ! MERGE_BASE=$(git merge-base "$BASE_REF" HEAD 2>/dev/null); then
    echo "::error::No merge base with $BASE_REF; cannot check the version bump."
    exit 1
fi
echo "Comparing against $BASE_REF at $MERGE_BASE"

CODE_CHANGED=$(git diff --name-only "$MERGE_BASE" HEAD \
    -- pan_mail_pro/ | wc -l)
if [ "$CODE_CHANGED" -eq 0 ]; then
    echo "No module code touched — version bump not required."
    exit 0
fi

# The version is compared with the mainline's tip, not the merge base: another
# pull request may have taken the next number since this branch was cut. Branch
# protection requires the branch to be up to date, so this runs again after
# every merge and the second of two same-numbered branches has to bump again.
python3 - "$BASE_REF" <<'PY'
import ast, subprocess, sys
base = sys.argv[1]

def version_of(text):
    return tuple(int(p) for p in ast.literal_eval(text)['version'].split('.'))

def show(path):
    return subprocess.run(['git', 'show', f'{base}:{path}'], capture_output=True, text=True)

new = version_of(open('pan_mail_pro/__manifest__.py').read())
# Up to 19.0.30.2.0 the module sat at the root, with pan_mail_pro a symlink to
# it that `git show` does not follow.
found = show('pan_mail_pro/__manifest__.py')
if found.returncode:
    found = show('__manifest__.py')
    found.check_returncode()
old = version_of(found.stdout)
if new <= old:
    print(f"::error file=pan_mail_pro/__manifest__.py::Module code changed but version was not bumped "
          f"({'.'.join(map(str, old))} -> {'.'.join(map(str, new))}). "
          f"Odoo skips the upgrade without a higher version. "
          f"If {base} moved on, take the next free number above it.")
    sys.exit(1)
print(f"Version bumped: {'.'.join(map(str, old))} -> {'.'.join(map(str, new))}")
PY
