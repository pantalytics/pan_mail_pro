#!/usr/bin/env bash
#
# Raise the manifest version on the mainline, once a merge has landed.
#
# This is the other half of tools/ci_version_bump.sh: pull requests leave the
# version alone, so there is exactly one place the number is chosen and exactly
# one branch it is chosen on. Nothing to conflict about, and no coordination
# between branches -- the order two pull requests merge in stops mattering.
#
#   tools/release_bump.sh                  # bump, commit and push
#   tools/release_bump.sh --dry-run        # say what it would do
#   BUMP_LEVEL=major tools/release_bump.sh # skip the label lookup
#
# Called by .github/workflows/release.yml, in the same run that tags the
# result: a push made with GITHUB_TOKEN does not start a new workflow run, so
# the tag has to be cut here rather than by a second workflow listening for it.
#
# The step comes from the merged pull request's labels -- `bump:patch` or
# `bump:major`, minor otherwise, which is what nearly every release in this
# repo has been.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

BRANCH=${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}
MARKER="[version]"

emit() { [ -n "${GITHUB_OUTPUT:-}" ] && echo "$1=$2" >> "$GITHUB_OUTPUT"; echo "$1=$2"; }

# The first parent is the branch's own previous tip, whether the merge was a
# squash, a merge commit or a merge queue's. So this diff is exactly "what this
# push added to the mainline", without needing the event payload.
if ! PREVIOUS=$(git rev-parse --verify --quiet HEAD^); then
    echo "HEAD has no parent -- nothing to compare against."
    emit bumped false
    emit version "$(tools/version.py read)"
    exit 0
fi

# Belt and braces. A GITHUB_TOKEN push cannot trigger this workflow, so the
# bump commit should never come back round; a deploy key or a PAT would.
if git log -1 --format=%s | grep -qF "$MARKER"; then
    echo "HEAD is a version commit already -- nothing to do."
    emit bumped false
    emit version "$(tools/version.py read)"
    exit 0
fi

OLD=$(tools/version.py read --ref="$PREVIOUS")
CURRENT=$(tools/version.py read)

# The one branch allowed to set the version itself is one that adds a
# migration: ci_version_bump.sh pins the two to each other, and raising it
# again here would step over the folder so the migration never runs.
if [ "$CURRENT" != "$OLD" ]; then
    echo "The merge brought its own version ($OLD -> $CURRENT). Leaving it alone."
    emit bumped false
    emit version "$CURRENT"
    exit 0
fi

if ! tools/version.py code-changed "$PREVIOUS" HEAD --quiet; then
    echo "No module code in this push -- nothing for an Odoo host to upgrade to."
    emit bumped false
    emit version "$CURRENT"
    exit 0
fi

# ---------------------------------------------------------------------------
# Which step. A label on the merged pull request, minor by default.
# ---------------------------------------------------------------------------
LEVEL=${BUMP_LEVEL:-}
if [ -z "$LEVEL" ] && [ -n "${GITHUB_REPOSITORY:-}" ] && command -v gh >/dev/null 2>&1; then
    LABELS=$(gh api "repos/${GITHUB_REPOSITORY}/commits/$(git rev-parse HEAD)/pulls" \
        --jq '.[].labels[].name' 2>/dev/null || true)
    case $'\n'"$LABELS"$'\n' in
        *$'\nbump:major\n'*) LEVEL=major ;;
        *$'\nbump:patch\n'*) LEVEL=patch ;;
    esac
fi
LEVEL=${LEVEL:-minor}

NEXT=$(tools/version.py next "$LEVEL")
echo "Module code changed. ${LEVEL} bump: ${CURRENT} -> ${NEXT}"

if [ -n "$DRY_RUN" ]; then
    emit bumped false
    emit version "$NEXT"
    exit 0
fi

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# The workflow serialises its runs on this branch, so a rejected push means
# somebody pushed directly. Rebuild the bump on top of what they pushed rather
# than forcing over it -- and if what they pushed already raised the version,
# stop: the release is theirs.
for attempt in 1 2 3 4; do
    NEXT=$(tools/version.py next "$LEVEL")
    tools/version.py write "$NEXT"
    git add __manifest__.py
    git commit -m "${MARKER} ${NEXT}" \
               -m "Raised after the merge, so no pull request has to guess it. See tools/release_bump.sh."

    if git push origin "HEAD:${BRANCH}"; then
        emit bumped true
        emit version "$NEXT"
        emit sha "$(git rev-parse HEAD)"
        exit 0
    fi

    echo "Push rejected (attempt ${attempt}); refetching ${BRANCH}."
    sleep $((2 ** attempt))
    git fetch --no-tags origin "$BRANCH"
    git reset --hard "origin/${BRANCH}"
    if [ "$(tools/version.py read)" != "$CURRENT" ]; then
        echo "${BRANCH} already carries $(tools/version.py read) -- somebody got there first."
        emit bumped false
        emit version "$(tools/version.py read)"
        exit 0
    fi
done

echo "::error::Could not push the version bump to ${BRANCH} after 4 attempts."
echo "::error::If the push was refused rather than raced, ${BRANCH}'s protection has to let"
echo "::error::github-actions[bot] through -- see docs/release.md."
exit 1
