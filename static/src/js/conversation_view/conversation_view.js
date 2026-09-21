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
import { useBus, useService } from "@web/core/utils/hooks";
// The search bar is Odoo's own, over `mail.message`: the same box, the same
// autocomplete, the same facets, the same filter menu. What it produces is a
// domain, and a domain is all `pan.mail.conversation` ever wanted -- every
// conversation on this screen is a group of `mail.message` rows. The filters
// live in a search view (`view_pan_mail_inbox_search`), so adding one is an
// inherited view rather than a patched component.
import { SearchModel } from "@web/search/search_model";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { View } from "@web/views/view";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
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

// The folder that is your own mailbox rather than what Odoo imported. One
// entry under your own mailbox and nowhere else: the imported folders are the
// simple path and stay exactly as they were, and the whole mailbox is one
// level deeper, for the days you want to work here instead of in Outlook.
const LIVE_FOLDER = "all";

// What you can ask of that folder, and the reason it exists: does Odoo have
// this mail. Deliberately *not* in the search bar next to it. Every filter in
// that bar is a domain over `mail.message`, and these rows are not
// `mail.message` rows at all -- they are a provider's answer, and the
// question is whether Odoo has them. A control of its own says that; a facet
// in a bar that cannot reach them would be a filter that lies.
const LIVE_FILTERS = [
    { id: "unlinked", name: _t("Not in Odoo") },
    { id: "linked", name: _t("In Odoo") },
];

// The context key the "Linked to nothing" filter carries. Mail filed on
// nothing is not one conversation, and that is the one thing its domain
// cannot say. Kept in step with `UNGROUPED_KEY` in
// `models/pan_mail_conversation.py`.
const UNGROUPED_KEY = "pan_mail_ungrouped";

