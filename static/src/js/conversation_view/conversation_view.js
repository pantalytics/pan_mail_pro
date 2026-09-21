/** @odoo-module */
/**
 * The conversation view: folders, conversations, the conversation, and the record.
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
 * conversation -- Mail, Mail + notes, Files, Activities.
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
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { useDebounced } from "@web/core/utils/timing";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { usePanes } from "./use_panes";
import { useImprove } from "../improve";
import { LinkDialog } from "./link_dialog";
import { AttachmentList } from "@mail/core/common/attachment_list";
import { useAttachmentUploader } from "@mail/core/common/attachment_uploader_hook";
import { FileUploader } from "@web/views/fields/file_handler";
import { useComposer, ComposerForm } from "./use_composer";
// The Activities tab draws Odoo's own activity card. Borrowing the component
// rather than restyling ours is what keeps the icons, the three state colours
// and Mark Done / Edit / Cancel identical to the chatter, for free and
// forever: a change Odoo makes to it arrives here with the upgrade.
import { Activity } from "@mail/core/web/activity";
import { FollowerList } from "@mail/core/web/follower_list";
import { compareDatetime } from "@mail/utils/common/misc";

const PAGE = 30;

// How long the search waits after the last keystroke. Long enough that typing
// a name is one query instead of eight, short enough that it still reads as
// the list following along.
const SEARCH_DELAY = 400;

// What a pane with nothing selected holds. A function rather than a constant:
// four lists shared between two selections is one stale thread away from a
// reply landing under the wrong subject.
const EMPTY_CONVERSATION = () => ({
    messages: [], records: [], rejected: [], activities: [], suggestion: false,
    // The attachments as the mail store holds them: the ids in order, the
    // records themselves in `store`. See `files` below.
    files: { ids: [], store: {} },
});

// Which mailboxes stand open in the mailbox list. In the browser, next to the pane
// widths: it is the same kind of preference, per person and per monitor, and
// a table for it would have to be read on every open.
const MAILBOX_LIST_KEY = "pan_mail_pro.mailbox_list";

// Which of the four readings of a conversation this person left open. Theirs
// rather than the conversation's: somebody clearing an inbox stays in Mail,
// somebody catching up on a deal stays in Mail + notes.
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
        const stored = JSON.parse(browser.localStorage.getItem(MAILBOX_LIST_KEY) || "null");
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
export class OdooRecordPane extends Component {
    static template = "pan_mail_pro.OdooRecordPane";
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
    static components = {
        OdooRecordPane, ComposerForm, Activity, AttachmentList, FileUploader,
        Dropdown, CheckboxItem, FollowerList,
    };
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
        // Odoo's activity card reads its activity out of the mail store, not
        // out of a dict we hand it, and the Files tab is Odoo's own attachment
        // list over the same store: preview, download, delete, and an upload
        // that lands on the record rather than in a copy of it.
        this.mailStore = useService("mail.store");
        this.attachmentUploader = useAttachmentUploader();
        // The follower list is Odoo's own, in the dropdown the chatter's
        // people icon opens: Follow, Unfollow, Add Followers, the subtype
        // edit. The Inbox adds the button and nothing else.
        this.followerListDropdown = useDropdownState();
        this.panes = usePanes();
        this.composer = useComposer({ onSent: () => this.onReplySent() });
        // Help improve Mail Pro: a no-op unless the session says otherwise.
        // Every `capture` below names a screen or a button, never content.
        this.improve = useImprove();
        // Typing is the search, the way it is in every mail client. Debounced
        // rather than bound to Enter: a list that only moves when you press a
        // key you were not told about reads as a search box that is broken.
        this.applySearch = useDebounced(() => this.runSearch(), SEARCH_DELAY);

        // Two request tokens, one per pane. Somebody who clicks three folders
        // in a second starts three reads, and without these the slowest answer
        // wins the screen -- which need not be the one they asked for last.
        this.listSeq = 0;
        this.conversationSeq = 0;

        this.state = useState({
            loading: true,
            error: "",
            folder: "inbox",
            // Two dimensions, two controls: the mailbox list says where you are, the
            // filter row says what you are looking for in there. Naming our
            // own states as folders made the mailbox list read like a filter panel
            // next to the mail client everybody also has open.
            filter: null,
            mailboxes: [],
            mailboxId: null,
            // The mailbox list, the way Outlook draws it: every mailbox can stand
            // open or folded, and folding one does not close the mail you
            // are reading. `counts` is keyed by mailbox id (0 when there is
            // no mailbox yet), so a folded mailbox costs no query at all.
            expanded: {},
            counts: {},
            conversations: [],
            limit: PAGE,
            hasMore: false,
            selected: null,
            // What a new mail is being written on, while one is: the record
            // picked in the dialog, by model, id and name. The composer reads
            // its target from its own context; this is for the head.
            compose: null,
            tab: restoreTab(),
            conversation: EMPTY_CONVERSATION(),
            // The ids whose activity cards are in the mail store. A card
            // lives in the store rather than in this state, so this list is
            // both what the tab draws and what tells Owl the second read
            // arrived.
            activityIds: [],
            // Which messages are open, whose quoted history is unfolded, and
            // whose header shows the full From / To / Cc / Date block. Keyed
            // by message id, so a thread that reloads under a reply keeps
            // nothing from the conversation before it.
            open: {},
            quotes: {},
            details: {},
            showRejected: false,
            search: "",
        });

        // Splitting a body into "what was written" and "what was quoted" is a
        // parse per message, and Owl re-renders this pane on every hover
        // state. Outside `state` on purpose: it is derived from a message that
        // cannot change, so it is a cache and not a fact.
        this.split = new Map();

        onWillStart(async () => {
            await this.loadMailboxes();
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
        // one you land in is always open: a mailbox list that opens fully folded
        // hides the folder you are looking at.
        const known = new Set(this.state.mailboxes.map((mailbox) => mailbox.id));
        for (const id of restoreExpanded()) {
            if (known.has(id)) {
                this.state.expanded[id] = true;
            }
        }
        this.state.expanded[this.mailboxKey()] = true;
    }

    /** The key a mailbox's folders are stored under; 0 is "no mailbox". */
    mailboxKey(mailboxId) {
        return (mailboxId === undefined ? this.state.mailboxId : mailboxId) || 0;
    }

    /** The mailboxes whose folders are on screen, so whose counts we need. */
    expandedKeys() {
        const keys = this.state.mailboxes
            .map((mailbox) => mailbox.id)
            .filter((id) => this.state.expanded[id]);
        // Without a mailbox the mailbox list still shows the reader's own folders,
        // and the open mailbox is counted even when its folders are folded:
        // the empty state names the folder you are in.
        const active = this.mailboxKey();
        return keys.includes(active) ? keys : [...keys, active];
    }

    saveExpanded() {
        try {
            browser.localStorage.setItem(
                MAILBOX_LIST_KEY,
                JSON.stringify(Object.keys(this.state.expanded)
                    .filter((id) => this.state.expanded[id])
                    .map(Number)));
        } catch {
            // A mailbox list nobody can store is still a mailbox list you can fold today.
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
            // mailbox is not counted, which is what keeps a mailbox list of six
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
                        folder: key === this.mailboxKey() ? this.state.folder : null,
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
                if (conversations.length && !this.panes.state.small) {
                    // A phone lands on the list, the way every mail client
                    // does: opening the first mail unasked is a screen the
                    // reader has to back out of before they have read it.
                    await this.select(conversations[0]);
                } else {
                    this.state.selected = null;
                    this.state.conversation = EMPTY_CONVERSATION();
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

    /** A conversation picked from the list: on a phone, that is also a step. */
    async pick(conversation) {
        this.panes.showConversation();
        await this.select(conversation);
    }

    /** The step back, on a phone. Nothing is deselected: the list marks it. */
    backToList() {
        this.composer.close();
        this.state.compose = null;
        this.panes.showConversationList();
    }

    async select(conversation) {
        // A reply belongs to the conversation it answers, and this is another
        // one. The draft goes with it: nothing was stored yet, and a composer
        // left open over the wrong conversation is worse than retyping two lines.
        this.composer.close();
        this.state.selected = conversation;
        this.state.showRejected = false;
        this.improve.capture("conversation_opened", { folder: this.state.folder });
        // Nothing from the previous conversation stays under the new subject.
        this.state.conversation = EMPTY_CONVERSATION();
        this.state.activityIds = [];
        this.state.open = {};
        this.state.quotes = {};
        this.state.details = {};
        this.split.clear();
        await this.readConversation();
    }

    /**
     * Read the open conversation, leaving on screen whatever is there.
     *
     * `select()` empties the pane before calling this, because another
     * conversation is coming. Everything else -- switching Mail to
     * Mail + notes, a reply that just went out -- is the *same* conversation
     * read again, and blanking it there is what made the pane flicker:
     * the header collapsed, the messages vanished, and the reader lost
     * which ones they had open. Here the old conversation stays up until the new
     * one arrives, and what was open stays open.
     */
    async readConversation({ openNewest = false } = {}) {
        const conversation = this.state.selected;
        if (!conversation) {
            return;
        }
        const seq = ++this.conversationSeq;
        try {
            const data = await this.orm.call(
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
            if (seq !== this.conversationSeq) {
                return;
            }
            // The attachments go into the mail store, which is where the
            // rest of the client reads them from, and this screen keeps
            // their ids.
            this.mailStore.insert(data.files?.store || {});
            this.state.conversation = data;
            // Keep the reader's place: a message that was open before this
            // read is still open after it, and one that is gone from this
            // reading takes its entry with it.
            const open = {};
            for (const message of data.messages) {
                if (this.state.open[message.id]) {
                    open[message.id] = true;
                }
            }
            const newest = data.messages[0];
            // The newest message is the one you came for. The rest of the
            // thread is context, one line each, a click away.
            if (newest && (openNewest || !Object.keys(open).length)) {
                open[newest.id] = true;
            }
            this.state.open = open;
            this.loadActivities(seq);
            this.loadFollowers();
        } catch (error) {
            if (seq === this.conversationSeq) {
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
        this.state.expanded[this.mailboxKey()] = true;
        this.saveExpanded();
        }
        this.state.folder = folder;
        this.panes.closeMailboxList();
        // A filter is a question about the folder you are in, so switching
        // folder keeps it: "linked to nothing" in Sent is a fair question,
        // and dropping it on every click is the thing that makes a filter
        // row feel like it undoes itself.
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
        const key = this.mailboxKey(mailboxId);
        this.state.expanded[key] = !this.state.expanded[key];
        this.saveExpanded();
        if (this.state.expanded[key] && !this.state.counts[key]) {
            await this.loadCounts(key);
        }
    }

    isExpanded(mailboxId) {
        return !!this.state.expanded[this.mailboxKey(mailboxId)];
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
            // A mailbox list that cannot count is a mailbox list without numbers, not an
            // error banner over the mail somebody is reading.
            console.warn("[Mail Pro] folder counts failed", error);
            this.state.counts[key] = [];
        }
    }

    foldersFor(mailboxId) {
        return (this.state.counts[this.mailboxKey(mailboxId)] || {}).folders || [];
    }

    /** The filter menu over the list, counted inside the open folder. */
    get filters() {
        return (this.state.counts[this.mailboxKey()] || {}).filters || [];
    }

    /** The one in use, named on the button so a closed menu still says so. */
    get activeFilter() {
        return this.filters.find((pill) => pill.id === this.state.filter) || null;
    }

    /** Open another mailbox, from the mailbox list. Folders are per mailbox. */
    async setMailbox(mailboxId) {
        this.panes.closeMailboxList();
        if (mailboxId === this.state.mailboxId) {
            return;
        }
        this.state.mailboxId = mailboxId;
        // Opening a mailbox unfolds it: the folders are where you go next.
        this.state.expanded[this.mailboxKey()] = true;
        this.saveExpanded();
        // The folder and the filter carry over. Every mailbox has the same
        // two folders, and landing back in Inbox on every switch loses the
        // one thing somebody switching mailboxes is usually doing: working
        // one view across all of them.
        this.state.limit = PAGE;
        await this.refresh();
    }

    onSearchInput(event) {
        this.state.search = event.target.value;
        this.applySearch();
    }

    /** Enter does not wait, and Escape gives the whole folder back. */
    onSearchKey(event) {
        if (event.key === "Enter") {
            this.applySearch.cancel();
            this.runSearch();
        } else if (event.key === "Escape" && this.state.search) {
            event.target.value = "";
            this.state.search = "";
            this.applySearch.cancel();
            this.runSearch();
        }
    }

    async runSearch() {
        this.state.limit = PAGE;
        await this.refresh();
    }

    async loadMore() {
        this.state.limit += PAGE;
        await this.refresh({ keepSelection: true });
    }

    // --------------------------------------------------------------- render

    get selectedRecord() {
        const chips = this.state.conversation.records || [];
        return chips.length ? chips[0] : null;
    }

    // Which panes are in the DOM. A folded pane stays, at no width, so the
    // fold can animate: `panes.folded(name)` says which ones are folded, and
    // these say which ones exist at all. Wide: all four. Narrow: all four,
    // the conversation and the record taking turns in the third column.
    // Small: one at a time -- the list or the conversation, the record over
    // either, and the mailbox list as a drawer over whichever is open.

    get showMailboxList() {
        return !this.panes.state.zoom;
    }

    /** On a phone, the conversation has the screen once there is one. */
    get conversationOpen() {
        return this.panes.state.stage === "conversation"
            && Boolean(this.state.selected || this.composer.state.open);
    }

    get showConversationList() {
        const panes = this.panes.state;
        return !panes.zoom && (!panes.small || !this.conversationOpen);
    }

    get showConversation() {
        const panes = this.panes.state;
        return !panes.zoom && (!panes.small || this.conversationOpen);
    }

    get showOdooRecord() {
        const panes = this.panes.state;
        return panes.zoom || !panes.small;
    }

    /**
     * The record, on a phone, where no pane for it fits: a button in the
     * conversation head that gives it the whole screen, and the screen's own
     * "Back to the Inbox" brings the conversation back. A tablet needs no
     * button: the record's divider is the strip that swaps it in.
     */
    get showOdooRecordButton() {
        const panes = this.panes.state;
        return panes.small && !panes.zoom && Boolean(this.selectedRecord);
    }

    showOdooRecordScreen() {
        if (!this.panes.state.zoom) {
            this.panes.toggleZoom();
        }
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
            { id: "all", label: _t("Mail + notes") },
            { id: "files", label: _t("Files") },
            { id: "activities", label: _t("Activities") },
        ];
    }

    /**
     * The four readings of one conversation.
     *
     * Mail and Mail + notes are the same list read twice, so switching between
     * them re-reads the conversation. Files and Activities came down with it, so
     * they cost nothing to open and their counts do not move when you do.
     */
    async setTab(tab) {
        if (tab === this.state.tab) {
            return;
        }
        const reread = (tab === "all") !== (this.state.tab === "all");
        this.state.tab = tab;
        this.improve.capture("tab_opened", { tab });
        try {
            browser.localStorage.setItem(TAB_KEY, tab);
        } catch {
            // A tab nobody can store is still a tab you can open today.
        }
        if (reread && this.state.selected) {
            // In place: the same conversation read another way is not a
            // reason to empty the pane and draw it again.
            await this.readConversation();
        }
    }

    /**
     * Which of the two writing actions this tab carries.
     *
     * One per tab, and each in the tab that shows what it produces: a reply
     * is correspondence and lands in Mail, a note is not and lands in
     * Mail + notes. Both on every tab meant a note written in Mail vanished on
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
            return this.fileIds.length;
        }
        if (tab === "activities") {
            return (this.state.conversation.activities || []).length;
        }
        return 0;
    }

    // --------------------------------------------------------- the files

    /** The order the server read them in, newest first. */
    get fileIds() {
        return this.state.conversation.files?.ids || [];
    }

    /**
     * The attachments themselves, out of the mail store.
     *
     * The store is where Odoo keeps an attachment, and holding a second copy
     * of one here is how the two drift: an upload, a delete or a rename in
     * another part of the client updates the store and nothing else. So this
     * screen keeps the ids and asks the store for the records.
     */
    get files() {
        return this.fileIds
            .map((id) => this.mailStore["ir.attachment"].get(id))
            .filter(Boolean);
    }

    /**
     * The conversation's record as the mail store knows it: the conversation the
     * chatter would have drawn. An upload lands on it, and its followers are
     * the people Odoo notifies about this record.
     */
    get recordThread() {
        const record = this.selectedRecord;
        return record
            ? this.mailStore.Thread.insert({ model: record.model, id: record.res_id })
            : null;
    }

    /** The record an upload lands on: the one the chatter would have used. */
    get uploadThread() {
        return this.recordThread;
    }

    // ------------------------------------------------------- the followers

    /**
     * Who Odoo notifies about this record, from the same request the chatter
     * makes: the count on the button, whether you are one of them, and the
     * read and write access the list needs to offer Add Followers.
     */
    loadFollowers() {
        this.recordThread?.fetchThreadData(["followers"]);
    }

    get followersLabel() {
        const thread = this.recordThread;
        return thread?.selfFollower ? _t("Following") : _t("Followers");
    }

    /** The wizard closed: whoever it added is on the list now. */
    onAddFollowers() {
        this.loadFollowers();
    }

    /**
     * Follow, Unfollow or an edited subscription. The subscribe routes answer
     * with the new follower data themselves, so a re-read here would only
     * race the answer that is already on its way; the list just closes.
     */
    onFollowerChanged() {
        this.followerListDropdown.close();
    }

    /** Delete, through Odoo's own route. The dialog is the list's own. */
    async unlinkAttachment(attachment) {
        // The id first: after the delete the record is gone from the store,
        // and asking a deleted record what it was is how a row survives its
        // own removal.
        const id = attachment.id;
        await this.attachmentUploader.unlink(attachment);
        this.state.conversation.files.ids = this.fileIds.filter((other) => other !== id);
    }

    /**
     * Attach a file to the conversation's record.
     *
     * The same upload the chatter does, so the file lands on the record and
     * is there for everybody who opens it -- not in a store of our own.
     */
    async onFileUploaded(data) {
        const thread = this.uploadThread;
        if (!thread) {
            return;
        }
        const attachment = await this.attachmentUploader.uploadData(data, { thread });
        if (attachment && !this.fileIds.includes(attachment.id)) {
            this.state.conversation.files.ids = [attachment.id, ...this.fileIds];
        }
    }

    // ------------------------------------------------------ the follow-ups

    /**
     * Put this conversation's activities in the mail store.
     *
     * `read_conversation` answers with the rows the tab count needs; Odoo's
     * activity card needs the record the chatter draws from, which is what
     * `activity_format` returns. It is the same call Odoo's own activity
     * popover makes, ACLs and all, so the ids are the only thing we add.
     */
    async loadActivities(seq) {
        const ids = (this.state.conversation.activities || []).map((row) => row.id);
        if (!ids.length) {
            return;
        }
        // The same conversation read another way carries the same follow-ups,
        // so a tab switch does not pay for this call twice.
        const known = this.state.activityIds;
        if (known.length === ids.length && ids.every((id, i) => known[i] === id)) {
            return;
        }
        const data = await this.orm.silent.call("mail.activity", "activity_format", [ids]);
        if (seq === this.conversationSeq) {
            this.mailStore.insert(data);
            this.state.activityIds = ids;
        }
    }

    /** The store records behind this conversation's activities, soonest first. */
    get activities() {
        const ids = new Set(this.state.activityIds);
        return Object.values(this.mailStore["mail.activity"].records)
            .filter((activity) => ids.has(activity.id))
            .sort((a, b) => compareDatetime(a.date_deadline, b.date_deadline) || a.id - b.id);
    }

    /**
     * Which record this follow-up sits on, and only when that is a question.
     *
     * The chatter never asks it: everything it lists belongs to the record it
     * hangs under. A conversation can have reached a lead and a contact both,
     * and then "call back" without a name on it is half an instruction.
     */
    activityRecordName(activity) {
        const rows = this.state.conversation.activities || [];
        if (new Set(rows.map((row) => `${row.model},${row.res_id}`)).size < 2) {
            return "";
        }
        return (rows.find((row) => row.id === activity.id) || {}).record_name || "";
    }

    /** An activity changed under us. Re-read the conversation, counts and all. */
    onActivityChanged() {
        if (this.state.selected) {
            this.select(this.state.selected);
        }
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

    /**
     * The header the way Outlook draws it: To and Cc on one line under the
     * sender, and the whole block -- From with its address, To, Cc, the full
     * date -- one click further. Folded by default, because "who was on
     * this" is a question asked on one mail in ten and the answer is four
     * lines tall.
     */
    showDetails(message) {
        return !!this.state.details[message.id];
    }

    toggleDetails(message) {
        this.state.details[message.id] = !this.state.details[message.id];
    }

    /** `Name <address>`, or whichever half the message has. */
    fromLine(message) {
        if (message.author && message.author_email
                && message.author !== message.author_email) {
            return `${message.author} <${message.author_email}>`;
        }
        return message.author || message.author_email || "";
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

    /** The same picture for the list, from the contact the conversation is with. */
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

    /** The folder the list is showing, for the header over it. */
    get folderLabel() {
        const folders = this.foldersFor();
        return (folders.find((e) => e.id === this.state.folder) || {}).name || "";
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
            // Send from the mailbox being read, when one is selected in the
            // mailbox list. The composer drops it again if this person may not send
            // from it and falls back to their own default.
            default_x_send_from_mailbox_id: this.state.mailboxId || false,
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
     * A new mail, on a record picked first.
     *
     * The same composer, in the same pane, as a reply: one place on this
     * screen writes mail, and a dialog over the Inbox was a second one to
     * keep in step with it. The pane has nothing to show behind a new mail,
     * so the head names the record instead of a subject, and the rest of the
     * screen stays where it was.
     *
     * The record is not optional. This module files mail on documents -- a
     * mail sent from here with no record behind it is the "Linked to nothing"
     * state the Inbox has a filter for, arriving by our own hand. So the same
     * picker linking uses asks the same two questions -- the kind of record
     * here, then the record in Odoo's own search dialog -- and the composer
     * only opens once both are answered. One way to say "where does this mail
     * belong" is one thing to learn.
     */
    newEmail() {
        this.dialog.add(LinkDialog, {
            title: _t("New email on"),
            onSelect: (model, resId, label) => this.composeOn(model, resId, label),
        });
    }

    /** The pane composer, on the record just picked. */
    async composeOn(model, resId, label) {
        // The pane is hidden while the record has the screen to itself.
        if (this.panes.state.zoom) {
            this.panes.toggleZoom();
        }
        this.panes.showConversation();
        this.state.compose = { model, res_id: resId, label: label || "" };
        // The record's own contact, the way a reply takes the last sender:
        // the composer fills "To" from nothing by itself, and a new mail that
        // opens addressed to nobody is a mail that is sent to nobody.
        const partnerIds = await this.orm.call(
            "pan.mail.conversation", "new_mail_recipients", [model, resId]
        );
        this.composer.open({
            default_model: model,
            default_res_ids: [resId],
            default_composition_mode: "comment",
            default_subtype_xmlid: "mail.mt_comment",
            // Send from the mailbox being read, when one is selected in the
            // mailbox list. The composer drops it again if this person may not send
            // from it and falls back to their own default.
            default_x_send_from_mailbox_id: this.state.mailboxId || false,
            default_partner_ids: partnerIds,
        }, "new");
    }

    /**
     * It went out: show it in the conversation, and recount the folders.
     *
     * Each of the two is written from the tab that shows it -- a reply in
     * Mail, a note in Mail + notes -- so what was just written is on screen
     * without leaving the tab, and it is the message that opens.
     */
    async onReplySent() {
        this.improve.capture("reply_sent", { mode: this.composer.state.mode });
        if (this.composer.state.mode === "new") {
            // A new mail belongs to no open thread. The list is re-read, and
            // the mail shows up there if it landed in the folder on screen.
            this.state.compose = null;
            await this.refresh({ keepSelection: true });
            return;
        }
        await this.readConversation({ openNewest: true });
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
        await this.mailStore.scheduleActivity(record.model, [record.res_id]);
        this.onActivityChanged();
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
        const incoming = (this.state.conversation.messages || [])
            .filter((m) => m.direction === "incoming")
            .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id));
        return incoming[0] || null;
    }

    // --------------------------------------------------------------- linking

    /** Take the suggestion the matcher made. One click, the common case. */
    async acceptSuggestion() {
        const suggestion = this.state.conversation.suggestion;
        if (suggestion) {
            await this.linkTo(suggestion.model, suggestion.res_id, "suggestion");
        }
    }

    /**
     * Open the picker: the kind of record here, then the record in Odoo's own
     * search dialog.
     *
     * It gets the correspondent so that dialog opens on their own records,
     * as a search facet anybody can drop. `pan.mail.conversation` decides
     * what "their own" means; this only hands over who is on the conversation.
     */
    openLinkDialog() {
        this.dialog.add(LinkDialog, {
            partnerId: this.state.selected?.partner_id || false,
            onSelect: (model, resId) => this.linkTo(model, resId, "picker"),
        });
    }

    /**
     * Move the conversation, and say what the move bought.
     *
     * The confirmation names the conversation link rather than the move, because
     * that is the part somebody would not otherwise know happened: the rest
     * of this conversation now files itself.
     */
    async linkTo(model, resId, via = "picker") {
        const messageIds = this.state.conversation.messages.map((message) => message.id);
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
        // Which way the correction came: the one-click suggestion or the
        // picker. The kind of record it went to is not sent, on purpose: a
        // model name is a fact about the customer's Odoo, not about ours.
        this.improve.capture("conversation_linked", { via });
        // The conversation is somewhere else now, so it is addressed by the
        // record it moved to. `keepSelection` then does the right thing in
        // both folders it can be linked from: in the inbox the row is still
        // there under its new record and the reader keeps their place, and in
        // an unlinked folder it is gone, so the screen moves on to the next
        // one waiting -- which is what working a queue means.
        this.state.selected = {
            ...this.state.selected, model: linked.model, res_id: linked.res_id,
        };
        await this.refresh({ keepSelection: true });
        if (this.state.selected && this.state.selected.model === linked.model
            && this.state.selected.res_id === linked.res_id) {
            // Still on it: re-read the conversation so the chips replace the
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
