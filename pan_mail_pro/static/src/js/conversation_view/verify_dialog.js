/** @odoo-module */
/**
 * Confirm it's you, for a mailbox with no sign-in screen.
 *
 * An IMAP mailbox has no provider page to be sent to, so the proof is the
 * mailbox password, typed here and tried against the server once
 * (`verify_mailbox_password`). It is never compared with what Odoo stores:
 * a stolen Odoo session, or a hoster's "log in as", holds the stored one and
 * must not be able to answer with it. A wrong password is a line under the
 * field, not an error dialog; the password itself goes nowhere else.
 */

import { Component, onMounted } from "@odoo/owl";
import { useState, useRef, useProps } from "@pan_mail_pro/compat/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { defineProps } from "@pan_mail_pro/compat/owl";

export class VerifyDialog extends Component {
    static template = "pan_mail_pro.VerifyDialog";
    static components = { Dialog };
    static propsSchema = {
        // The address whose password is asked for, named on the field.
        email: String,
        // (password) => Promise<boolean>: the one call that tries it.
        verify: Function,
        // Called once, after a yes, before the dialog closes.
        onVerified: Function,
        close: Function,
    };

    setup() {
        this.props = useProps();
        this.inputRef = useRef("inputRef");
        this.state = useState({ busy: false, wrong: false, error: "" });
        onMounted(() => this.inputRef.el?.focus());
    }

    get title() {
        return _t("Confirm it's you");
    }

    async confirm() {
        const password = this.inputRef.el?.value || "";
        if (!password || this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.wrong = false;
        this.state.error = "";
        try {
            const ok = await this.props.verify(password);
            if (ok) {
                await this.props.onVerified();
                this.props.close();
                return;
            }
            this.state.wrong = true;
            this.inputRef.el?.focus();
        } catch (error) {
            // The server's own sentence: the mailbox is not one this can
            // confirm, or the mail server could not be reached.
            this.state.error = error?.data?.message || error?.message || _t("Something went wrong.");
        } finally {
            this.state.busy = false;
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }
}
defineProps(VerifyDialog);