// What a pane with nothing selected holds. A function rather than a constant:
// four lists shared between two selections is one stale thread away from a
// reply landing under the wrong subject.
const EMPTY_CONVERSATION = () => ({
    messages: [], records: [], rejected: [], activities: [], suggestion: false,
    // Your own unsent answers on this record. Nobody else's: the rule on
    // `pan.mail.draft` decides that, and the screen never asks for more.
    drafts: [],
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

// What the server said, for the banner. An RPC failure carries the reason the
// call refused -- a missing column after a deploy that never upgraded, a model
// that is not there, an access error -- and hiding it behind "something went
// wrong" turns a one-line answer into a log-reading session. One line, never
// the traceback: the details dialog is Odoo's job, not this banner's.
function serverReason(error) {
    const reason = error?.data?.message || error?.message || "";
    return String(reason).split("\n")[0].trim().slice(0, 300);
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
        Dropdown, FollowerList, SearchBar,
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
        this.composer = useComposer({
            onSent: () => this.onReplySent(),
            onDraftSaved: (row) => this.onDraftSaved(row),
        });
        // Help improve Mail Pro: a no-op unless the session says otherwise.
        // Every `capture` below names a screen or a button, never content.
        this.improve = useImprove();
        // Odoo's own search model, over `mail.message`. `useSubEnv` is how
        // `SearchBar` finds it, the same way every view in the web client
        // hands it to its control panel. Everything the reader types, picks
        // or removes comes back out of it as one domain.
        this.searchModel = new SearchModel(this.env, {
            orm: this.orm,
            view: useService("view"),
            field: useService("field"),
            name: useService("name"),
            dialog: this.dialog,
            treeProcessor: useService("tree_processor"),
        });
        useSubEnv({ searchModel: this.searchModel });
        useBus(this.searchModel, "update", () => this.onSearch());

        // Door 1: the chatter's Open in mail names the record it came from,
        // and whether one conversation is the answer or the reader has to
        // pick. The client action takes no params of its own, so it arrives
        // in the context. See static/src/js/chatter_door.js.
        const context = this.props.action?.context || {};
        this.openedOn = context.pan_mail_model && context.pan_mail_res_id
            ? {
                  model: context.pan_mail_model,
                  res_id: context.pan_mail_res_id,
                  select: context.pan_mail_select !== false,
              }
            : null;

        // Two request tokens, one per pane. Somebody who clicks three folders
        // in a second starts three reads, and without these the slowest answer
        // wins the screen -- which need not be the one they asked for last.
        this.listSeq = 0;
        this.conversationSeq = 0;

        this.state = useState({
            loading: true,
            error: "",
            // The banner says what broke, not only that something did, and
            // what to do about it when the server can name it.
            errorReason: "",
            errorRemedy: "",
            folder: "inbox",
            // Two dimensions, two controls: the mailbox list says where you
            // are, the search bar says what you are looking for in there.
            // Naming our own states as folders made the mailbox list read
            // like a filter panel next to the mail client everybody also has
            // open, so they are filters in the search view instead.
            mailboxes: [],
            mailboxId: null,
            // The mailbox list, the way Outlook draws it: every mailbox can stand
            // open or folded, and folding one does not close the mail you
            // are reading. `counts` is keyed by mailbox id (0 when there is
            // no mailbox yet), so a folded mailbox costs no query at all.
            expanded: {},
            counts: {},
            // The mailboxes this reader may open in full, which is their own
            // and nobody else's -- the server decides that, this is the
            // answer. Empty for somebody who reads only shared mailboxes,
            // and then the live folder is simply not drawn.
            liveIds: [],
            // The live message being read, when it is one Odoo does not
            // have. A conversation and a loose message are different things
            // on screen: this one has no thread, no record and no reply,
            // only what it says and a button to file it.
            live: null,
            liveBusy: false,
            liveConnected: true,
            // Which half of the live folder is on screen: the mail Odoo has,
            // the mail it does not, or all of it.
            liveFilter: null,
            conversations: [],
            // Door 1's narrowing: while it is set the list is the mail on one
            // record rather than the mail in one mailbox. Any folder,
            // mailbox or search leaves it, because each of those is a
            // question about a mailbox.
            record: null,
            limit: PAGE,
            hasMore: false,
            selected: null,
            // The chevron, the way Outlook draws a conversation list: a row
            // says how many mails are in there, and unfolding it says which.
            // Keyed by the row's own key, so folding one thread says nothing
            // about the next. `thread` is the cache the fold reads back.
            unfolded: {},
            thread: {},
            threadLoading: {},
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
        });

        // Splitting a body into "what was written" and "what was quoted" is a
        // parse per message, and Owl re-renders this pane on every hover
        // state. Outside `state` on purpose: it is derived from a message that
        // cannot change, so it is a cache and not a fact.
        this.split = new Map();

        onWillStart(async () => {
            const [, searchViewId] = await Promise.all([
                this.loadMailboxes(),
                this.orm.call("pan.mail.conversation", "inbox_search_view_id", []),
            ]);
            await this.searchModel.load({
                resModel: "mail.message",
                searchViewId,
                // Filters, and nothing else. Group By is a question about a
                // list over a table and this list is a mailbox: the grouping
                // is the conversation. A favourite would be a saved search
                // per model rather than per screen, which is a promise this
                // one cannot keep.
                searchMenuTypes: ["filter"],
            });
            if (this.openedOn) {
                this.state.record = {
                    model: this.openedOn.model,
                    res_id: this.openedOn.res_id,
                    name: "",
                };
                // Mail on a record, wherever it arrived: the reader came from
                // the record and not from a mailbox, and the thread they want
                // may well have been synced by another one.
                this.state.mailboxId = null;
            }
            await this.refresh({ select: this.openedOn ? this.openedOn.select : true });
            // Deliberately not awaited: the list is already on screen and
            // this only corrects the dots on it. Waiting would make the first
            // paint as slow as the provider is.
            this.refreshReadState();
        });
    }

    // ----------------------------------------------------------------- load

    async loadMailboxes() {
        // The notification mailbox is the one the module sends *from*, not one
        // anybody reads. Opening the inbox on it shows an empty screen to
        // somebody whose mail is one dropdown away, which reads as broken.
        // `status_message` is empty on a healthy mailbox, which is the whole
        // interface: this pane shows a marker on a truthy value and nothing at
        // all otherwise, rather than deciding for itself what healthy looks
        // like. The mailbox form's alert reads the same string.
        this.state.mailboxes = await this.orm.searchRead(
            "pan.mail.mailbox",
            [["active", "=", true], ["is_notification_mailbox", "=", false]],
            ["email", "status_message"],
            { limit: 50, order: "sequence, email" }
        );
        if (this.state.mailboxes.length) {
            this.state.mailboxId = this.state.mailboxes[0].id;
        }
        // Where leaving door 1's narrowing puts the reader back.
        this.defaultMailboxId = this.state.mailboxId;
        try {
            const live = await this.orm.call("pan.mail.conversation", "live_mailboxes", []);
            this.state.liveIds = live.map((mailbox) => mailbox.id);
        } catch (error) {
            // No live folder is a smaller inbox, not a broken one.
            console.warn("[Mail Pro] live mailboxes failed", error);
            this.state.liveIds = [];
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

    /**
     * Ask the provider which mail is unread, then redraw if it disagreed.
     *
     * Beside the list load rather than before it: the screen paints from what
     * Odoo already knows, and the correction arrives a moment later if there
     * is one. Waiting for a network call before the first row appears would
     * make every visit to the Inbox as slow as the provider is.
     *
     * Throttled server-side per mailbox, so clicking between folders costs one
     * call a minute, and silent on failure: an unreachable provider leaves the
     * mirror as it was.
     */
    async refreshReadState() {
        try {
            const changed = await this.orm.silent.call(
                "pan.mail.conversation", "refresh_read_state", [], {
                    mailbox_id: this.state.mailboxId,
                });
            if (changed) {
                await this.refresh({ keepSelection: true });
            }
        } catch (error) {
            console.warn("[Mail Pro] could not refresh read state", error);
        }
    }

    async refresh({ keepSelection = false, select = true } = {}) {
        const seq = ++this.listSeq;
        this.state.loading = true;
        this.state.error = "";
        this.state.errorReason = "";
        this.state.errorRemedy = "";
        if (!keepSelection) {
            // Another folder, filter or search is another list, and an
            // unfolded thread from the previous one would reopen under
            // whichever row happens to land on that key.
            this.state.unfolded = {};
            this.state.thread = {};
            this.state.threadLoading = {};
        }
        try {
            const args = {
                mailbox_id: this.state.mailboxId,
                ...this.searchArgs(),
            };
            const record = this.state.record
                ? { record_model: this.state.record.model,
                    record_id: this.state.record.res_id }
                : {};
            // One count query per mailbox that is standing open. A folded
            // mailbox is not counted, which is what keeps a mailbox list of six
            // accounts from costing six times the queries of one.
            const keys = this.expandedKeys();
            const [counts, conversations] = await Promise.all([
                Promise.all(keys.map((key) => this.orm.call(
                    "pan.mail.conversation", "folder_counts", [], {
                        ...args,
                        mailbox_id: key || null,
                    }))),
                // The live folder is read from the provider, so it takes
                // neither the domain the search bar built nor the folder the
                // counts are for.
                this.isLive
                    ? this.readLiveFolder()
                    : this.orm.call("pan.mail.conversation", "search_conversations", [], {
                        ...args,
                        ...record,
                        folder: this.state.folder,
                        limit: this.state.limit,
                    }),
            ]);
            if (seq !== this.listSeq) {
                return; // A newer request is already on its way.
            }
            this.state.counts = Object.fromEntries(
                keys.map((key, index) => [key, counts[index]]));
            this.state.conversations = conversations;
            // The live folder is the newest page and has no next one: the
            // provider contract's search takes a limit and no offset, so
            // older mail is a search term rather than a scroll.
            this.state.hasMore = !this.isLive
                && conversations.length >= this.state.limit;
            if (this.state.record && conversations.length) {
                // The record's own name, for the header over the narrowed
                // list. It comes off the mail rather than a read of its own.
                this.state.record.name = conversations[0].record_name || "";
            }

            const stillThere = keepSelection && this.state.selected
                && conversations.some((row) => this.sameConversation(row, this.state.selected));
            if (!stillThere) {
                if (select && conversations.length && !this.panes.state.small) {
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
                this.state.errorReason = serverReason(error);
                this.loadRemedy(error);
            }
            console.warn("[Mail Pro] conversation list failed", error);
        } finally {
            if (seq === this.listSeq) {
                this.state.loading = false;
            }
        }
    }

    // ------------------------------------------------------------ live

    /** Is the open folder the mailbox itself rather than what Odoo imported? */
    get isLive() {
        return this.state.folder === LIVE_FOLDER;
    }

    /** May this mailbox be opened in full? Only its owner's own may. */
    isLiveMailbox(mailboxId) {
        return this.state.liveIds.includes(this.mailboxKey(mailboxId));
    }

    /**
     * One page of the mailbox itself, as list rows.
     *
     * The rows come back in the same shape the imported list draws, so there
     * is one list component and one template: what a live row adds is
     * `linked`, and what it lacks is a `message_id`, because Odoo has no
     * message for it yet.
     */
    async readLiveFolder() {
        const linked = this.state.liveFilter === "linked" ? true
            : this.state.liveFilter === "unlinked" ? false : null;
        const result = await this.orm.call(
            "pan.mail.conversation", "live_messages", [], {
                mailbox_id: this.state.mailboxId,
                folder: "inbox",
                linked,
                // The words out of the search bar, handed to the provider
                // rather than compiled into a domain: this folder searches
                // the whole mailbox, which is the one thing it does better
                // than the imported list beside it.
                search: this.searchText(),
            });
        this.state.liveConnected = result.connected;
        return result.rows;
    }

    /**
     * Read a live message that Odoo does not have.
     *
     * Not a conversation: there is no thread to draw, no record beside it and
     * nothing to reply to yet. What the pane shows is the mail and the one
     * button that changes that.
     */
    async readLiveMessage(row) {
        const seq = ++this.conversationSeq;
        try {
            const message = await this.orm.call(
                "pan.mail.conversation", "read_live_message", [], {
                    mailbox_id: this.state.mailboxId,
                    provider_message_id: row.live_id,
                });
            if (seq === this.conversationSeq) {
                this.state.live = message;
            }
        } catch (error) {
            if (seq === this.conversationSeq) {
                this.state.error = _t("Could not open that email.");
            }
            console.warn("[Mail Pro] live message failed to open", error);
        }
    }

    /**
     * File the open live message in Odoo.
     *
     * The moment a private read becomes Odoo data, which is why it is a
     * button and not something opening a message does on its own. Where it
     * lands is the matcher's answer, so the list is read again and the
     * conversation it became is opened.
     */
    async importLive() {
        const row = this.state.selected;
        if (!row || !row.live || this.state.liveBusy) {
            return;
        }
        this.state.liveBusy = true;
        try {
            const result = await this.orm.call(
                "pan.mail.conversation", "import_live_message", [], {
                    mailbox_id: this.state.mailboxId,
                    provider_message_id: row.live_id,
                });
            if (!result.linked) {
                this.state.error = _t("Odoo would not take that email in.");
                return;
            }
            this.state.live = null;
            await this.refresh();
            await this.select({
                ...row,
                linked: true,
                model: result.linked.model,
                res_id: result.linked.res_id,
                record_name: result.linked.name,
            });
        } catch (error) {
            this.state.error = _t("Could not file that email.");
            console.warn("[Mail Pro] live import failed", error);
        } finally {
            this.state.liveBusy = false;
        }
    }

    /**
     * What to do about the failure, under the line that reports it.
     *
     * Two cases can be named honestly and no more. A request that never got
     * an answer is Odoo or the connection to it, and asking the server about
     * it would fail the same way. Everything else is the server's to explain,
     * so we ask it: the common answer is a database the deploy never
     * upgraded, which no error message in the browser can diagnose.
     */
    async loadRemedy(error) {
        if (!error?.data) {
            this.state.errorRemedy = _t(
                "Odoo did not answer. Check your connection and try again.");
            return;
        }
        try {
            this.state.errorRemedy = await this.orm.silent.call(
                "pan.mail.conversation", "failure_remedy", []);
        } catch {
            // The reason is already on screen; a second failure adds nothing.
            this.state.errorRemedy = "";
        }
    }

    sameConversation(left, right) {
        // A draft has no message to key on and there can be two of them on one
        // record, so its own id is what tells the rows apart. Undefined on
        // both sides for every other row, which is the ordinary case.
        return left.model === right.model
            && left.res_id === right.res_id
            && left.message_id === right.message_id
            && (left.draft_id || false) === (right.draft_id || false);
    }

    /**
     * A conversation picked from the list: on a phone, that is also a step.
     *
     * The click also unfolds it, the way Outlook does: the conversation you
     * are reading is the one whose mails the list shows. One at a time --
     * a list that keeps every thread you have looked at open is a list you
     * scroll through your own history in -- so picking folds the rest back.
     */
    async pick(conversation) {
        this.panes.showConversation();
        this.foldOthers(conversation);
        if (conversation.draft_id) {
            // A row in Drafts is an unsent mail, and there is one thing to do
            // with one: carry on writing it. So the conversation opens with
            // the composer already on it, in a single click.
            await this.continueDraft(conversation);
            return;
        }
        const opened = this.select(conversation);
        if (conversation.count > 1 && !this.isUnfolded(conversation)) {
            await this.unfold(conversation);
        }
        await opened;
    }

    /** The key a conversation's unfolded thread is cached under. */
    conversationKey(conversation) {
        // A live row has no Odoo message to key on. It is also never
        // unfoldable -- one provider message is one row, count 1, no chevron
        // -- so this only has to be a key nothing else answers to.
        return conversation.message_id || conversation.live_id;
    }

    /** Is this conversation unfolded into the mails it is made of? */
    isUnfolded(conversation) {
        return !!this.state.unfolded[this.conversationKey(conversation)];
    }

    /** Those mails, or nothing at all while the read is still out. */
    threadOf(conversation) {
        return this.state.thread[this.conversationKey(conversation)] || [];
    }

    isThreadLoading(conversation) {
        return !!this.state.threadLoading[this.conversationKey(conversation)];
    }

    /**
     * The chevron: unfold a conversation into its own mails, one line each.
     *
     * The fold, and the one way to look into a conversation without opening
     * it: a chevron that also switched the pane would cost the reader the
     * conversation they had open to answer "how many of these are from her".
     * Opening a conversation unfolds it too, from `pick`.
     *
     * One read per conversation, kept until the list is rebuilt. Folding
     * keeps the rows, because folding and unfolding the same thread twice is
     * not two questions.
     */
    async toggleUnfold(conversation) {
        const key = this.conversationKey(conversation);
        if (this.state.unfolded[key]) {
            this.state.unfolded[key] = false;
            return;
        }
        await this.unfold(conversation);
    }

    /**
     * Unfold one conversation, reading its mails the first time it is asked.
     *
     * Both ways in end here: the chevron, and the click that opens the
     * conversation. So the rows are read once whichever one the reader used,
     * and a failure folds the row back either way.
     */
    async unfold(conversation) {
        const key = this.conversationKey(conversation);
        this.state.unfolded[key] = true;
        if (this.state.thread[key]) {
            return;
        }
        this.state.threadLoading[key] = true;
        try {
            this.state.thread[key] = await this.orm.call(
                "pan.mail.conversation", "conversation_messages", [], {
                    model: conversation.model,
                    res_id: conversation.res_id,
                    message_id: conversation.message_id,
                    mailbox_id: this.state.mailboxId,
                });
        } catch (error) {
            // Fold it back rather than leave an empty box standing open: the
            // row above it still opens the conversation, which is the way in
            // that matters.
            this.state.unfolded[key] = false;
            this.notification.add(
                _t("Could not read that conversation."), { type: "warning" });
            console.warn("[Mail Pro] could not unfold a conversation", error);
        } finally {
            this.state.threadLoading[key] = false;
        }
    }

    /** Everything else folds back: only what is being read stands open. */
    foldOthers(conversation) {
        const key = String(this.conversationKey(conversation));
        for (const other of Object.keys(this.state.unfolded)) {
            if (other !== key) {
                this.state.unfolded[other] = false;
            }
        }
    }

    /** A mail picked from under the chevron: the pane opens on that one. */
    async pickMessage(conversation, message) {
        this.panes.showConversation();
        await this.select(conversation, { openMessageId: message.id });
    }

    /** The step back, on a phone. Nothing is deselected: the list marks it. */
    async backToList() {
        await this.leaveComposer();
        this.state.compose = null;
        this.panes.showConversationList();
    }

    /**
     * Close the composer on the way out, keeping whatever was typed.
     *
     * Leaving is not discarding: Discard still throws the answer away, and
     * every other way out of the composer -- another conversation, the step
     * back on a phone -- stores it as a draft instead. The count under the
     * mailbox is corrected; the list is not re-read, because the reader is
     * already on their way somewhere and a list that reorders under them is
     * worse than a number that waits for the next read.
     */
    async leaveComposer() {
        const stored = await this.composer.leave();
        if (stored) {
            this.notification.add(_t("Draft saved."), { type: "success" });
            await this.loadCounts(this.mailboxKey());
        }
        return stored;
    }

    /**
     * Open a conversation in the pane.
     *
     * `openMessageId` is the mail the reader clicked under the chevron. It is
     * seeded into `state.open` before the read, because `readConversation`
     * keeps what was already open and only falls back to the newest message
     * when nothing is -- so the mail you picked is the one that is unfolded
     * when the pane draws, and not the one at the top of the thread.
     */
    async select(conversation, { openMessageId = null } = {}) {
        // A reply belongs to the conversation it answers, and this is another
        // one, so the composer closes with it -- and what was typed into it
        // is kept as a draft on the conversation it was written for. Losing
        // an answer to a click on the list was the one thing this pane did
        // that nobody expected.
        await this.leaveComposer();
        this.state.selected = conversation;
        this.state.showRejected = false;
        this.improve.capture("conversation_opened", { folder: this.state.folder });
        // Nothing from the previous conversation stays under the new subject.
        this.state.conversation = EMPTY_CONVERSATION();
        this.state.activityIds = [];
        this.state.open = openMessageId ? { [openMessageId]: true } : {};
        this.state.quotes = {};
        this.state.details = {};
        this.split.clear();
        this.state.live = null;
        if (conversation.live && !conversation.linked) {
            await this.readLiveMessage(conversation);
            return;
        }
        await this.readConversation();
        await this.markRead(conversation);
    }

    /**
     * Opening a conversation reads it, the way every mail client means it.
     *
     * Always asked, even for a conversation the list already drew as read:
     * the mailbox's read state and your own Odoo Inbox rows are two different
     * facts, and the bell can still be ringing for a mail the mailbox calls
     * read. The server marks only what moved, so saying so twice costs one
     * query and no provider call.
     *
     * Silent on failure. A dot that is a second out of date is not worth an
     * error over a conversation the reader has in front of them.
     */
    async markRead(conversation) {
        try {
            await this.orm.silent.call(
                "pan.mail.conversation", "set_read", [], {
                    model: conversation.model,
                    res_id: conversation.res_id,
                    message_id: conversation.message_id,
                    mailbox_id: this.state.mailboxId,
                    read: true,
                });
        } catch (error) {
            console.warn("[Mail Pro] could not mark the conversation read", error);
            return;
        }
        this.setUnreadLocally(conversation, false);
    }

    /** Is the open conversation one the mailbox still calls unread? */
    get selectedUnread() {
        return !!(this.state.selected && this.state.selected.unread);
    }

    /** What the one read-state button in the header says right now. */
    get readToggleLabel() {
        return this.selectedUnread ? _t("Mark read") : _t("Mark unread");
    }

    /**
     * Read and unread, from the conversation you have open.
     *
     * A toggle, because the button is the only place the click can answer.
     * Marking unread and then reading it again used to mean opening another
     * conversation and coming back, and the one button said "Mark unread"
     * over a conversation that already was: a second click that did nothing,
     * which is what a broken button looks like.
     *
     * The list is corrected here rather than by reloading it. A reload would
     * re-sort, lose the reader's place, and on the Unread filter make the
     * conversation they are reading jump into the list under them.
     */
    async toggleRead() {
        const conversation = this.state.selected;
        if (!conversation) {
            return;
        }
        const read = this.selectedUnread;
        try {
            await this.orm.call("pan.mail.conversation", "set_read", [], {
                model: conversation.model,
                res_id: conversation.res_id,
                message_id: conversation.message_id,
                mailbox_id: this.state.mailboxId,
                read,
            });
        } catch (error) {
            console.warn("[Mail Pro] could not change the conversation's read state",
                         error);
            return;
        }
        this.setUnreadLocally(conversation, !read);
    }

    /** The dot on the row and the button in the header, without a reload. */
    setUnreadLocally(conversation, unread) {
        for (const row of this.state.conversations) {
            if (this.sameConversation(row, conversation)) {
                row.unread = unread;
            }
        }
        if (this.state.selected
            && this.sameConversation(this.state.selected, conversation)) {
            this.state.selected.unread = unread;
        }
        // Reading a conversation reads every mail in it, so the rows under
        // the chevron cannot keep a dot the row above them just lost.
        for (const row of this.threadOf(conversation)) {
            row.unread = unread;
        }
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
                this.state.errorReason = serverReason(error);
                this.loadRemedy(error);
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
        this.leaveRecord();
        if (mailboxId !== undefined && mailboxId !== this.state.mailboxId) {
            this.state.mailboxId = mailboxId;
        // Opening a mailbox unfolds it: the folders are where you go next.
        this.state.expanded[this.mailboxKey()] = true;
        this.saveExpanded();
        }
        // The live folder's own filter is a question only it can ask, so
        // leaving it puts the question away rather than carrying it into a
        // folder with no control to show it in.
        if ((folder === LIVE_FOLDER) !== this.isLive) {
            this.state.liveFilter = null;
        }
        this.state.folder = folder;
        this.panes.closeMailboxList();
        // The search is a question about the folder you are in, so switching
        // folder keeps it: "linked to nothing" in Sent is a fair question,
        // and dropping it on every click is the thing that makes a search
        // bar feel like it undoes itself. Drafts live in another table, so
        // the filters over `mail.message` simply do not reach them -- only
        // the words somebody typed do.
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
                    ...this.searchArgs(),
                });
        } catch (error) {
            // A mailbox list that cannot count is a mailbox list without numbers, not an
            // error banner over the mail somebody is reading.
            console.warn("[Mail Pro] folder counts failed", error);
            this.state.counts[key] = [];
        }
    }

    foldersFor(mailboxId) {
        const folders = this.state.counts[this.mailboxKey(mailboxId)] || [];
        if (!this.isLiveMailbox(mailboxId)) {
            return folders;
        }
        // Last, and without a number. It is not a third place mail sits: it
        // is the mailbox itself, and counting it would mean asking the
        // provider how much mail you have every time a folder is unfolded.
        return [...folders, {
            id: LIVE_FOLDER,
            name: _t("All email"),
            kind: "folder",
            count: 0,
            capped: false,
        }];
    }

    /**
     * The two questions the live folder answers, and the one in use.
     *
     * Its own control rather than a facet in the search bar: every filter in
     * that bar is a domain over `mail.message`, and these rows are a
     * provider's answer that no domain can reach.
     */
    get liveFilters() {
        return LIVE_FILTERS;
    }

    get activeLiveFilter() {
        return LIVE_FILTERS.find((pill) => pill.id === this.state.liveFilter) || null;
    }

    /** Narrow the live folder, or clear it with a second click. */
    async setLiveFilter(filter) {
        this.state.liveFilter = this.state.liveFilter === filter ? null : filter;
        await this.refresh();
    }
    }

    /** Open another mailbox, from the mailbox list. Folders are per mailbox. */
    async setMailbox(mailboxId) {
        this.panes.closeMailboxList();
        if (mailboxId === this.state.mailboxId) {
            return;
        }
        this.leaveRecord();
        this.state.mailboxId = mailboxId;
        // Opening a mailbox unfolds it: the folders are where you go next.
        this.state.expanded[this.mailboxKey()] = true;
        this.saveExpanded();
        // The folder and the search carry over. Every mailbox has the same
        // two folders, and landing back in Inbox on every switch loses the
        // one thing somebody switching mailboxes is usually doing: working
        // one view across all of them. Except the live folder, which not
        // every mailbox has: a mailbox you do not own lands in Inbox.
        if (this.isLive && !this.isLiveMailbox(mailboxId)) {
            this.state.folder = "inbox";
            this.state.liveFilter = null;
        }
        this.state.limit = PAGE;
        await this.refresh();
        // A mailbox the reader just opened is one whose dots are worth being
        // right. Throttled server-side, so switching back and forth is free.
        this.refreshReadState();
    }

    /**
     * What the search bar is asking for, as the two arguments every read of
     * the list takes.
     *
     * The domain is the whole of it, bar two things a domain cannot say.
     * Mail filed on nothing is not one conversation, so the filter that asks
     * for it carries `pan_mail_ungrouped` in its own context and the list
     * stops grouping -- a search view attribute, not a special case in here.
     * And Drafts are a table of their own, so a domain over `mail.message`
     * means nothing to them; what does carry across is the words somebody
     * typed, which is every text facet in the bar.
     */
    searchArgs() {
        return {
            domain: this.searchModel.domain,
            search: this.searchText(),
            ungrouped: !!this.searchModel.context[UNGROUPED_KEY],
        };
    }

    /** What the reader typed, as one string: the bar's text facets. */
    searchText() {
        const typed = this.searchModel.facets
            .filter((facet) => facet.type === "field")
            .flatMap((facet) => facet.values);
        return typed.join(" ") || null;
    }

    /**
     * The search changed: a word typed, a facet removed, a filter picked.
     *
     * Back to the whole mailbox and to the first page, because a search is a
     * question about a mailbox rather than about the record door 1 opened on.
     */
    async onSearch() {
        this.leaveRecord();
        this.state.limit = PAGE;
        await this.refresh();
    }

    /**
     * Leave door 1's narrowing, without reading anything: every caller is on
     * its way to a read of its own.
     *
     * The mailbox comes back with it. The door opened the Inbox on every
     * mailbox the reader may see, which is right for one record's mail and
     * wrong for the folder they just clicked.
     */
    leaveRecord() {
        if (!this.state.record) {
            return;
        }
        this.state.record = null;
        if (!this.state.mailboxId) {
            this.state.mailboxId = this.defaultMailboxId;
        }
    }

    /** The way back to the whole mailbox, from the narrowed list's header. */
    async showWholeMailbox() {
        this.leaveRecord();
        this.state.limit = PAGE;
        await this.refresh();
    }

    async loadMore() {
        this.state.limit += PAGE;
        await this.refresh({ keepSelection: true });
    }

    // --------------------------------------------------------------- render

    /**
     * A new mail is open in the pane. The composer is what says so: Discard
     * closes it without a word to anyone else, so `state.compose` alone
     * outlives the mail it describes.
     */
    get composingNew() {
        return Boolean(this.state.compose)
            && this.composer.state.open
            && this.composer.state.mode === "new";
    }

    get selectedRecord() {
        // A new mail is written on a record that is picked, not read: the
        // conversation behind the pane is still the one that was open, and
        // its record is not the one this mail is about. The pick wins for as
        // long as the new mail is open.
        if (this.composingNew) {
            const compose = this.state.compose;
            return {
                model: compose.model,
                res_id: compose.res_id,
                name: compose.label,
                model_label: compose.model_label,
            };
        }
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

    /**
     * The body of a live message.
     *
     * The only body on this screen that never passed through `message_post`,
     * so `read_live_message` runs Odoo's own `html_sanitize` over it before
     * it leaves the server -- the same call the Html field makes on write.
     * Rendering a provider's HTML unsanitized in an Odoo session is a mail
     * from anybody running script as the reader.
     */
    safeBody(body) {
        return markup(body || "");
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
        if (this.state.record) {
            // Arrived through door 1: the list is one record's mail, so the
            // header says which record and not which folder.
            return this.state.record.name || _t("This record");
        }
        const folders = this.foldersFor();
        return (folders.find((e) => e.id === this.state.folder) || {}).name || "";
    }

    /** Is the search bar asking for anything? The empty state reads it. */
    get searching() {
        return this.searchModel.facets.length > 0;
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
     * dialog linking uses asks the same two questions, in the same two
     * searchable steps, and the composer only opens once both are answered.
     * One dialog for "where does this mail belong" is one thing to learn.
     */
    newEmail() {
        this.dialog.add(LinkDialog, {
            title: _t("New email on"),
            onSelect: (model, resId, label, modelLabel) =>
                this.composeOn(model, resId, label, modelLabel),
        });
    }

    /** The pane composer, on the record just picked. */
    async composeOn(model, resId, label, modelLabel) {
        // The pane is hidden while the record has the screen to itself.
        if (this.panes.state.zoom) {
            this.panes.toggleZoom();
        }
        this.panes.showConversation();
        this.state.compose = {
            model,
            res_id: resId,
            label: label || "",
            model_label: modelLabel || "",
        };
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

    // ------------------------------------------------------------- drafts

    /** The unsent answers on the open conversation. Yours, and only yours. */
    get drafts() {
        return this.state.conversation.drafts || [];
    }

    /**
     * Whether the open composer can be put away rather than sent.
     *
     * Not on a note. A note is two lines to the record's followers and it is
     * written in one sitting; a Drafts folder that fills up with half-written
     * notes is a second inbox for something that was never mail.
     */
    get canSaveDraft() {
        return this.composer.state.open && this.composer.state.mode !== "note";
    }

    /**
     * Open a stored draft: its conversation behind it, its words in the pane.
     *
     * The conversation first, so the pane the composer sits in is the thread
     * the draft answers rather than an empty one. Then the composer, on the
     * defaults the draft hands back -- the same subject, recipients and files
     * it was saved with.
     */
    async continueDraft(row) {
        await this.select(row);
        await this.openDraft(row.draft_id);
    }

    /**
     * The composer is made on the server and the pane mounts its form on it.
     *
     * Not opened empty on `default_` values: the composer recomputes its own
     * body and subject while it mounts, so a draft handed over that way is
     * gone before anybody sees it -- which is exactly what the browser check
     * caught. `open_composer` creates the wizard, where the ORM protects the
     * values it was created with, and the form has only to display it.
     */
    async openDraft(draftId) {
        try {
            const composerId = await this.orm.call(
                "pan.mail.draft", "open_composer", [draftId]);
            if (!composerId) {
                // Without an id the form would open on a new, empty composer
                // and look like a draft that lost its words. Say so instead.
                throw new Error("pan.mail.draft.open_composer returned nothing");
            }
            this.composer.open({}, "reply", draftId, composerId);
        } catch (error) {
            this.notification.add(_t("Could not open that draft."), { type: "danger" });
            console.warn("[Mail Pro] draft failed to open", error);
        }
    }

    /**
     * Throw a draft away.
     *
     * The one thing on this screen that destroys something nobody can get
     * back -- there is no Trash for a draft -- so it is the one thing that
     * asks first. Odoo's own confirmation dialog, because a dialog of ours
     * would be a second one to keep in step with it.
     */
    deleteDraft(draftId) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete this draft"),
            body: _t("The text is not kept anywhere else."),
            confirmLabel: _t("Delete"),
            confirm: () => this.discardDraft(draftId),
            cancel: () => {},
        });
    }

    async discardDraft(draftId) {
        await this.orm.call("pan.mail.draft", "discard_draft", [draftId]);
        if (this.composer.state.draftId === draftId) {
            this.composer.close();
        }
        await this.readConversation();
        await this.refresh({ keepSelection: true });
    }

    /**
     * It was put away: the card above the thread, and the Drafts count.
     *
     * A new mail has no conversation behind it, so there is nothing to
     * re-read and the line in the corner is the whole feedback. The folder is
     * recounted either way, because that is where the draft went.
     */
    async onDraftSaved() {
        this.state.compose = null;
        this.notification.add(_t("Draft saved."), { type: "success" });
        await this.readConversation();
        await this.refresh({ keepSelection: true });
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
     * Open the picker: the kind of record, then the record, both searchable.
     *
     * It gets the correspondent so the second step can open on their own
     * records instead of an empty search box. `pan.mail.conversation` decides
     * what that means; this only hands over who is on the conversation.
     */
    openLinkDialog() {
        this.dialog.add(LinkDialog, {
            partnerId: this.state.selected?.partner_id || false,
            correspondent: this.state.selected?.correspondent || "",
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
