/** @odoo-module */
/**
 * The provider form, verified on save.
 *
 * The form used to carry Test Credentials and Sign In Myself in its header,
 * even on a provider that was already connected. Now a save that changes the
 * registration opens one dialog: connecting, verifying, then the verdict. On
 * success its one button is the next step, signing in; on failure it says
 * which field to fix and closes back onto the form. A connected provider
 * offers nothing, because there is nothing left to do.
 */

import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";

const REGISTRATION = ["provider", "client_id", "client_secret", "tenant_id"];

export class ProviderVerifyDialog extends Component {
    static template = "pan_mail_pro.ProviderVerifyDialog";
    static components = { Dialog };
    static props = { resId: Number, label: String, close: Function };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        // connecting -> verifying -> verified | unverifiable | rejected
        this.state = useState({ phase: "connecting", message: "" });
        onWillStart(() => {
            // Not awaited: the dialog opens on "connecting" and moves on.
            this.verify();
        });
    }

    async verify() {
        const beat = setTimeout(() => {
            if (this.state.phase === "connecting") {
                this.state.phase = "verifying";
            }
        }, 600);
        try {
            const result = await this.orm.call(
                "pan.mail.provider", "verify_registration", [[this.props.resId]]);
            this.state.message = result.message;
            this.state.phase = result.verified === null ? "unverifiable"
                : result.verified ? "verified" : "rejected";
        } catch (error) {
            this.state.message = error.data?.message || error.message || "";
            this.state.phase = "rejected";
        } finally {
            clearTimeout(beat);
        }
    }

    get busy() {
        return this.state.phase === "connecting" || this.state.phase === "verifying";
    }

    async signIn() {
        const action = await this.orm.call(
            "pan.mail.provider", "action_connect_myself", [[this.props.resId]]);
        this.props.close();
        await this.action.doAction(action);
    }
}

export class ProviderFormController extends FormController {
    setup() {
        super.setup();
        this.dialogService = useService("dialog");
    }

    async onRecordSaved(record, changes) {
        await super.onRecordSaved(...arguments);
        const touched = REGISTRATION.some((name) => name in (changes || {}));
        if (!touched || !record.data.uses_oauth || !record.data.credentials_set) {
            return;
        }
        const field = record.fields.provider;
        const label = (field.selection || []).find(([code]) => code === record.data.provider);
        this.dialogService.add(ProviderVerifyDialog, {
            resId: record.resId,
            label: label ? label[1] : record.data.provider,
        });
    }
}

export const providerFormView = { ...formView, Controller: ProviderFormController };

registry.category("views").add("pan_mail_provider_form", providerFormView);
