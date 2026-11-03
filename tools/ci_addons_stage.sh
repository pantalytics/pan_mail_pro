#!/usr/bin/env bash
#
# Copy the CI-only addons into a folder an Odoo can load them from.
#
#   tools/ci_addons_stage.sh /tmp/pan_ci_addons
#   odoo-bin --addons-path=...,/tmp/pan_ci_addons -i pan_mail_pro_ci_dark,...
#
# tools/ci_addons/ holds addons the UI check needs and no customer should see:
# dark mode on the community image, and a stand-in for the platform's pairing
# start. In the repository their manifest is `__manifest__.ci`, a name Odoo
# does not read, because Odoo.sh puts every folder of a submodule that holds a
# module on the addons path and offers what it finds in Apps (#395). This
# copies each addon into DEST and gives the manifest back its real name there.
#
# DEST itself is kept and only its addons are replaced, so a container that
# already bind-mounts it (tools/ui_preview.sh --update) sees the new copy.
set -euo pipefail

DEST=${1:?usage: tools/ci_addons_stage.sh DEST}
SRC=$(cd "$(dirname "$0")/ci_addons" && pwd)

mkdir -p "$DEST"
for addon in "$SRC"/*/; do
    name=$(basename "$addon")
    if [ ! -f "$addon/__manifest__.ci" ]; then
        echo "tools/ci_addons/$name has no __manifest__.ci" >&2
        exit 1
    fi
    rm -rf "${DEST:?}/$name"
    cp -R "$addon" "$DEST/$name"
    mv "$DEST/$name/__manifest__.ci" "$DEST/$name/__manifest__.py"
done
