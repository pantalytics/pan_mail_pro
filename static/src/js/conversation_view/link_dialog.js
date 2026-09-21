/** @odoo-module */
/**
 * Linking a conversation, in the two steps the question actually has.
 *
 * "Where does this mail belong" is a model and then a record. The first half
 * is ours: a searchable list of the kinds of record this database files mail
 * on, each with the tile of the app it belongs to. The second half is Odoo's
 * own `SelectCreateDialog` over that model's list view -- the search bar, the
 * filters, the paging and the columns every many2one on this database already
 * opens -- because a record picker with one search box and twelve rows was a
 * worse copy of a control the reader already knows.
 *
 * The second step opens on the correspondent's own records rather than on the
 * whole table. `link_scope` decides what "their own" means (a `partner_id` or
 * an `email_from`, and nothing cleverer); this side turns the answer into a
 * filter facet, on by default and one click to remove, so the seeding is a
 * head start and never a filter somebody has to escape.
 *
 * Creating a record from here stays off, as it was in the dialog this
 * replaced: linking is about where mail belongs, and a record invented to
 * hold it is a different decision.
 */

import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { _t } from "@web/core/l10n/translation";

export class LinkDialog extends Component {
    static template = "pan_mail_pro.LinkDialog";
    static components = { Dialog };
    static props = {
        // The correspondent, for the head start on step two. Both optional:
        // a conversation with no contact behind it still has to be linkable.
        partnerId: { type: [Number, Boolean], optional: true },
        correspondent: { type: String, optional: true },
        // What the dialog is for, when it is not linking an existing
        // conversation. New Email asks the same two questions.
        title: { type: String, optional: true },
        onSelect: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.searchRef = useRef("search");
        this.state = useState({
            search: "",
            rows: [],
            loading: true,
        });
        // Every keystroke is a query. The sequence number is what keeps a slow
        // answer to "lea" from landing on top of a fast one to "lead".
        this.sequence = 0;
        this.onSearch = useDebounced((event) => {
            this.state.search = event.target.value;
            this.load();
        }, 250);
        onWillStart(() => this.load());
    }

    get title() {
        return this.props.title || _t("Link this conversation");
    }

    /** The kinds of record under the box. */
    async load() {
        const sequence = ++this.sequence;
        this.state.loading = true;
        let rows;
        try {
            rows = await this.orm.call("pan.mail.conversation", "link_targets", [], {
                search: this.state.search,
            });
        } catch {
            // An empty list and a working dialog beats a traceback over the
            // inbox: the reader can still change the search.
            rows = [];
        }
        if (sequence !== this.sequence) {
            return; // A later search already answered.
        }
        this.state.rows = rows || [];
        this.state.loading = false;
    }

    /**
     * A kind of record picked: hand over to Odoo's own picker for the record.
     *
     * This dialog closes first, so the picker is the only thing on screen;
     * closing that one is the way back, the same as everywhere else in Odoo.
     */
    async choose(target) {
        this.props.close();
        let scope = { domain: false, partner: "" };
        try {
            scope = await this.orm.call("pan.mail.conversation", "link_scope", [], {
                model: target.model,
                partner_id: this.props.partnerId || false,
            });
        } catch {
            // No head start, then: the picker still opens on the whole list.
        }
        this.dialog.add(SelectCreateDialog, {
            resModel: target.model,
            title: `${this.title}: ${target.label}`,
            multiSelect: false,
            noCreate: true,
            // Whose records these are, as a facet the reader can take off.
            dynamicFilters: scope.domain
                ? [{ description: scope.partner, domain: scope.domain }]
                : [],
            onSelected: async ([resId]) => {
                // The record's name and the model's own label come along: the
                // caller shows the record in a pane whose head names both.
                const [record] = await this.orm.read(target.model, [resId], ["display_name"]);
                this.props.onSelect(target.model, resId, record.display_name, target.label);
            },
        });
    }
}
