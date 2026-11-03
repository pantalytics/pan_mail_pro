/** @odoo-module */
/**
 * The sync level as a slider, with what lands where under it (#390).
 *
 * Four stops, one to one with `sync_level`'s selection, in its order. Under
 * the slider one grid: the four kinds of mail a mailbox receives, your mail
 * app always has each of them, and the Odoo record gets one more row per
 * stop. The radio it replaces listed four labels and showed none of that.
 *
 * Used on the mailbox form and on My Preferences (`x_pan_mail_sync_level`,
 * the same selection read through `res.users`). Odoo's own range input
 * carries the keyboard and the focus ring; the stop names under it are
 * clicked, not tabbed to, because the arrows already move the slider.
 */

import { Component, onWillUnmount } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { defineProps, useProps, useState } from "@pan_mail_pro/compat/owl";

// What each stop adds, as the name under it. Same order as the selection.
const STOPS = [
    _t("Replies"),
    _t("+ Sent Items"),
    _t("+ known contacts"),
    _t("+ new contacts"),
];

// Row `from` is the first stop at which the Odoo record gets it. Each detail
// line is what `pan.mail.fetcher._gate_wanted` and `_folders_to_sync` do,
// in the reader's words; change one and change the other.
const ROWS = [
    {
        name: _t("Replies"),
        from: 0,
        detail: _t("A reply to mail you sent from Odoo, or to a conversation Odoo already has, lands on the record it continues. This happens at every level."),
    },
    {
        name: _t("Sent Items"),
        from: 1,
        detail: _t("What you answer from your mail app on a conversation Odoo already has. A new mail you start there stays out, because Odoo can't tell where it belongs."),
    },
    {
        name: _t("New mail from a known contact"),
        from: 2,
        detail: _t("A new conversation from someone who is already a contact in Odoo. It lands on the record its subject names, or else on that contact."),
    },
    {
        name: _t("New mail from a new contact"),
        from: 3,
        detail: _t("A new conversation from an address Odoo doesn't know yet. Odoo creates the contact. Mail between your own internal domains always stays out."),
    },
];

// How long a row that just changed stays marked. The stylesheet's animation
// is the same length, and off under prefers-reduced-motion.
const FLASH_MS = 400;

let nextId = 0;

export class SyncLevelField extends Component {
    static template = "pan_mail_pro.SyncLevelField";
    static propsSchema = { ...standardFieldProps };

    setup() {
        this.props = useProps();
        this.id = `o_mailpro_sync_level_${nextId++}`;
        this.stops = STOPS;
        this.rows = ROWS;
        this.state = useState({ open: {}, flash: [] });
        this.flashTimer = null;
        onWillUnmount(() => clearTimeout(this.flashTimer));
    }

    get options() {
        return this.props.record.fields[this.props.name].selection;
    }

    get index() {
        const value = this.props.record.data[this.props.name];
        const found = this.options.findIndex(([key]) => key === value);
        return found < 0 ? 0 : found;
    }

    get valueText() {
        const option = this.options[this.index];
        return option ? option[1] : "";
    }

    get max() {
        return this.options.length - 1;
    }

    // Where a stop sits along the track, as a fraction.
    position(index) {
        return this.max ? index / this.max : 0;
    }

    inOdoo(row) {
        return this.index >= row.from;
    }

    choose(index) {
        if (this.props.readonly) {
            return;
        }
        const target = Math.max(0, Math.min(this.max, index));
        const before = this.index;
        if (target === before) {
            return;
        }
        // The rows whose Odoo mark flips: every row whose stop lies between
        // the old position and the new one.
        const low = Math.min(before, target);
        const high = Math.max(before, target);
        this.state.flash = this.rows
            .map((row, i) => (row.from > low && row.from <= high ? i : -1))
            .filter((i) => i >= 0);
        clearTimeout(this.flashTimer);
        this.flashTimer = setTimeout(() => {
            this.state.flash = [];
        }, FLASH_MS);
        this.props.record.update({ [this.props.name]: this.options[target][0] });
    }

    onInput(ev) {
        this.choose(parseInt(ev.target.value, 10));
    }

    toggle(index) {
        this.state.open[index] = !this.state.open[index];
    }
}

defineProps(SyncLevelField);

export const syncLevelField = {
    component: SyncLevelField,
    displayName: _t("Sync level"),
    supportedTypes: ["selection"],
    isEmpty: () => false,
};

registry.category("fields").add("mailpro_sync_level", syncLevelField);
