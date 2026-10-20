/** @odoo-module */

import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class MailboxListController extends ListController {
    setup() {
        super.setup();
        this.actionService = useService("action");
    }

    /**
     * Navigate back to Mail Pro settings
     */
    onBackToSettings() {
        this.actionService.doAction({
            type: "ir.actions.act_url",
            url: "/odoo/settings#pan_mail_pro",
            target: "self",
        });
    }
}

export const mailboxListView = {
    ...listView,
    Controller: MailboxListController,
    buttonTemplate: "pan_mail_pro.MailboxListButtons",
};

registry.category("views").add("mailbox_list", mailboxListView);
