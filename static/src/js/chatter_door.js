/** @odoo-module */
/**
 * Door 1: from a record's chatter into the Inbox.
 *
 * The chatter shows the messages filed on *this* record. The conversation they
 * belong to may be larger, and this button is the way over to it:
 * **Open in mail**, drawn on any record that carries an emailed message.
 *
 * One thread on the record and it opens that conversation. More than one and
 * it opens the Inbox with the list narrowed to this record and nothing
 * selected -- newest is a guess, and the conversation somebody wants is the
 * one they were just reading. `pan.mail.conversation.record_conversations()`
 * is what says which of the two this is.
 *
 * `session.pan_mail_inbox` is the gate. Without it this would call the read
 * layer on every form anybody opens and be refused on most of them: the Inbox
 * is for mailbox managers, and an AccessError per record open is not a way to
 * find that out.
 *
 * The button is added by inheriting `mail.Chatter` and it is a plain
 * `<button>` on purpose -- a component tag in a borrowed template resolves
 * against the class Odoo mounted, which on Enterprise snapshotted its
 * components before this module loaded. See static/src/js/connect_banner.js,
 * and the check in tools/ci_lint.sh that keeps it that way.
 */

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { useEffect, useState } from "@odoo/owl";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.action = useService("action");
        this.mailProOrm = useService("orm");
        // `threads` is zero until the answer arrives, so the button appears
        // with the count it needs rather than before it.
        this.mailPro = useState({ threads: 0 });
        useEffect(
            (model, threadId) => {
                this.loadMailProDoor(model, threadId);
            },
            () => [this.props.threadModel, this.props.threadId]
        );
    },

    /** Is there mail on this record, and how many threads of it. */
    async loadMailProDoor(model, threadId) {
        this.mailPro.threads = 0;
        if (!session.pan_mail_inbox || !session.pan_mail_connected || !model || !threadId) {
            return;
        }
        try {
            const door = await this.mailProOrm.call(
                "pan.mail.conversation", "record_conversations", [model, threadId]);
            this.mailPro.threads = door.here ? door.threads || 1 : 0;
        } catch (error) {
            // A door that cannot be drawn is a chatter without a button, not
            // an error dialog over the record somebody came to read.
            console.warn("[Mail Pro] Open in mail could not be drawn", error);
        }
    },

    openInMail() {
        this.action.doAction("pan_mail_pro.action_pan_mail_conversation", {
            additionalContext: {
                pan_mail_model: this.props.threadModel,
                pan_mail_res_id: this.props.threadId,
                pan_mail_select: this.mailPro.threads <= 1,
            },
        });
    },
});
