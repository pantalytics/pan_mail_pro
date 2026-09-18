/** @odoo-module */
/**
 * The conversation view: folders, conversations, the thread, and the record.
 *
 * Everything on this screen is read through `pan.mail.conversation`, which
 * stores nothing. Every action is Odoo's own method on the record underneath,
 * so what happens here is what would have happened in the chatter.
 *
 * The record pane mounts Odoo's own form view. That is the one load-bearing
 * assumption in the whole screen, so it sits behind an error boundary: if the
 * form cannot render, the pane falls back to a link and the rest of the inbox
 * keeps working.
 *
 * The panes themselves are draggable and the two outer ones fold away; that
 * lives in `use_panes.js`, because how wide a pane is has nothing to do with
 * what is in it.
 */

import { Component, useState, useSubEnv, onWillStart, onError, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { usePanes } from "./use_panes";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";

const PAGE = 30;

// The quoted history, as the clients people write to us from mark it:
// Outlook (the divider it inserts and the header block it draws), Gmail and
// Apple Mail (`gmail_quote`, and the blockquote everyone falls back to),
// Thunderbird (`moz-cite-prefix`), and Odoo's own composer, which tags the
// history it quotes with `data-o-mail-quote`.
const QUOTE_MARKERS = [
    "blockquote",
    ".gmail_quote",
    ".moz-cite-prefix",
    "[data-o-mail-quote]",
    "#divRplyFwdMsg",
    "#appendonsend",
    ".OutlookMessageHeader",
].join(", ");

/** The record pane, isolated so a form-view failure cannot take the page. */
export class RecordPane extends Component {
    static template = "pan_mail_pro.RecordPane";
    static components = { View };
    static props = { record: { type: Object, optional: true } };

    setup() {
        this.action = useService("action");
        this.state = useState({ failed: false });
        // The form view inherits the action's `config` and names the action
        // after the record it shows. That is right when the form *is* the
        // screen and wrong here, where it is one pane of the Inbox: the
        // breadcrumb and the tab would read as the record, or as nothing at
        // all. The pane gets a config whose rename is a no-op.
        useSubEnv({
            config: {
                ...this.env.config,
                setDisplayName: () => {},
            },
        });
        onError((error) => {
            console.warn("[Mail Pro] record pane fell back", error);
            this.state.failed = true;
        });
    }

    get viewProps() {
        return {
            type: "form",
            resModel: this.props.record.model,
            resId: this.props.record.res_id,
            display: { controlPanel: false },
        };
    }

    openRecord() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: this.props.record.model,
            res_id: this.props.record.res_id,
            views: [[false, "form"]],
        });
    }
}

export class ConversationView extends Component {
    static template = "pan_mail_pro.ConversationView";
    static components = { RecordPane };
    static props = ["*"];
    // A client action's name in the breadcrumb and the browser tab is the
    // component's, not the action record's: without this, opening a record
    // from the Inbox shows "Unnamed / Onderhoudscontract 2027" up top.
    static displayName = _t("Inbox");

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.panes = usePanes();

        // Two request tokens, one per pane. Somebody who clicks three folders
        // in a second starts three reads, and without these the slowest answer
        // wins the screen -- which need not be the one they asked for last.
        this.listSeq = 0;
        this.threadSeq = 0;

        this.state = useState({
            loading: true,
            error: "",
            folders: [],
            folder: "inbox",
            mailboxes: [],
            mailboxId: null,
            conversations: [],
            limit: PAGE,
            hasMore: false,
            selected: null,
            thread: { messages: [], records: [], rejected: [], suggestion: false },
            // Which messages are open, and whose quoted history is unfolded.
            // Keyed by message id, so a thread that reloads under a reply
            // keeps nothing from the thread before it.
            open: {},
            quotes: {},
            showRejected: false,
            // The model row of the filing picker. Closed unless somebody asked
            // to file something, because on a correctly filed thread it is an
            // answer to a question nobody has.
            filing: false,
            refileTargets: [],
            search: "",
        });

        // Splitting a body into "what was written" and "what was quoted" is a
        // parse per message, and Owl re-renders this pane on every hover
        // state. Outside `state` on purpose: it is derived from a message that
        // cannot change, so it is a cache and not a fact.
        this.split = new Map();

