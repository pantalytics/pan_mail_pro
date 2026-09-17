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
 * form cannot render, the pane falls back to a link and the rest of the
 * inbox keeps working.
 */

import { Component, useState, onWillStart, onError, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
import { _t } from "@web/core/l10n/translation";

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

        this.state = useState({
            loading: true,
            error: false,
            folders: [],
            folder: "inbox",
            mailboxes: [],
            mailboxId: null,
            conversations: [],
            selected: null,
            thread: { messages: [], records: [], rejected: [] },
            search: "",
            // Progressive disclosure: the fourth pane is the point of the
            // screen, but on a narrow window it is one tab away instead of
            // three panes squeezed into two.
            showRecord: true,
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
            { limit: 20, order: "sequence, email" }
        );
        if (this.state.mailboxes.length) {
            this.state.mailboxId = this.state.mailboxes[0].id;
        }
    }

    async refresh() {
        this.state.loading = true;
        this.state.error = false;
        try {
            const [folders, conversations] = await Promise.all([
                this.orm.call("pan.mail.conversation", "folder_counts", [], {
                    mailbox_id: this.state.mailboxId,
                }),
                this.orm.call("pan.mail.conversation", "search_conversations", [], {
                    mailbox_id: this.state.mailboxId,
                    folder: this.state.folder,
                    search: this.state.search || null,
                }),
            ]);
            this.state.folders = folders;
            this.state.conversations = conversations;
            if (conversations.length) {
                await this.select(conversations[0]);
            } else {
                this.state.selected = null;
                this.state.thread = { messages: [], records: [], rejected: [] };
            }
        } catch (error) {
            // Keep what the reader was looking at; say one line and offer
            // a retry rather than clearing the pane.
            this.state.error = true;
            console.warn("[Mail Pro] conversation list failed", error);
        } finally {
            this.state.loading = false;
        }
    }

    async select(conversation) {
        this.state.selected = conversation;
        this.state.thread = await this.orm.call(
            "pan.mail.conversation", "read_conversation", [], {
                model: conversation.model,
                res_id: conversation.res_id,
                mailbox_id: this.state.mailboxId,
            }
        );
    }

    async setFolder(folder) {
        this.state.folder = folder;
        await this.refresh();
    }

    async setMailbox(event) {
        this.state.mailboxId = parseInt(event.target.value, 10) || null;
        await this.refresh();
    }

    async onSearch(event) {
        if (event.key !== "Enter") {
            return;
        }
        this.state.search = event.target.value;
        await this.refresh();
    }

    // --------------------------------------------------------------- render

    get selectedRecord() {
        const chips = this.state.thread.records || [];
        return chips.length ? chips[0] : null;
    }

    body(message) {
        return markup(message.body || "");
    }

    day(value) {
        if (!value) {
            return "";
        }
        return String(value).slice(0, 16).replace("T", " ");
    }

    folderLabel(id) {
        const folder = this.state.folders.find((f) => f.id === id);
        return folder ? folder.name : _t("Inbox");
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
