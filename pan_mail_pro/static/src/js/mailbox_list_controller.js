/** @odoo-module */

import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { registry } from "@web/core/registry";

export class MailboxListController extends ListController {
    /**
     * Ticking Notification on one row unticks it on the other, on the
     * server. Reload so the list shows that rather than two ticks.
     */
    async onRecordSaved(record, changes) {
        await super.onRecordSaved(...arguments);
        if (changes && "is_notification_mailbox" in changes) {
            await this.model.load();
        }
    }
}

export const mailboxListView = {
    ...listView,
    Controller: MailboxListController,
};

registry.category("views").add("mailbox_list", mailboxListView);
