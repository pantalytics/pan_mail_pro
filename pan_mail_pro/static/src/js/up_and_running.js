/** @odoo-module */
/**
 * The end of onboarding (#391): "Mail Pro is up and running", once.
 *
 * Drawn on Settings > Mail Pro when the four steps are done and the first
 * incoming mail has landed on a record (`pan.mail.setup.up_and_running`).
 * An amber step 4 is done: it adds step 4's own line under the headline,
 * the way to Mailboxes (#387).
 * The checklist folds under this line; Show setup unfolds it again, held
 * here rather than in a field, so it is a click and never a change for the
 * settings form to save. The stylesheet hides the steps while it is folded.
 *
 * Beside the invite, Take the tour: the Inbox, starting the tour its "?"
 * starts (#393), through the action's context (`pan_mail_tour`).
 *
 * Confetti once per database: the first page that draws the line asks the
 * server to spend the moment (`celebrate_once`), and only that page
 * celebrates. Under prefers-reduced-motion nothing flies and the rocket
 * stands still; the moment is spent all the same.
 */

import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

// The brand accent and the checklist's own dot colours, the platform's
// confetti (odoo-mcp-pro-admin #595) in this page's palette.
const COLOURS = ["#9b99ff", "#1f9d63", "#e8a317", "#e5484d", "#4a78c2"];

function stillMotion() {
    return Boolean(browser.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/**
 * Throw `count` pieces from (x, y) on a layer over the whole page that
 * catches no clicks, and take the layer away once they have landed.
 */
export function confetti(x, y, count, spread) {
    const layer = document.createElement("div");
    layer.className = "o_mailpro_confetti";
    layer.setAttribute("aria-hidden", "true");
    document.body.appendChild(layer);
    let longest = 0;
    for (let i = 0; i < count; i++) {
        const piece = document.createElement("i");
        piece.style.left = `${x}px`;
        piece.style.top = `${y}px`;
        piece.style.background = COLOURS[i % COLOURS.length];
        layer.appendChild(piece);
        const angle = Math.random() * Math.PI * 2;
        const distance = spread * (0.4 + Math.random() * 0.6);
        const duration = 700 + Math.random() * 600;
        longest = Math.max(longest, duration);
        piece.animate(
            [
                { transform: "translate(0, 0) rotate(0deg)", opacity: 1 },
                {
                    transform: `translate(${Math.cos(angle) * distance}px, ${
                        Math.sin(angle) * distance + spread * 0.4
                    }px) rotate(${Math.random() * 720 - 360}deg)`,
                    opacity: 0,
                },
            ],
            { duration, easing: "cubic-bezier(.2, .6, .4, 1)", fill: "forwards" }
        );
    }
    browser.setTimeout(() => layer.remove(), longest + 400);
}

export class UpAndRunning extends Component {
    static template = "pan_mail_pro.UpAndRunning";
    static props = standardWidgetProps;

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.rocket = useRef("rocket");
        this.state = useState({ open: false, launch: false });
        onMounted(() => this.celebrate());
    }

    get unconnected() {
        return this.props.record.data.x_unconnected_count;
    }

    /** Step 4 is amber: a personal mailbox carries the system email (#387). */
    get personalNotification() {
        return this.props.record.data.x_mailboxes_dot === "attention";
    }

    openMailboxes() {
        return this.action.doAction("pan_mail_pro.action_pan_mail_mailbox");
    }

    async celebrate() {
        const first = await this.orm.call("pan.mail.setup", "celebrate_once", []);
        if (!first || stillMotion() || !this.rocket.el) {
            return;
        }
        this.state.launch = true;
        const box = this.rocket.el.getBoundingClientRect();
        confetti(box.left + box.width / 2, box.top + box.height / 2, 60, 220);
        browser.setTimeout(() => {
            confetti(window.innerWidth * 0.2, 140, 30, 140);
            confetti(window.innerWidth * 0.8, 140, 30, 140);
        }, 250);
    }

    async invite() {
        const action = await this.orm.call("pan.mail.setup", "action_invite_colleagues", []);
        await this.action.doAction(action);
    }

    /** The Inbox, starting its tour the way its "?" does (#393). */
    takeTour() {
        return this.action.doAction("pan_mail_pro.action_pan_mail_conversation", {
            additionalContext: { pan_mail_tour: true },
        });
    }

    toggle() {
        this.state.open = !this.state.open;
    }
}

registry.category("view_widgets").add("mailpro_up_and_running", {
    component: UpAndRunning,
    fieldDependencies: [
        { name: "x_unconnected_count", type: "integer" },
        { name: "x_mailboxes_dot", type: "selection" },
    ],
});