        onWillStart(async () => {
            await this.loadMailboxes();
            await this.loadRefileTargets();
            await this.refresh();
        });
    }

    // ----------------------------------------------------------------- load

    async loadMailboxes() {
        // The notification mailbox is the one the module sends *from*, not one
        // anybody reads. Opening the inbox on it shows an empty screen to
        // somebody whose mail is one dropdown away, which reads as broken.
        this.state.mailboxes = await this.orm.searchRead(
            "pan.mail.mailbox",
            [["active", "=", true], ["is_notification_mailbox", "=", false]],
            ["email"],
            { limit: 50, order: "sequence, email" }
        );
        if (this.state.mailboxes.length) {
            this.state.mailboxId = this.state.mailboxes[0].id;
        }
    }

    async refresh({ keepSelection = false } = {}) {
        const seq = ++this.listSeq;
        this.state.loading = true;
        this.state.error = "";
        try {
            const args = {
                mailbox_id: this.state.mailboxId,
                search: this.state.search || null,
            };
            const [folders, conversations] = await Promise.all([
                this.orm.call("pan.mail.conversation", "folder_counts", [], args),
                this.orm.call("pan.mail.conversation", "search_conversations", [], {
                    ...args,
                    folder: this.state.folder,
                    limit: this.state.limit,
                }),
            ]);
            if (seq !== this.listSeq) {
                return; // A newer request is already on its way.
            }
            this.state.folders = folders;
            this.state.conversations = conversations;
            this.state.hasMore = conversations.length >= this.state.limit;

            const stillThere = keepSelection && this.state.selected
                && conversations.some((row) => this.sameConversation(row, this.state.selected));
            if (!stillThere) {
                if (conversations.length) {
                    await this.select(conversations[0]);
                } else {
                    this.state.selected = null;
                    this.state.thread = {
                        messages: [], records: [], rejected: [], suggestion: false,
                    };
                }
            }
        } catch (error) {
            // Keep what the reader was looking at; say one line and offer a
            // retry rather than clearing the pane.
            if (seq === this.listSeq) {
                this.state.error = _t("Could not load your conversations.");
            }
            console.warn("[Mail Pro] conversation list failed", error);
        } finally {
            if (seq === this.listSeq) {
                this.state.loading = false;
            }
        }
    }

    sameConversation(left, right) {
        return left.model === right.model
            && left.res_id === right.res_id
            && left.message_id === right.message_id;
    }

    async select(conversation) {
        const seq = ++this.threadSeq;
        this.state.selected = conversation;
        this.state.showRejected = false;
        this.state.filing = false;
        // Nothing from the previous thread stays under the new subject.
        this.state.thread = { messages: [], records: [], rejected: [], suggestion: false };
        this.state.open = {};
        this.state.quotes = {};
        this.split.clear();
        try {
            const thread = await this.orm.call(
                "pan.mail.conversation", "read_conversation", [], {
                    model: conversation.model,
                    res_id: conversation.res_id,
                    message_id: conversation.message_id,
                    mailbox_id: this.state.mailboxId,
                }
            );
            if (seq === this.threadSeq) {
                this.state.thread = thread;
                // The newest message is the one you came for. The rest of the
                // thread is context, one line each, a click away.
                const newest = thread.messages[thread.messages.length - 1];
                if (newest) {
                    this.state.open[newest.id] = true;
                }
            }
        } catch (error) {
            if (seq === this.threadSeq) {
                this.state.error = _t("Could not open that conversation.");
            }
            console.warn("[Mail Pro] conversation failed to open", error);
        }
    }

    async setFolder(folder) {
        this.state.folder = folder;
        this.state.limit = PAGE;
        await this.refresh();
    }

    /** Open another mailbox, from the rail. Folders are per mailbox. */
    async setMailbox(mailboxId) {
        if (mailboxId === this.state.mailboxId) {
            return;
        }
        this.state.mailboxId = mailboxId;
        // The folder you were in carries over. It is the same five states in
        // every mailbox, and landing back in Inbox on every switch loses the
        // one thing somebody switching mailboxes is usually doing: working
        // one folder across all of them.
        this.state.limit = PAGE;
        await this.refresh();
    }

    async onSearch(event) {
        if (event.key !== "Enter") {
            return;
        }
        this.state.search = event.target.value;
        this.state.limit = PAGE;
        await this.refresh();
    }

    async loadMore() {
        this.state.limit += PAGE;
        await this.refresh({ keepSelection: true });
    }

    // --------------------------------------------------------------- render

    get selectedRecord() {
        const chips = this.state.thread.records || [];
        return chips.length ? chips[0] : null;
    }

    // --------------------------------------------------------- the stack

    isOpen(message) {
        return !!this.state.open[message.id];
    }

    toggleMessage(message) {
        this.state.open[message.id] = !this.state.open[message.id];
    }

    toggleQuote(message) {
        this.state.quotes[message.id] = !this.state.quotes[message.id];
    }

    hasQuote(message) {
        return !!this.parts(message).quote;
    }

    visibleBody(message) {
        // `mail.message.body` is an Html field and the framework sanitizes it
        // on write; every provider body enters through `message_post`. This is
        // the same trust the chatter itself extends to that column, and the
        // split below only moves nodes -- it never adds any.
        return markup(this.parts(message).body);
    }

    quotedBody(message) {
        return markup(this.parts(message).quote);
    }

    /** The one line a collapsed message shows, taken from what was written. */
    snippet(message) {
        return this.parts(message).text.slice(0, 200);
    }

    /**
     * A mail body, split into what this person wrote and what they quoted.
     *
     * Every client marks the history it pasted under a reply, and each one
     * marks it differently; the markers below are what the four clients our
     * customers write to us from actually emit. A body with none of them is
     * all reply, which is the right answer for a first mail.
     *
     * The cut is the first marker in document order, plus everything after it
     * among its own siblings. Trailing content above that level stays visible,
     * which is the harmless way to be wrong: a stray line of signature shows,
     * rather than a reply disappearing into a fold.
     */
    parts(message) {
        const cached = this.split.get(message.id);
        if (cached) {
            return cached;
        }
        const doc = new DOMParser().parseFromString(message.body || "", "text/html");
        const marker = doc.body.querySelector(QUOTE_MARKERS);
        let quote = "";
        if (marker) {
            const folded = doc.createElement("div");
            let node = marker;
            while (node) {
                const next = node.nextSibling;
                folded.appendChild(node);
                node = next;
            }
            quote = folded.innerHTML;
            if (!(doc.body.textContent || "").trim()) {
                // A forward is all quote and nothing written. Folding it
                // leaves an empty card with a "..." under it, so a message
                // that is only history is shown as its own body.
                doc.body.innerHTML = quote;
                quote = "";
            }
        }
        const parsed = {
            body: doc.body.innerHTML,
            quote,
            text: (doc.body.textContent || "").split(/\s+/).join(" ").trim(),
        };
        this.split.set(message.id, parsed);
        return parsed;
    }

    /** The author's own picture, which is what makes a stack scannable. */
    avatar(message) {
        return `/web/image/res.partner/${message.author_id}/avatar_128`;
    }

    initials(name) {
        const words = (name || "").split(/\s+/).filter(Boolean);
        if (!words.length) {
            return "?";
        }
        const first = words[0][0];
        const last = words.length > 1 ? words[words.length - 1][0] : "";
        return (first + last).toUpperCase();
    }

    /** The date column, in the reader's own timezone and shortened by age. */
    day(value) {
        if (!value) {
            return "";
        }
        const when = deserializeDateTime(value);
        const now = when.constructor.now();
        if (when.hasSame(now, "day")) {
            return when.toFormat("HH:mm");
        }
        if (now.diff(when, "days").days < 7) {
            return when.toFormat("ccc HH:mm");
        }
        return when.toFormat("d LLL");
    }

    /** The whole stamp, for the title attribute. */
    fullDate(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "";
    }

    messageCount(count) {
        return count === 1 ? _t("1 message") : _t("%s messages", count);
    }

    folderLabel(id) {
        const folder = this.state.folders.find((entry) => entry.id === id);
        return folder ? folder.name : "";
    }

    folderCount(folder) {
        return folder.capped ? `${folder.count}+` : `${folder.count}`;
    }

    /**
     * Reply through Odoo's own composer, not one of ours.
     *
     * It already carries this module's "Send From" dropdown, the followers,
     * the templates and the attachment handling, and `message_post` is what
     * files the reply on the record and threads it. A composer of our own
     * would be a second implementation of all of that, drifting from the day
     * it shipped.
     */
    async reply() {
        const conversation = this.state.selected;
        if (!conversation || !conversation.model) {
            return;
        }
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "mail.compose.message",
                views: [[false, "form"]],
                target: "new",
                context: {
                    default_model: conversation.model,
                    // 19.0 refuses `default_res_id` by name: the composer
                    // takes a list, because it also composes in batch.
                    default_res_ids: [conversation.res_id],
                    default_composition_mode: "comment",
                    default_subtype_xmlid: "mail.mt_comment",
                    // The chatter fills "To" from the record's suggested
                    // recipients; the composer itself fills nothing, and since
                    // 18.2 the customer is no longer a follower by default. A
                    // reply with an empty "To" reaches nobody, so the person
                    // who wrote last from their side goes in.
                    default_partner_ids: this.replyRecipients(conversation),
                    // The message being answered. The composer takes its
                    // subject from it, and `message_post` threads the reply
                    // under it, so the customer's client files the answer in
                    // the same thread. Without it the subject is the record's
                    // name and the mail arrives as a new conversation.
                    default_parent_id: this.newestIncoming()?.id || false,
                },
            },
            { onClose: () => this.select(conversation) }
        );
    }

    /**
     * Who a reply goes to: the author of the newest incoming message, the
     * person rather than their company. Without one (a thread that is only
     * our own mail so far) the conversation's correspondent, and without
     * that nobody, which the composer shows as an empty "To" to fill in.
     */
    replyRecipients(conversation) {
        const newest = this.newestIncoming();
        if (newest && newest.author_id) {
            return [newest.author_id];
        }
        return conversation.partner_id ? [conversation.partner_id] : [];
    }

    /** The newest message from their side in the open thread, if any. */
    newestIncoming() {
        // Newest by date, and by id when two share a second: an import
        // stamps a whole thread in one go, and the answer still has to go
        // under the last message and not the first.
        const incoming = (this.state.thread.messages || [])
            .filter((m) => m.direction === "incoming")
            .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id));
        return incoming[0] || null;
    }

    // ---------------------------------------------------------------- filing

    /**
     * Where mail may be filed. Read once: it is the shape of this database,
     * not of the conversation on screen, and it changes about as often as a
     * mailbox is configured.
     */
    async loadRefileTargets() {
        try {
            this.state.refileTargets = await this.orm.call(
                "pan.mail.conversation", "refile_targets", []
            );
        } catch (error) {
            // A picker nobody can open is better than an inbox that does not
            // load. Filing stays unavailable and everything else works.
            console.warn("[Mail Pro] could not read filing targets", error);
        }
    }

    /** Take the suggestion the matcher made. One click, the common case. */
    async acceptSuggestion() {
        const suggestion = this.state.thread.suggestion;
        if (suggestion) {
            await this.fileOn(suggestion.model, suggestion.res_id);
        }
    }

    /**
     * Pick a record on a model, through Odoo's own list-and-search dialog.
     * Creating from here is off: filing is about where mail belongs, and a
     * record invented to hold it is a different decision.
     */
    pickTarget(target) {
        this.state.filing = false;
        this.dialog.add(SelectCreateDialog, {
            resModel: target.model,
            title: _t("File this conversation on a %s", target.label),
            multiSelect: false,
            noCreate: true,
            onSelected: (resIds) => {
                if (resIds.length) {
                    this.fileOn(target.model, resIds[0]);
                }
            },
        });
    }

    /**
     * Move the conversation, and say what the move bought.
     *
     * The confirmation names the thread link rather than the move, because
     * that is the part somebody would not otherwise know happened: the rest
     * of this conversation now files itself.
     */
    async fileOn(model, resId) {
        const messageIds = this.state.thread.messages.map((message) => message.id);
        if (!messageIds.length) {
            return;
        }
        let filed;
        try {
            filed = await this.orm.call(
                "pan.mail.routing.log", "refile", [messageIds, model, resId]
            );
        } catch (error) {
            this.notification.add(_t("Could not file this conversation."), { type: "danger" });
            console.warn("[Mail Pro] refile failed", error);
            return;
        }
        this.notification.add(
            _t("Filed on %s. The next mail in this thread lands here too.", filed.name),
            { type: "success" }
        );
        // The conversation is somewhere else now, so it is addressed by the
        // record it moved to. `keepSelection` then does the right thing in
        // both folders it can be filed from: in the inbox the row is still
        // there under its new record and the reader keeps their place, and in
        // an unfiled folder it is gone, so the screen moves on to the next
        // one waiting -- which is what working a queue means.
        this.state.selected = {
            ...this.state.selected, model: filed.model, res_id: filed.res_id,
        };
        this.state.filing = false;
        await this.refresh({ keepSelection: true });
        if (this.state.selected && this.state.selected.model === filed.model
            && this.state.selected.res_id === filed.res_id) {
            // Still on it: re-read the thread so the chips replace the
            // suggestion instead of the screen still offering it.
            await this.select(this.state.selected);
        }
    }

    openRecordChip(chip) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: chip.model,
            res_id: chip.res_id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("pan_mail_conversation_view", ConversationView);
