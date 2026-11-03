/** @odoo-module */
/**
 * The Internal domains list, and what can be read off the database for it.
 *
 * Settings step 3 only reports the list; this is where it is answered, so
 * this is where the suggestion lives (#382). One button beside New, naming
 * the domains it adds, and only while the list is empty: a list that has
 * something in it is the admin's, and offering additions to it forever reads
 * as a complaint about a deliberate choice.
 */

import { onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

export class DomainListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.suggestion = useState({ names: [] });
        onWillStart(async () => {
            this.suggestion.names = await this.orm.call(
                "pan.mail.domain", "suggest_domains", []);
        });
    }

    get suggested() {
        return this.model.root.records.length ? "" : this.suggestion.names.join(", ");
    }

    async onAddSuggested() {
        await this.orm.call("pan.mail.domain", "action_add_suggested", []);
        await this.model.load();
    }
}

export const domainListView = {
    ...listView,
    Controller: DomainListController,
    buttonTemplate: "pan_mail_pro.DomainListButtons",
};

registry.category("views").add("pan_mail_domain_list", domainListView);
