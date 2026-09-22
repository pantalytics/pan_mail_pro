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
    -- 'models/**' 'views/**' 'wizard/**' 'controllers/**' \
       'security/**' 'data/**' 'migrations/**' 'static/**' '__manifest__.py' | wc -l)
if [ "$CODE_CHANGED" -eq 0 ]; then
    echo "No module code touched — version bump not required."
    exit 0
fi

python3 - "$MERGE_BASE" <<'PY'
import ast, subprocess, sys
base = sys.argv[1]

def version_of(text):
    return tuple(int(p) for p in ast.literal_eval(text)['version'].split('.'))

new = version_of(open('__manifest__.py').read())
old = version_of(subprocess.run(['git', 'show', f'{base}:__manifest__.py'],
                                capture_output=True, text=True, check=True).stdout)
if new <= old:
    print(f"::error file=__manifest__.py::Module code changed but version was not bumped "
          f"({'.'.join(map(str, old))} -> {'.'.join(map(str, new))}). "
          f"Odoo skips the upgrade without a higher version.")
    sys.exit(1)
print(f"Version bumped: {'.'.join(map(str, old))} -> {'.'.join(map(str, new))}")
PY
