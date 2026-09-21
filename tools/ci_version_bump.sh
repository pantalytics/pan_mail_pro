#!/usr/bin/env bash
#
# The manifest version does not belong in a pull request.
#
# Odoo hosts decide whether to run an upgrade by comparing the manifest version
# to the installed one, so every code change needs a higher number -- but the
# number is always "whatever is on the mainline, plus one". Two branches cut
# from the same base both write the same value, and the second one to be merged
# conflicts on a line whose content nobody chose. Worse, when the conflict was
# resolved by keeping either side, several merges shipped under one version:
# 19.0.17.1.0 covers four pull requests and got one tag.
#
# So the bump moved to where the answer is knowable: `tools/release_bump.sh`
# raises it on `19.0` once the merge has landed, and this check makes sure
# nothing gets there first.
#
#   tools/ci_version_bump.sh <base-ref>     # e.g. origin/19.0
#
# Called by the version-bump job in ci.yml, and by tools/ci_lint.sh when
# BASE_REF is set.
#
# The one exception is a migration. `migrations/<version>/` has to name the
# version it runs on, and the author cannot know that until the merge, so a
# branch that adds one sets the version by hand and this check pins the two to
# each other. There are 8 such folders across 18 minor versions; merge them one
# at a time and the conflict this file exists to prevent cannot happen.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF=${1:?usage: ci_version_bump.sh <base-ref>}

# Compares against the merge base with the mainline rather than the PR's base
# SHA, so this also runs on a plain branch push -- before a PR exists, which is
# when the version is easiest to touch by accident.
if ! MERGE_BASE=$(git merge-base "$BASE_REF" HEAD 2>/dev/null); then
    echo "::error::No merge base with $BASE_REF; cannot check the version."
    exit 1
fi
echo "Comparing against $BASE_REF at $MERGE_BASE"

OLD=$(tools/version.py read --ref="$MERGE_BASE")
NEW=$(tools/version.py read)

# --diff-filter=A: a branch that edits an existing migration is fixing a script
# that already shipped under its own version, and needs no new one.
#
# The working tree, not HEAD, and untracked files alongside the diff: the
# version above is read from the working tree, so the migration has to be read
# from the same place or a folder you just created reads as absent and the
# check tells you to put the version back. On a runner the tree is clean and
# the two are the same thing.
ADDED_MIGRATION=$({ git diff --name-only --diff-filter=A "$MERGE_BASE" -- 'migrations/*';
                    git ls-files --others --exclude-standard -- 'migrations/*'; } \
    | cut -d/ -f2 | sort -V | tail -1)

if [ -n "$ADDED_MIGRATION" ]; then
    echo "Branch adds migrations/$ADDED_MIGRATION"
    if [ "$NEW" != "$ADDED_MIGRATION" ]; then
        echo "::error file=__manifest__.py::This branch adds migrations/$ADDED_MIGRATION, so"
        echo "::error file=__manifest__.py::__manifest__.py must say that version. It says $NEW."
        echo "::error::Odoo runs a migration folder only when the manifest version reaches it."
        exit 1
    fi
    if [ "$(printf '%s\n%s\n' "$OLD" "$NEW" | sort -V | tail -1)" = "$OLD" ]; then
        echo "::error file=__manifest__.py::Migration version $NEW is not above $BASE_REF's $OLD."
        exit 1
    fi
    echo "OK: version $NEW is set by hand for the migration, and is above $OLD."
    exit 0
fi

if [ "$NEW" != "$OLD" ]; then
    echo "::error file=__manifest__.py::The version moved ($OLD -> $NEW) and this branch adds no migration."
    echo "::error file=__manifest__.py::Put it back to $OLD. The mainline raises it after the merge"
    echo "::error file=__manifest__.py::(tools/release_bump.sh); a minor bump by default, or label the"
    echo "::error file=__manifest__.py::pull request 'bump:patch' or 'bump:major' to ask for another step."
    exit 1
fi

if tools/version.py code-changed "$MERGE_BASE" HEAD --quiet; then
    echo "OK: module code changed; 19.0 will raise $OLD after the merge."
else
    echo "OK: no module code touched; the merge will release nothing."
fi
