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
 * the chatter uses. Three things have to be arranged for it to live outside a
 * dialog:
 *
 * - **The footer.** `FormController` cuts every `<footer>` out of the arch
 *   and renders it only in a dialog, so inline the arch's Send button and
 *   the paperclip beside it are not drawn at all. The view mounted here is
 *   `pan_mail_pro.mail_compose_message_inline_form`, which puts the paperclip
 *   and the template selector back in the body. Send and Discard do not come
 *   with them: the pane is what closes, so the pane owns them.
 * - **The record.** Send saves the composer and then calls it, so this needs
 *   the form's own record. The controller hands itself to the hook through
 *   the env, which is the smallest seam that does not reach into the form
 *   from the outside.
 * - **A dialog to talk to.** mail's composer form writes to `env.dialogData`
 *   while it mounts, and without one the mount throws before a field renders.
 *   `ComposerForm` provides it, and provides it around the composer rather
 *   than around the Inbox: the record pane mounts a form view too, and it has
 *   no business thinking it is in a dialog.
 */

import { Component, useState, useSubEnv, onWillDestroy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { View } from "@web/views/view";
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

/** The composer form, with the env mail's composer expects around it. */
export class ComposerForm extends Component {
    static template = "pan_mail_pro.ComposerForm";
    static components = { View };
    static props = {
        viewProps: Object,
        handle: Object,
        close: Function,
    };

    setup() {
        useSubEnv({
            // Where the form's controller leaves itself, for Send to find.
            mailproComposer: this.props.handle,
            // mail's composer form writes to `env.dialogData` twice while it
            // mounts: the controller stamps the model on it, the renderer
            // hangs its recipient-sync callback on `dismiss`. Outside a dialog
            // there is nothing to write to, so the mount throws before a
            // single field renders -- in the browser only, with an empty
            // server log. Both writes want somewhere to land more than they
            // want a real dialog; `close` is there for whatever asks to be
            // closed, and what closes here is the pane.
            dialogData: {
                close: () => this.props.close(),
                dismiss: () => this.props.close(),
            },
            // A composer is not a screen, so it does not get to name one: the
            // form view renames the breadcrumb and the browser tab after the
            // record it holds, and here that is "New". Same guard as the
            // record pane, for the same reason.
            config: { ...this.env.config, setDisplayName: () => {} },
        });
    }
}

/**
 * @param {Object} options
 * @param {Function} options.onSent called after the mail actually went out
 */
export function useComposer({ onSent }) {
    const orm = useService("orm");
    const state = useState({ open: false, sending: false });

    // Where the form's controller leaves itself on mount. Not in `state`: it
    // is a component, not a fact about the screen, and nothing renders it.
    const handle = {};

    function close() {
        state.open = false;
        state.sending = false;
        handle.controller = null;
        formProps.viewProps = null;
    }

    // One object, never replaced: `View` reloads the form when the props it
    // reads change, and a half-written reply is not worth risking that.
    const formProps = { viewProps: null, handle, close };

    return {
        state,
        formProps,

        /** @param {Object} context the `default_*` values for the reply */
        open(context) {
            formProps.viewProps = {
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
         * A save that fails says which field is missing, in the form, next to
         * the field. A send that fails raises, which is Odoo's own error
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
