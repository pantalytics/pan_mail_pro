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
 */

import { Component, useState, onWillStart, onError, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";

const PAGE = 30;

/** The record pane, isolated so a form-view failure cannot take the page. */
export class RecordPane extends Component {
    static template = "pan_mail_pro.RecordPane";
    static components = { View };
    static props = { record: { type: Object, optional: true } };

    setup() {
        this.action = useService("action");
        this.state = useState({ failed: false });
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

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

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
            thread: { messages: [], records: [], rejected: [] },
            showRejected: false,
            search: "",
        });

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
                    this.state.thread = { messages: [], records: [], rejected: [] };
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
        // Nothing from the previous thread stays under the new subject.
        this.state.thread = { messages: [], records: [], rejected: [] };
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

    async setMailbox(event) {
        const value = parseInt(event.target.value, 10);
        this.state.mailboxId = Number.isNaN(value) ? null : value;
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

    body(message) {
        // `mail.message.body` is an Html field and the framework sanitizes it
        // on write; every provider body enters through `message_post`. This is
        // the same trust the chatter itself extends to that column.
        return markup(message.body || "");
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
                },
            },
            { onClose: () => this.select(conversation) }
        );
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
