/** @odoo-module */
/**
 * The reply, in the pane instead of on top of it.
 *
 * A dialog over the Inbox hides the three things somebody looks at while
 * answering: the list, the thread and the record. So the composer takes the
 * conversation pane, the way every mail client does it, and the rest of the
 * screen stays where it was.
 *
 * It is still Odoo's own `mail.compose.message` form -- the Send From
 * dropdown, the templates, the attachments and `message_post` are the ones
 * the chatter uses. Two things have to move for it to live outside a dialog:
 *
 * - **The footer.** `FormController` cuts every `<footer>` out of the arch
 *   and renders it only in a dialog, so inline the arch's Send button and
 *   the paperclip beside it are not drawn at all. The view this hook mounts
 *   is `pan_mail_pro.mail_compose_message_inline_form`, which puts the
 *   paperclip and the template selector back in the body. Send and Discard
 *   do not come with them: the pane is what closes, so the pane owns them.
 * - **The record.** Send saves the composer and then calls it, so this needs
 *   the form's own record. The controller hands itself to the hook through
 *   the env, which is the smallest seam that does not reach into the form
 *   from the outside.
 */

import { useEnv, useState, useSubEnv, onWillDestroy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formView } from "@web/views/form/form_view";

const INLINE_FORM = "pan_mail_pro.mail_compose_message_inline_form";

// mail's own composer view, with a controller that says which record it is
// editing. `mail_composer_form` is the js_class the composer arch itself
// names; the fallback keeps the Inbox working if mail ever renames it.
const COMPOSER_VIEW = registry.category("views").get("mail_composer_form", formView);

class InlineComposerController extends COMPOSER_VIEW.Controller {
    setup() {
        super.setup();
        const handle = this.env.mailproComposer;
        if (!handle) {
            return; // The same view, opened anywhere else. Nothing to tell.
        }
        handle.controller = this;
        onWillDestroy(() => {
            if (handle.controller === this) {
                handle.controller = null;
            }
        });
    }
}

registry.category("views").add("pan_mail_inline_composer_form", {
    ...COMPOSER_VIEW,
    Controller: InlineComposerController,
});

/**
 * @param {Object} options
 * @param {Function} options.onSent called after the mail actually went out
 */
export function useComposer({ onSent }) {
    const env = useEnv();
    const orm = useService("orm");
    const state = useState({ open: false, sending: false });

    // The controller writes itself in here on mount. Not in `state`: it is a
    // component, not a fact about the screen, and nothing renders from it.
    const handle = {};

    // View props built once per reply and never rebuilt: `View` reloads the
    // form when `context` changes, which on a half-written reply would mean
    // losing it.
    let viewProps = null;

    useSubEnv({
        mailproComposer: handle,
        // A composer is not a screen, so it does not get to name one: the
        // form view renames the breadcrumb and the browser tab after the
        // record it holds, and here that is "New". Same guard as the record
        // pane, one level higher.
        config: { ...env.config, setDisplayName: () => {} },
    });

    function close() {
        state.open = false;
        state.sending = false;
        viewProps = null;
        handle.controller = null;
    }

    return {
        state,

        get viewProps() {
            return viewProps;
        },

        /** @param {Object} context the `default_*` values for the reply */
        open(context) {
            viewProps = {
                type: "form",
                resModel: "mail.compose.message",
                resId: false,
                display: { controlPanel: false },
                context: { ...context, form_view_ref: INLINE_FORM },
            };
            state.open = true;
            state.sending = false;
        },

        close,

        /**
         * Save the composer, then send it -- which is what the button in the
         * dialog's footer does, in that order.
         *
         * A save that fails says which field is missing, in the form, next
         * to the field. A send that fails raises, which is Odoo's own error
         * dialog, and leaves the reply open to try again.
         */
        async send() {
            const controller = handle.controller;
            if (!controller || state.sending) {
                return;
            }
            state.sending = true;
            const record = controller.model.root;
            try {
                if (!(await record.save({ reload: false }))) {
                    return;
                }
                await orm.call("mail.compose.message", "action_send_mail", [[record.resId]]);
            } finally {
                state.sending = false;
            }
            close();
            await onSent();
        },
    };
}
