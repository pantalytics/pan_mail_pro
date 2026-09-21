/** @odoo-module */
/**
 * Linking a conversation, in the two steps the question actually has.
 *
 * "Where does this mail belong" is a model and then a record. The first half
 * is this dialog: a search box over the models this database files mail on,
 * because a chip row is fine for four models and useless for the fifth.
 *
 * The second half is Odoo's own `SelectCreateDialog` -- the list view with a
 * search bar, filters and a pager that every many2one on this database opens.
 * A picker of our own was a second implementation of that screen, and a worse
 * one: no pager, no filters, twelve rows and a box.
 *
 * The head start survives the move. `link_candidate_domain` says which records
 * are the correspondent's own, and that goes in as a default search facet --
 * so the list still opens on Vandermolen's quotes, and dropping it is the same
 * click as dropping any other facet.
 *
 * Creating a record from here stays off (`noCreate`): linking is about where
 * mail belongs, and a record invented to hold it is a different decision.
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
        // The correspondent, for the head start on step two. Optional: a
        // conversation with no contact behind it still has to be linkable.
        partnerId: { type: [Number, Boolean], optional: true },
        // What the dialog is for, when it is not linking an existing
        // conversation. New Email asks the same two questions.
        title: { type: String, optional: true },
        onSelect: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        // Step two outlives this component: it opens as this one closes, and
        // a service bound with `useService` never settles once its component
        // is gone. The env's own services do, which is what that step needs.
        this.services = this.env.services;
        this.searchRef = useRef("search");
        this.state = useState({
            search: "",
            rows: [],
            loading: true,
        });
        // Every keystroke is a query. The sequence number is what keeps a slow
        // answer to "vand" from landing on top of a fast one to "vanderm".
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

    /** The models, for whatever has been typed. */
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
        this.state.rows = (rows || []).map((row) => ({
            key: row.model,
            label: row.label,
            icon: row.icon || "",
            row,
        }));
        this.state.loading = false;
    }

    /** A model: hand the second question to Odoo's own dialog. */
    async choose(entry) {
        const target = entry.row;
        let seed = {};
        try {
            seed = await this.orm.call(
                "pan.mail.conversation", "link_candidate_domain", [], {
                    model: target.model,
                    partner_id: this.props.partnerId || false,
                }
            );
        } catch {
            seed = {}; // No head start is a working dialog; a traceback is not.
        }
        this.services.dialog.add(SelectCreateDialog, {
            resModel: target.model,
            // The question stays on screen: this dialog was opened to link a
            // conversation or to write a new mail, and "Search: Contact"
            // alone forgets which.
            title: `${this.title}: ${target.label}`,
            multiSelect: false,
            noCreate: true,
            dynamicFilters: seed.domain
                ? [{ description: seed.description, domain: seed.domain }]
                : [],
            onSelected: (resIds) => this.selected(target, resIds),
        });
        this.props.close();
    }

    /** What came back: one id, and the name the caller shows for it. */
    async selected(target, resIds) {
        const resId = Array.isArray(resIds) ? resIds[0] : resIds;
        if (!resId) {
            return;
        }
        let label = "";
        try {
            const [record] = await this.services.orm.read(
                target.model, [resId], ["display_name"]
            );
            label = record?.display_name || "";
        } catch {
            label = target.label; // The kind of record, when the name is not readable.
        }
        this.props.onSelect(target.model, resId, label);
    }
}
