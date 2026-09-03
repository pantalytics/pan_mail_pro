#!/usr/bin/env bash
#
# Every static check CI runs: ruff, XML well-formedness, the Odoo 19 checklist,
# the architecture boundaries, the manifest and the version bump.
#
#   tools/ci_lint.sh              # everything that needs no base ref
#   BASE_REF=19.0 tools/ci_lint.sh  # + the diff-shape and version-bump checks
#
# .github/workflows/ci.yml calls this file rather than repeating the checks, so
# a cloud session and a GitHub runner cannot disagree about what "lint passes"
# means. Run it from the repository root.
set -uo pipefail

cd "$(dirname "$0")/.."

FAILURES=0
step() { printf '\n=== %s\n' "$1"; }
fail() { echo "::error::$1"; FAILURES=$((FAILURES + 1)); }

# ---------------------------------------------------------------------------
step "Odoo series from the manifest"
SERIES=$(python3 - <<'PY'
import ast
manifest = ast.literal_eval(open('__manifest__.py').read())
version = manifest['version']
parts = version.split('.')
assert len(parts) == 5, f"version must be <series>.<x>.<y>.<z> style, got {version!r}"
print(f"{parts[0]}.{parts[1]}")
PY
) || fail "Could not read the version from __manifest__.py"
echo "Odoo ${SERIES}"

# ---------------------------------------------------------------------------
step "ruff"
if command -v ruff >/dev/null 2>&1; then
    ruff check . || fail "ruff found problems."
else
    fail "ruff is not installed (pip install ruff==0.14.0)."
fi

# ---------------------------------------------------------------------------
step "XML is well-formed"
python3 - <<'PY' || fail "Malformed XML."
import os, sys
from xml.etree import ElementTree
# os.walk, not rglob: the repo carries a `pan_mail_pro -> .` symlink for the
# local Docker addons_path, and rglob would follow it forever.
failed = checked = 0
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__'}]
    for name in files:
        if not name.endswith('.xml'):
            continue
        path = os.path.join(root, name)
        checked += 1
        try:
            ElementTree.parse(path)
        except ElementTree.ParseError as exc:
            print(f"::error file={path}::{exc}")
            failed += 1
print(f"Parsed {checked} XML file(s), {failed} malformed.")
sys.exit(1 if failed else 0)
PY

# ---------------------------------------------------------------------------
step "Odoo 19 — no attrs= in views"
if grep -rn 'attrs=' --include='*.xml' .; then
    fail "'attrs=' is removed in Odoo 17+. Use invisible/readonly/required directly."
else
    echo "OK: no attrs= found."
fi

step "Odoo 19 — no numbercall on cron records"
if grep -rn 'name="numbercall"' --include='*.xml' .; then
    fail "'numbercall' is deprecated on ir.cron in Odoo 17+."
else
    echo "OK: no numbercall found."
fi

# ---------------------------------------------------------------------------
step "Boundary — anthropic imported only inside models/ai/"
if grep -rn 'import anthropic\|from anthropic' --include='*.py' . | grep -v '^./models/ai/'; then
    fail "The AI vendor SDK may only be imported inside models/ai/."
else
    echo "OK: AI vendor boundary intact."
fi

step "Boundary — no AI call inside the mail path"
if grep -n 'get_ai_backend\|pan\.mail\.ai' models/pan_mail_fetcher.py models/mail_mail.py; then
    fail "AI must never be called from the fetcher or the send path; a slow model call there stalls a mailbox."
else
    echo "OK: AI cannot block mail."
fi

step "Boundary — provider SDKs stay inside models/providers/"
if grep -rn 'graph\.microsoft\.com\|gmail\.googleapis\.com' --include='*.py' . \
   | grep -v '^./models/providers/' | grep -v '^./tests/'; then
    fail "Provider URLs may only appear inside models/providers/."
else
    echo "OK: provider boundary intact."
fi

# ---------------------------------------------------------------------------
step "Every model is named in ARCHITECTURE.md"
MISSING=0
for model in $(grep -rhoP "^\s+_name = '\K[^']+" models/ | sort -u); do
    if ! grep -qF "\`$model\`" ARCHITECTURE.md; then
        echo "::error file=ARCHITECTURE.md::Model '$model' is not documented in ARCHITECTURE.md"
        MISSING=1
    fi
done
if [ "$MISSING" -ne 0 ]; then
    fail "Add the model to the map in ARCHITECTURE.md §1, in backticks so this check can find it."
else
    echo "OK: every model is documented."
fi

