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
 * keeps working. It shows the record and never its chatter: this screen writes
 * in one pane, and what the chatter carried is the strip of four tabs over the
 * thread -- Mail, Everything, Files, Activities.
 *
 * The panes themselves are draggable and the two outer ones fold away; that
 * lives in `use_panes.js`, because how wide a pane is has nothing to do with
 * what is in it. Replying takes the conversation pane rather than a dialog
 * over the screen; that lives in `use_composer.js`.
 */

import { Component, useState, useSubEnv, onWillStart, onError, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { usePanes } from "./use_panes";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { useComposer, ComposerForm } from "./use_composer";

const PAGE = 30;

// What a pane with nothing selected holds. A function rather than a constant:
// four lists shared between two selections is one stale thread away from a
// reply landing under the wrong subject.
const EMPTY_THREAD = () => ({
    messages: [], records: [], rejected: [], files: [], activities: [], suggestion: false,
});

// Which mailboxes stand open in the rail. In the browser, next to the pane
// widths: it is the same kind of preference, per person and per monitor, and
// a table for it would have to be read on every open.
const RAIL_KEY = "pan_mail_pro.rail";

// Which of the four readings of a conversation this person left open. Theirs
// rather than the conversation's: somebody clearing an inbox stays in Mail,
// somebody catching up on a deal stays in Everything.
const TAB_KEY = "pan_mail_pro.tab";
const TAB_IDS = ["mail", "all", "files", "activities"];

/** The tab this person last read in, or Mail. */
function restoreTab() {
    try {
        const stored = browser.localStorage.getItem(TAB_KEY);
        return TAB_IDS.includes(stored) ? stored : "mail";
    } catch {
        return "mail";
    }
}

/** Stored state is somebody else's data by the time we read it back. */
function restoreExpanded() {
    try {
        const stored = JSON.parse(browser.localStorage.getItem(RAIL_KEY) || "null");
        return Array.isArray(stored) ? stored.filter(Number.isFinite) : [];
    } catch {
        return []; // Private window, cleared storage, a half-written value.
    }
}

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
    static props = {
        record: { type: Object, optional: true },
        zoomed: { type: Boolean, optional: true },
        zoomLabel: { type: String, optional: true },
        onToggleZoom: { type: Function, optional: true },
    };

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
    static components = { RecordPane, ComposerForm };
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
        this.composer = useComposer({ onSent: () => this.onReplySent() });

        // Two request tokens, one per pane. Somebody who clicks three folders
        // in a second starts three reads, and without these the slowest answer
        // wins the screen -- which need not be the one they asked for last.
        this.listSeq = 0;
        this.threadSeq = 0;

        this.state = useState({
            loading: true,
            error: "",
            folder: "inbox",
            // Two dimensions, two controls: the rail says where you are, the
            // filter row says what you are looking for in there. Naming our
            // own states as folders made the rail read like a filter panel
            // next to the mail client everybody also has open.
            filter: null,
            mailboxes: [],
            mailboxId: null,
            // The rail, the way Outlook draws it: every mailbox can stand
            // open or folded, and folding one does not close the mail you
            // are reading. `counts` is keyed by mailbox id (0 when there is
            // no mailbox yet), so a folded mailbox costs no query at all.
            expanded: {},
            counts: {},
            conversations: [],
            limit: PAGE,
            hasMore: false,
            selected: null,
            tab: restoreTab(),
            thread: EMPTY_THREAD(),
            // Which messages are open, and whose quoted history is unfolded.
            // Keyed by message id, so a thread that reloads under a reply
            // keeps nothing from the thread before it.
            open: {},
            quotes: {},
            showRejected: false,
            // The model row of the link picker. Closed unless somebody asked
            // to link something, because on a correctly linked thread it is
            // an answer to a question nobody has.
            linking: false,
            linkTargets: [],
            search: "",
        });

        // Splitting a body into "what was written" and "what was quoted" is a
        // parse per message, and Owl re-renders this pane on every hover
        // state. Outside `state` on purpose: it is derived from a message that
        // cannot change, so it is a cache and not a fact.
        this.split = new Map();

        onWillStart(async () => {
            await this.loadMailboxes();
            await this.loadLinkTargets();
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
        // What stood open last time, minus the mailboxes that are gone. The
        // one you land in is always open: a rail that opens fully folded
        // hides the folder you are looking at.
        const known = new Set(this.state.mailboxes.map((mailbox) => mailbox.id));
        for (const id of restoreExpanded()) {
            if (known.has(id)) {
                this.state.expanded[id] = true;
            }
        }
        this.state.expanded[this.railKey()] = true;
    }

    /** The key a mailbox's folders are stored under; 0 is "no mailbox". */
    railKey(mailboxId) {
        return (mailboxId === undefined ? this.state.mailboxId : mailboxId) || 0;
    }

    /** The mailboxes whose folders are on screen, so whose counts we need. */
    expandedKeys() {
        const keys = this.state.mailboxes
            .map((mailbox) => mailbox.id)
            .filter((id) => this.state.expanded[id]);
        // Without a mailbox the rail still shows the reader's own folders,
        // and the open mailbox is counted even when its folders are folded:
        // the empty state names the folder you are in.
        const active = this.railKey();
        return keys.includes(active) ? keys : [...keys, active];
    }

    saveExpanded() {
        try {
            browser.localStorage.setItem(
                RAIL_KEY,
                JSON.stringify(Object.keys(this.state.expanded)
                    .filter((id) => this.state.expanded[id])
                    .map(Number)));
        } catch {
            // A rail nobody can store is still a rail you can fold today.
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
            // One count query per mailbox that is standing open. A folded
            // mailbox is not counted, which is what keeps a rail of six
            // accounts from costing six times the queries of one.
            const keys = this.expandedKeys();
            const [counts, conversations] = await Promise.all([
                Promise.all(keys.map((key) => this.orm.call(
                    "pan.mail.conversation", "folder_counts", [], {
                        ...args,
                        mailbox_id: key || null,
                        // The filter row belongs to the list, so it is
                        // counted for the mailbox the list is showing and
                        // nowhere else.
                        folder: key === this.railKey() ? this.state.folder : null,
                    }))),
                this.orm.call("pan.mail.conversation", "search_conversations", [], {
                    ...args,
                    folder: this.state.folder,
                    filter_name: this.state.filter,
                    limit: this.state.limit,
                }),
            ]);
            if (seq !== this.listSeq) {
                return; // A newer request is already on its way.
            }
            this.state.counts = Object.fromEntries(
                keys.map((key, index) => [key, counts[index]]));
            this.state.conversations = conversations;
            this.state.hasMore = conversations.length >= this.state.limit;

            const stillThere = keepSelection && this.state.selected
                && conversations.some((row) => this.sameConversation(row, this.state.selected));
            if (!stillThere) {
                if (conversations.length) {
                    await this.select(conversations[0]);
                } else {
                    this.state.selected = null;
                    this.state.thread = EMPTY_THREAD();
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
        // A reply belongs to the conversation it answers, and this is another
        // one. The draft goes with it: nothing was stored yet, and a composer
        // left open over the wrong thread is worse than retyping two lines.
        this.composer.close();
        this.state.selected = conversation;
        this.state.showRejected = false;
        this.state.linking = false;
        // Nothing from the previous thread stays under the new subject.
        this.state.thread = EMPTY_THREAD();
        this.state.open = {};
        this.state.quotes = {};
        this.split.clear();
        await this.readThread();
    }

    /**
     * Read the open conversation, leaving on screen whatever is there.
     *
     * `select()` empties the pane before calling this, because another
     * conversation is coming. Everything else -- switching Mail to
     * Everything, a reply that just went out -- is the *same* conversation
     * read again, and blanking it there is what made the pane flicker:
     * the header collapsed, the messages vanished, and the reader lost
     * which ones they had open. Here the old thread stays up until the new
     * one arrives, and what was open stays open.
     */
    async readThread({ openNewest = false } = {}) {
        const conversation = this.state.selected;
        if (!conversation) {
            return;
        }
        const seq = ++this.threadSeq;
        try {
            const thread = await this.orm.call(
                "pan.mail.conversation", "read_conversation", [], {
                    model: conversation.model,
                    res_id: conversation.res_id,
                    message_id: conversation.message_id,
                    mailbox_id: this.state.mailboxId,
                    // Files and Activities are two other lists over the same
                    // conversation, so they read the mail thread underneath.
                    scope: this.state.tab === "all" ? "all" : "mail",
                }
            );
            if (seq !== this.threadSeq) {
                return;
            }
            this.state.thread = thread;
            // Keep the reader's place: a message that was open before this
            // read is still open after it, and one that is gone from this
            // reading takes its entry with it.
            const open = {};
            for (const message of thread.messages) {
                if (this.state.open[message.id]) {
                    open[message.id] = true;
                }
            }
            const newest = thread.messages[thread.messages.length - 1];
            // The newest message is the one you came for. The rest of the
            // thread is context, one line each, a click away.
            if (newest && (openNewest || !Object.keys(open).length)) {
                open[newest.id] = true;
            }
            this.state.open = open;
        } catch (error) {
            if (seq === this.threadSeq) {
                this.state.error = _t("Could not open that conversation.");
            }
            console.warn("[Mail Pro] conversation failed to open", error);
        }
    }

    /**
     * Open a folder. A folder belongs to the mailbox it sits under, so
     * clicking one in a mailbox you are not in switches mailbox and folder
     * in a single read rather than two.
     */
    async setFolder(folder, mailboxId) {
        if (mailboxId !== undefined && mailboxId !== this.state.mailboxId) {
            this.state.mailboxId = mailboxId;
        // Opening a mailbox unfolds it: the folders are where you go next.
        this.state.expanded[this.railKey()] = true;
        this.saveExpanded();
        }
        this.state.folder = folder;
        // A filter is a question about the folder you are in, so switching
        // folder keeps it: "needs reply" in Sent is a fair question, and
        // dropping it on every click is the thing that makes a filter row
        // feel like it undoes itself.
        this.state.limit = PAGE;
        await this.refresh();
    }

    /** Narrow the folder you are in, or clear the filter with a second click. */
    async setFilter(filter) {
        this.state.filter = this.state.filter === filter ? null : filter;
        this.state.limit = PAGE;
        await this.refresh();
    }

    /** Fold a mailbox away, or open it, without leaving the one you are in. */
    async toggleMailbox(mailboxId) {
        const key = this.railKey(mailboxId);
        this.state.expanded[key] = !this.state.expanded[key];
        this.saveExpanded();
        if (this.state.expanded[key] && !this.state.counts[key]) {
            await this.loadCounts(key);
        }
    }

    isExpanded(mailboxId) {
        return !!this.state.expanded[this.railKey(mailboxId)];
    }

    /** The folder counts of one mailbox, loaded when it is unfolded. */
    async loadCounts(key) {
        try {
            this.state.counts[key] = await this.orm.call(
                "pan.mail.conversation", "folder_counts", [], {
                    mailbox_id: key || null,
                    search: this.state.search || null,
                });
        } catch (error) {
            // A rail that cannot count is a rail without numbers, not an
            // error banner over the mail somebody is reading.
            console.warn("[Mail Pro] folder counts failed", error);
            this.state.counts[key] = [];
        }
    }

    foldersFor(mailboxId) {
        return (this.state.counts[this.railKey(mailboxId)] || {}).folders || [];
    }

    /** The filter row over the list, counted inside the open folder. */
    get filters() {
        return (this.state.counts[this.railKey()] || {}).filters || [];
    }

    /** Open another mailbox, from the rail. Folders are per mailbox. */
    async setMailbox(mailboxId) {
        if (mailboxId === this.state.mailboxId) {
            return;
        }
        this.state.mailboxId = mailboxId;
        // Opening a mailbox unfolds it: the folders are where you go next.
        this.state.expanded[this.railKey()] = true;
        this.saveExpanded();
        // The folder and the filter carry over. Every mailbox has the same
        // two folders, and landing back in Inbox on every switch loses the
        // one thing somebody switching mailboxes is usually doing: working
        // one view across all of them.
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

    // ----------------------------------------------------------- the tabs

    /**
     * The strip itself. One control with four positions rather than a toggle
     * plus a tab bar: two pieces of chrome over one pane is chrome competing
     * with content.
     */
    get TABS() {
        return [
            { id: "mail", label: _t("Mail") },
            { id: "all", label: _t("Everything") },
            { id: "files", label: _t("Files") },
            { id: "activities", label: _t("Activities") },
        ];
    }

    /**
     * The four readings of one conversation.
     *
     * Mail and Everything are the same list read twice, so switching between
     * them re-reads the thread. Files and Activities came down with it, so
     * they cost nothing to open and their counts do not move when you do.
     */
    async setTab(tab) {
        if (tab === this.state.tab) {
            return;
        }
        const reread = (tab === "all") !== (this.state.tab === "all");
        this.state.tab = tab;
        try {
            browser.localStorage.setItem(TAB_KEY, tab);
        } catch {
            // A tab nobody can store is still a tab you can open today.
        }
        if (reread && this.state.selected) {
            // In place: the same conversation read another way is not a
            // reason to empty the pane and draw it again.
            await this.readThread();
        }
    }

    /**
     * Which of the two writing actions this tab carries.
     *
     * One per tab, and each in the tab that shows what it produces: a reply
     * is correspondence and lands in Mail, a note is not and lands in
     * Everything. Both on every tab meant a note written in Mail vanished on
     * save and a reply sent from Files landed on a screen that shows no mail
     * at all.
     */
    get canReply() {
        return this.state.tab === "mail" && !!this.state.selected?.model;
    }

    get canLogNote() {
        return this.state.tab === "all" && !!this.state.selected?.model;
    }

    /** The number beside a tab, drawn only when there is one. */
    tabCount(tab) {
        if (tab === "files") {
            return (this.state.thread.files || []).length;
        }
        if (tab === "activities") {
            return (this.state.thread.activities || []).length;
        }
        return 0;
    }

    /** What the reader downloads. Odoo's own attachment route, unchanged. */
    fileUrl(file) {
        return `/web/content/${file.id}?download=true`;
    }

    /** A size somebody can read, which is not a number of bytes. */
    fileSize(bytes) {
        const size = Number(bytes) || 0;
        if (size < 1024) {
            return `${size} B`;
        }
        if (size < 1024 * 1024) {
            return `${Math.round(size / 1024)} kB`;
        }
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    openActivityRecord(activity) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: activity.model,
            res_id: activity.res_id,
            views: [[false, "form"]],
        });
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

    /** The same picture for the list, from the contact the thread is with. */
    partnerAvatar(conversation) {
        return `/web/image/res.partner/${conversation.partner_id}/avatar_128`;
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

    /** What the list is showing, in words: the folder, narrowed by the filter. */
    get listLabel() {
        const named = (entries, id) => (entries.find((e) => e.id === id) || {}).name;
        const folder = named(this.foldersFor(), this.state.folder) || "";
        const filter = this.state.filter && named(this.filters, this.state.filter);
        return filter ? `${folder} / ${filter}` : folder;
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
     * it shipped. It opens in the conversation pane -- see `use_composer.js`
     * for what that costs and what it buys.
     */
    reply() {
        const conversation = this.state.selected;
        if (!this.canReply) {
            return;
        }
        this.composer.open({
            default_model: conversation.model,
            // 19.0 refuses `default_res_id` by name: the composer takes a
            // list, because it also composes in batch.
            default_res_ids: [conversation.res_id],
            default_composition_mode: "comment",
            default_subtype_xmlid: "mail.mt_comment",
            // The chatter fills "To" from the record's suggested recipients;
            // the composer itself fills nothing, and since 18.2 the customer
            // is no longer a follower by default. A reply with an empty "To"
            // reaches nobody, so the person who wrote last from their side
            // goes in.
            default_partner_ids: this.replyRecipients(conversation),
            // The message being answered. The composer takes its subject from
            // it, and `message_post` threads the reply under it, so the
            // customer's client files the answer in the same thread. Without
            // it the subject is the record's name and the mail arrives as a
            // new conversation.
            default_parent_id: this.newestIncoming()?.id || false,
        });
    }

    /**
     * It went out: show it in the thread, and recount the folders.
     *
     * Each of the two is written from the tab that shows it -- a reply in
     * Mail, a note in Everything -- so what was just written is on screen
     * without leaving the tab, and it is the message that opens.
     */
    async onReplySent() {
        await this.readThread({ openNewest: true });
        await this.refresh({ keepSelection: true });
    }

    /**
     * An internal note, in the same composer and the same pane as the reply.
     *
     * The record pane no longer carries a chatter, so this is where a note
     * gets written. No recipients and no parent: a note reaches the record's
     * followers through Odoo's own note subtype and threads nothing outwards.
     * A recipient row on a note is what makes people believe a note is a
     * mail, and the subtype is the whole difference between the two.
     */
    logNote() {
        const conversation = this.state.selected;
        if (!this.canLogNote) {
            return;
        }
        this.composer.open({
            default_model: conversation.model,
            default_res_ids: [conversation.res_id],
            default_composition_mode: "comment",
            default_subtype_xmlid: "mail.mt_note",
        }, "note");
    }

    /**
     * A follow-up with a date on it is a `mail.activity` on the record.
     *
     * Odoo's own scheduler, so the activity lands in the Activities clock the
     * rest of the database reads. This module has never kept a queue of its
     * own and this is not the place to start one.
     */
    async scheduleActivity() {
        const record = this.selectedRecord;
        if (!record) {
            return;
        }
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "mail.activity.schedule",
                views: [[false, "form"]],
                target: "new",
                context: {
                    active_model: record.model,
                    active_ids: [record.res_id],
                    default_res_model: record.model,
                    default_res_ids: [record.res_id],
                },
            },
            { onClose: () => this.select(this.state.selected) }
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

    // --------------------------------------------------------------- linking

    /**
     * What mail may be linked to. Read once: it is the shape of this
     * database, not of the conversation on screen, and it changes about as
     * often as a mailbox is configured.
     */
    async loadLinkTargets() {
        try {
            this.state.linkTargets = await this.orm.call(
                "pan.mail.conversation", "link_targets", []
            );
        } catch (error) {
            // A picker nobody can open is better than an inbox that does not
            // load. Linking stays unavailable and everything else works.
            console.warn("[Mail Pro] could not read link targets", error);
        }
    }

    /** Take the suggestion the matcher made. One click, the common case. */
    async acceptSuggestion() {
        const suggestion = this.state.thread.suggestion;
        if (suggestion) {
            await this.linkTo(suggestion.model, suggestion.res_id);
        }
    }

    /**
     * Pick a record on a model, through Odoo's own list-and-search dialog.
     * Creating from here is off: linking is about where mail belongs, and a
     * record invented to hold it is a different decision.
     */
    pickTarget(target) {
        this.state.linking = false;
        this.dialog.add(SelectCreateDialog, {
            resModel: target.model,
            title: _t("Link this conversation to a %s", target.label),
            multiSelect: false,
            noCreate: true,
            onSelected: (resIds) => {
                if (resIds.length) {
                    this.linkTo(target.model, resIds[0]);
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
    async linkTo(model, resId) {
        const messageIds = this.state.thread.messages.map((message) => message.id);
        if (!messageIds.length) {
            return;
        }
        let linked;
        try {
            linked = await this.orm.call(
                "pan.mail.routing.log", "link_to", [messageIds, model, resId]
            );
        } catch (error) {
            this.notification.add(_t("Could not link this conversation."), { type: "danger" });
            console.warn("[Mail Pro] linking failed", error);
            return;
        }
        this.notification.add(
            _t("Linked to %s. The next mail in this thread lands here too.", linked.name),
            { type: "success" }
        );
        // The conversation is somewhere else now, so it is addressed by the
        // record it moved to. `keepSelection` then does the right thing in
        // both folders it can be linked from: in the inbox the row is still
        // there under its new record and the reader keeps their place, and in
        // an unlinked folder it is gone, so the screen moves on to the next
        // one waiting -- which is what working a queue means.
        this.state.selected = {
            ...this.state.selected, model: linked.model, res_id: linked.res_id,
        };
        this.state.linking = false;
        await this.refresh({ keepSelection: true });
        if (this.state.selected && this.state.selected.model === linked.model
            && this.state.selected.res_id === linked.res_id) {
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
