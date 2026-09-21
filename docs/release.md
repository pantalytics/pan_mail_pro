# Releasing: how a merge becomes a version

Internal runbook. Not published to the knowledge base.

A pull request here never touches `'version'` in `__manifest__.py`. The
mainline raises it once the merge has landed, tags the result and publishes the
release. Nothing to coordinate between branches, and nothing to conflict on.

## Why it is not in the pull request

Odoo decides whether to run an upgrade by comparing the manifest version to the
installed one, so every code change needs a higher number. But that number is
always "the mainline's, plus one", which is not a decision the author of a
branch can make: two branches cut from the same base both write it, and the
second one to be merged conflicts on a line whose content nobody chose.

Resolving that conflict by keeping either side is worse than it looks. Four
merged pull requests (#222, #226, #227, #228) all shipped as `19.0.17.1.0`, and
`release.yml` skips a tag that already exists, so three of them were never
tagged and never appeared on the releases page. A customer on `19.0.17.1.0` and
a customer on `19.0.17.1.0` had different code.

## What happens now

| Step | Where | What it does |
|------|-------|--------------|
| Pull request | `tools/ci_version_bump.sh`, job **Manifest version** | Fails if the branch writes a version |
| Merge queue | branch protection on `19.0` | Tests each pull request against the real merged result, one at a time |
| After the merge | `tools/release_bump.sh`, in `release.yml` | Raises the version, commits it to `19.0` |
| Same run | `release.yml` | Tags `v<version>`, publishes the release with generated notes |

The bump and the tag are in **one job** on purpose: a push made with
`GITHUB_TOKEN` does not start a new workflow run, so a second workflow
listening for the bump commit would never fire.

## How big a step

Minor by default, which is what nearly every release in this repo has been.
Two labels change it:

| Label | `19.0.18.3.0` becomes | Use it for |
|-------|----------------------|------------|
| *(none)* | `19.0.18.4.0` | A feature, a screen, a new field |
| `bump:patch` | `19.0.18.3.1` | A fix with no new behaviour |
| `bump:major` | `19.0.19.0.0` | A removal, a rename, anything a customer has to read about |

Put the label on the pull request before it merges. The bump reads the labels
of whichever pull request contains the merge commit.

A push that touches only docs, tests or `tools/` raises nothing and releases
nothing: there is no database change for an Odoo host to upgrade to.

## The one exception: migrations

`migrations/<version>/` has to name the version it runs on, and Odoo executes
the folder only when the manifest version reaches it. The author cannot know
that number before the merge, so a branch that **adds** a migration sets the
version by hand, and `tools/ci_version_bump.sh` pins the two together: the
manifest must say exactly what the new folder is called. `release_bump.sh` then
leaves that version alone.

Merge those one at a time. There are 8 migration folders across 18 minor
versions, so this is the rare path and not worth automating around.

A branch that *edits* an existing migration is fixing a script that already
shipped under its own version, and needs no new one.

## Settings this depends on

Both are repository settings, not code, and both have to be set once:

1. **Branch protection on `19.0` must let `github-actions[bot]` push.**
   Settings → Rules → the `19.0` ruleset → bypass list → add the
   **GitHub Actions** actor. Without it the bump is refused and the release job
   fails with a message saying so. Nothing else can push to `19.0`.
2. **The merge queue is on for `19.0`**, with `Lint and Odoo 19 checklist`,
   `Manifest version`, `Tests`, `UI checks` and both `Upgrade from …` jobs
   required. The queue builds each pull request on top of the real base and
   merges only if that is green, which is also the fix for the auto-merge
   racing the slow jobs (#193).

The required check was renamed from *Manifest version bumped* to
**Manifest version** when it inverted, so the required-checks list has to be
updated to match or it will wait forever for a check that no longer reports.

## Running it yourself

```bash
BASE_REF=origin/19.0 tools/ci_lint.sh   # includes the version check
tools/ci_version_bump.sh origin/19.0    # just that check

tools/version.py read                   # 19.0.18.3.0
tools/version.py next minor             # 19.0.18.4.0
BRANCH=19.0 tools/release_bump.sh --dry-run
```
