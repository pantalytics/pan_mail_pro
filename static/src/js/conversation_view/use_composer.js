/** @odoo-module */
/**
 * The reply, in the pane instead of on top of it.
 *
 * A dialog over the Inbox hides the three things somebody looks at while
 * answering: the list, the conversation and the record. So the composer takes the
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
 * @param {Function} options.onDraftSaved called with the stored draft's row
 */
export function useComposer({ onSent, onDraftSaved }) {
    const orm = useService("orm");
    // `mode` is which of the three things is being written: `reply`, `note`
    // or `new`. The button label and the pane's head read it; the composer,
    // the save and the post are the same, and what separates them is the
    // subtype and the record in the context.
    //
    // `draftId` is the stored draft this composer came out of, if it came out
    // of one. Saving again writes that row rather than a second one, and
    // sending deletes it: a draft that survives the mail it became is the
    // Drafts folder nobody trusts.
    const state = useState({
        open: false, sending: false, saving: false, mode: "reply", draftId: null,
    });

    // Where the form's controller leaves itself on mount. Not in `state`: it
    // is a component, not a fact about the screen, and nothing renders it.
    const handle = {};

    function close() {
        state.open = false;
        state.sending = false;
        state.saving = false;
        state.draftId = null;
        handle.controller = null;
        formProps.viewProps = null;
    }

    // One object, never replaced: `View` reloads the form when the props it
    // reads change, and a half-written reply is not worth risking that.
    const formProps = { viewProps: null, handle, close };

    return {
        state,
        formProps,

        /**
         * @param {Object} context the `default_*` values for the message
         * @param {string} [mode] "reply", "note" or "new"; the label only
         * @param {number} [draftId] the stored draft this composer continues
         * @param {number} [resId] a wizard the server already filled in
         *   (a reopened draft). Without one the form starts empty on the
         *   defaults, which is every other way this pane is opened.
         */
        open(context, mode = "reply", draftId = null, resId = false) {
            formProps.viewProps = {
                type: "form",
                resModel: "mail.compose.message",
                resId: resId || false,
                display: { controlPanel: false },
                context: { ...context, form_view_ref: INLINE_FORM },
                // `FormController` calls this the moment it is mounted, to
                // put the record id in the action's state. Its own default is
                // a no-op, but mail's composer controller replaces
                // `defaultProps` wholesale and loses it, and the action
                // service is what supplies it everywhere else -- so mounted
                // by hand it is undefined and the mount ends in an "Oops!"
                // over a composer that otherwise rendered fine. There is no
                // action state here; the Inbox is the screen.
                updateActionState: () => {},
            };
            state.open = true;
            state.sending = false;
            state.saving = false;
            state.mode = mode;
            state.draftId = draftId || null;
        },

        close,

        /**
         * Leaving the composer by going somewhere else, rather than by
         * discarding it: anything typed is kept as a draft.
         *
         * The difference from Discard is the whole point. Discard is somebody
         * saying "throw this away", and it still does; clicking another
         * conversation is not, and losing an answer to that was the one thing
         * this pane did that nobody expected. It is also why there is no
         * autosave on a timer: a draft is written when you leave the mail,
         * once, rather than on every pause in the typing.
         *
         * `isDirty()` rather than `dirty`: it flushes the field changes that
         * have not been notified yet, which on this form is the body -- the
         * field people type in and the one that reports late. A composer
         * nobody touched is not dirty, so opening Reply and changing your
         * mind leaves no row behind.
         *
         * Never on a note, and never a reason to trap somebody on this
         * screen: a save that fails still closes the pane.
         *
         * @returns {Promise<boolean>} whether a draft was stored
         */
        async leave() {
            const controller = handle.controller;
            if (!controller || state.sending || state.mode === "note") {
                close();
                return false;
            }
            const record = controller.model.root;
            let stored = false;
            try {
                if (await record.isDirty() && await record.save({ reload: false })) {
                    await orm.call("pan.mail.draft", "save_from_composer",
                                   [record.resId], { draft_id: state.draftId });
                    stored = true;
                }
            } catch (error) {
                console.warn("[Mail Pro] could not keep the draft", error);
            } finally {
                close();
            }
            return stored;
        },

        /**
         * Put the unsent mail away: save the composer, then store it.
         *
         * The wizard is saved first and the server reads *it*, so a draft is
         * the record that would have been posted -- the same recipients, the
         * same uploaded files -- rather than a second reading of the form in
         * JavaScript. A save that fails says which field is missing, in the
         * form, next to the field, exactly as Send does.
         *
         * The pane closes afterwards. A draft is what you write when you are
         * leaving the mail, and a composer that stays open over a stored copy
         * is two places holding the same words.
         */
        async saveDraft() {
            const controller = handle.controller;
            if (!controller || state.saving || state.sending) {
                return;
            }
            state.saving = true;
            const record = controller.model.root;
            let row = null;
            try {
                if (!(await record.save({ reload: false }))) {
                    return;
                }
                row = await orm.call("pan.mail.draft", "save_from_composer",
                                     [record.resId], { draft_id: state.draftId });
            } finally {
                state.saving = false;
                if (row) {
                    close();
                    await onDraftSaved(row);
                }
            }
        },

        /**
         * Save the composer, then send it -- which is what the button in the
         * dialog's footer does, in that order.
         *
         * A save that fails says which field is missing, in the form, next to
         * the field. A send that fails raises, which is Odoo's own error
         * dialog -- and by then the reply is already posted: the chatter sends
         * after its commit, so the raise reaches the browser with the message
         * in the conversation and the failure on its envelope. Leaving the composer
         * open would let a second Send post the same reply again, so it
         * closes and the conversation refreshes before the dialog shows.
         */
        async send() {
            const controller = handle.controller;
            if (!controller || state.sending) {
                return;
            }
            state.sending = true;
            const record = controller.model.root;
            let sent = false;
            const draftId = state.draftId;
            try {
                if (!(await record.save({ reload: false }))) {
                    return;
                }
                sent = true;
                await orm.call("mail.compose.message", "action_send_mail", [[record.resId]]);
                // The draft became the mail, so it stops being a draft. After
                // the send and not before: a delete that runs first turns a
                // failed send into a lost answer.
                if (draftId) {
                    await orm.call("pan.mail.draft", "discard_draft", [draftId]);
                }
            } finally {
                state.sending = false;
                if (sent) {
                    close();
                    await onSent();
                }
            }
        },
    };
}
