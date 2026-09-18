/** @odoo-module */
/**
 * Linking a conversation, in the two steps the question actually has.
 *
 * "Where does this mail belong" is a model and then a record, and both halves
 * need a search box: a chip row is fine for the four models a database files
 * mail on and useless for the fifth, and a record is never picked from a list
 * of twelve. So: one dialog, one search input, two steps behind it.
 *
 * The second step opens on the correspondent's own records rather than on an
 * empty box. `link_candidates` decides what "their own" means (a `partner_id`
 * or an `email_from`, and nothing cleverer); this side only draws the list and
 * says whose it is. Typing replaces it with a plain `name_search`, so the
 * seeding is a head start and never a filter somebody has to escape.
 *
 * Creating a record from here stays off, as it was in the dialog this
 * replaced: linking is about where mail belongs, and a record invented to
 * hold it is a different decision.
 */

import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
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
        this.searchRef = useRef("search");
        this.state = useState({
            step: "model",
            search: "",
            target: null,      // the model chosen in step one
            rows: [],
            related: false,    // is this list the correspondent's own records
            partner: "",
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
        const opening = this.props.title || _t("Link this conversation");
        return this.state.target
            ? `${opening}: ${this.state.target.label}`
            : opening;
    }

    get placeholder() {
        return this.state.target
            ? _t("Search %s...", this.state.target.label)
            : _t("Search for a kind of record...");
    }

    /** The list under the box, for whichever step is open. */
    async load() {
        const sequence = ++this.sequence;
        this.state.loading = true;
        let result;
        try {
            result = this.state.target
                ? await this.orm.call("pan.mail.conversation", "link_candidates", [], {
                      model: this.state.target.model,
                      search: this.state.search,
                      partner_id: this.props.partnerId || false,
                  })
                : { rows: await this.orm.call("pan.mail.conversation", "link_targets", [], {
                      search: this.state.search,
                  }) };
        } catch {
            // An empty list and a working dialog beats a traceback over the
            // inbox: the reader can still change the search or step back.
            result = { rows: [] };
        }
        if (sequence !== this.sequence) {
            return; // A later search already answered.
        }
        this.state.rows = (result.rows || []).map((row) => ({
            key: row.model || row.id,
            label: row.label || row.name,
            row,
        }));
        this.state.related = Boolean(result.related);
        this.state.partner = result.partner || "";
        this.state.loading = false;
    }

    /** A row: the model in step one, the destination in step two. */
    async choose(entry) {
        if (this.state.target) {
            this.props.onSelect(this.state.target.model, entry.row.id, entry.label);
            this.props.close();
            return;
        }
        this.state.target = entry.row;
        this.state.step = "record";
        this.state.search = "";
        if (this.searchRef.el) {
            this.searchRef.el.value = "";
            this.searchRef.el.focus();
        }
        await this.load();
    }

    /** Back to the models, with the search cleared: it was a model search. */
    async back() {
        this.state.target = null;
        this.state.step = "model";
        this.state.search = "";
        if (this.searchRef.el) {
            this.searchRef.el.value = "";
            this.searchRef.el.focus();
        }
        await this.load();
    }
}
