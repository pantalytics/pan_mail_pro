/** @odoo-module */
/**
 * The sync level as a slider, with what reaches Odoo under it (#390, #419).
 *
 * Four stops, one to one with `sync_level`'s selection, in its order. Above
 * the slider one line on what Mail Pro does with a conversation; under it one
 * grid: the emails a mailbox receives and sends, in two groups, and an In
 * Odoo column that follows the slider. One row is in Odoo at every stop, one
 * at none, and each stop above the first adds exactly one row.
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
    _t("+ sent replies"),
    _t("+ existing contacts"),
    _t("+ new contacts"),
];

// The intro above the slider and the line under the grid. Both are true at
// every stop: the first is what Mail Pro does, the second what no stop lets in
// (`_gate_internal_domain`, `_gate_blocked_contact`).
const INTRO = _t("Mail Pro links each conversation to one Odoo record, such as a quote or a contact, and shows the conversation's emails on that record. The sync level decides which emails Mail Pro brings into Odoo from your mailbox. Your mail app keeps all of them.");
const NEVER = _t("Email between colleagues on your internal domains never comes into Odoo. Neither does email with a contact who has Block Email Sync turned on.");

// Row `from` is the first stop at which the row is in Odoo, `null` for a row
// no stop brings in. Each detail line is what `pan.mail.fetcher._gate_wanted`
// and `_folders_to_sync` do and where `pan.mail.matcher` links the
// conversation, in the reader's words; change one and change the other.
const GROUPS = [
    {
        name: _t("Receiving"),
        rows: [
            {
                name: _t("Replies"),
                from: 0,
                detail: _t("Someone replies in a conversation that is already in Odoo. Mail Pro adds the reply to that conversation, on the record it is linked to. The sender can be an existing or a new contact."),
            },
            {
                name: _t("New conversation from an existing contact"),
                from: 2,
                detail: _t("Someone who is already a contact in Odoo starts a new conversation with you. Mail Pro links the conversation to the record whose number is in the subject, otherwise to that contact."),
            },
            {
                name: _t("New conversation from a new contact"),
                from: 3,
                detail: _t("Someone who is not a contact in Odoo yet starts a new conversation with you. Mail Pro adds them as a contact, then links the conversation to the record whose number is in the subject, otherwise to the new contact."),
            },
        ],
    },
    {
        name: _t("Sending"),
        rows: [
            {
                name: _t("Replies from your mail app"),
                from: 1,
                detail: _t("You reply from your mail app in a conversation that is already in Odoo. Mail Pro adds your reply to that conversation, on the record it is linked to."),
            },
            {
                name: _t("New conversation from your mail app"),
                from: null,
                detail: _t("You start a new conversation in your mail app, with an existing or a new contact. Mail Pro never brings it into Odoo, because it can't tell which record to link the conversation to."),
            },
        ],
    },
];

// Every row once, numbered across the groups: the number is the row's key
// for its info dot and its flash, and `data-row` on screen.
const ROWS = GROUPS.flatMap((group) => group.rows);
ROWS.forEach((row, index) => {
    row.index = index;
});

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
        this.groups = GROUPS;
        this.rows = ROWS;
        this.intro = INTRO;
        this.never = NEVER;
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
        return row.from !== null && this.index >= row.from;
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
        // The rows whose In Odoo mark flips: every row whose stop lies
        // between the old position and the new one. A row no stop brings in
        // never flips.
        const low = Math.min(before, target);
        const high = Math.max(before, target);
        this.state.flash = this.rows
            .filter((row) => row.from !== null && row.from > low && row.from <= high)
            .map((row) => row.index);
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