# ---------------------------------------------------------------------------
step "Every test file is imported by tests/__init__.py"
# Odoo collects tests through tests/__init__.py. A file on disk that nothing
# imports does not exist as far as the runner is concerned: it lints clean, it
# is committed, CI goes green, and it never runs. That happened to the file
# asserting the sync sends no mail, so the green build proved the opposite of
# what it appeared to. ci_assert_tests.sh cannot see it -- a suite that never
# had the tests has no count to have lost.
ON_DISK=$(cd tests && ls test_*.py 2>/dev/null | sed 's/\.py$//' | sort)
# sed, not `grep -oP`: -P is GNU-only and this must run on macOS too.
IMPORTED=$(sed -n 's/^from \. import \(test_[A-Za-z0-9_]*\).*/\1/p' tests/__init__.py | sort)
if [ "$ON_DISK" != "$IMPORTED" ]; then
    comm -23 <(echo "$ON_DISK") <(echo "$IMPORTED") | while read -r f; do
        [ -n "$f" ] && echo "::error file=tests/__init__.py::tests/$f.py is never imported, so it never runs"
    done
    comm -13 <(echo "$ON_DISK") <(echo "$IMPORTED") | while read -r f; do
        [ -n "$f" ] && echo "::error file=tests/__init__.py::imports $f, which does not exist"
    done
    fail "Add the missing line to tests/__init__.py (or remove the stale import)."
else
    echo "OK: every test file is on the list."
fi

# ---------------------------------------------------------------------------
step "Migration folder is not above the manifest version"
VERSION=$(python3 -c "import ast,pathlib;print(ast.literal_eval(pathlib.Path('__manifest__.py').read_text())['version'])")
LATEST=$(ls migrations 2>/dev/null | sort -V | tail -1)
if [ -n "$LATEST" ]; then
    HIGHEST=$(printf '%s\n%s\n' "$VERSION" "$LATEST" | sort -V | tail -1)
    if [ "$HIGHEST" != "$VERSION" ]; then
        fail "migrations/$LATEST is above manifest version $VERSION."
    elif [ "$LATEST" = "$VERSION" ]; then
        echo "OK: migrations/$LATEST runs on upgrade to $VERSION."
    else
        echo "OK: latest migration $LATEST is below $VERSION (already applied)."
    fi
fi

# ---------------------------------------------------------------------------
step "Manifest data files exist"
python3 - <<'PY' || fail "Manifest references files that are not on disk."
import ast, pathlib, sys
manifest = ast.literal_eval(open('__manifest__.py').read())
missing = [f for f in manifest.get('data', []) if not pathlib.Path(f).is_file()]
missing += [f for f in manifest.get('assets', {}).get('web.assets_backend', [])
            if not pathlib.Path(f.split('/', 1)[1]).is_file()]
if missing:
    print("Referenced in __manifest__.py but missing on disk:")
    print("\n".join(f"  - {m}" for m in missing))
    sys.exit(1)
print("All manifest data/asset files exist.")
PY

# ---------------------------------------------------------------------------
# The two checks that need something to compare against. On a GitHub runner
# BASE_REF is the PR base; in a cloud session, export it yourself.
# ---------------------------------------------------------------------------
if [ -n "${BASE_REF:-}" ]; then
    if ! MERGE_BASE=$(git merge-base "$BASE_REF" HEAD 2>/dev/null); then
        git fetch --no-tags --quiet --depth=200 origin "${BASE_REF#origin/}" 2>/dev/null || true
        MERGE_BASE=$(git merge-base "$BASE_REF" HEAD 2>/dev/null) || MERGE_BASE=""
    fi
    if [ -z "$MERGE_BASE" ]; then
        fail "No merge base with $BASE_REF; cannot check the diff shape."
    else
        step "Models changed implies tests changed (vs $BASE_REF)"
        CHANGED=$(git diff --name-only "$MERGE_BASE" HEAD)
        if echo "$CHANGED" | grep -q '^models/' && ! echo "$CHANGED" | grep -q '^tests/'; then
            fail "This branch changes models/ but no test. Add or update one."
        else
            echo "OK: model changes are accompanied by tests."
        fi

        step "Manifest version bumped (vs $BASE_REF)"
        tools/ci_version_bump.sh "$BASE_REF" || fail "Module code changed without a version bump."
    fi
else
    echo
    echo "BASE_REF not set — skipping the diff-shape and version-bump checks."
fi

echo
if [ "$FAILURES" -ne 0 ]; then
    echo "$FAILURES check(s) failed."
    exit 1
fi
echo "All static checks passed."
