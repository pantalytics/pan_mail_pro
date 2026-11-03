/** @odoo-module */
/**
 * The Inbox's tour: how mail and Odoo records are linked, in four steps.
 *
 * Odoo's own tour framework, not an overlay of ours (#393): a tour in the
 * `web_tour.tours` registry, run by `tour_service` in its interactive mode,
 * drawn by its own pointer, and recorded as seen on its own `web_tour.tour`
 * row (`data/web_tour_tour.xml`), whose `user_consumed_ids` is the "once per
 * user". What that framework is in 19.0 decides three things here:
 *
 * - A step moves on when the reader does the thing it points at. There is no
 *   Next button; a step with nothing to click is skipped. So every step points
 *   at a control whose click is harmless and on topic: the record's own head,
 *   Change link, the filter menu, the search box.
 * - The pointer's own "Stop Tour" is the skip. The last step's click is done.
 * - A tour started by hand switches the person's Onboarding on, and a tour
 *   that ends starts the next one of Odoo's own. `start()` switches it back
 *   off for somebody who had it off, so the Inbox does not enrol everybody
 *   who opens it in the CRM and Sales tours.
 *
 * The steps differ by the shape of the screen, which `isActive` reads off the
 * Inbox's root classes. A monitor walks the issue's order. A tablet has the
 * record pane folded, so step one points at the button that swaps it in, and
 * the same button takes the reader back to the conversation after. A
 * phone opens on the list, where the bar is, so it starts with the search
 * bar's two steps and then opens a conversation to reach the record.
 *
 * Linking is for mailbox managers (`pan.mail.routing.log.link_to`), so the
 * step that teaches it is only there for them.
 */
import { markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";
import { tourState } from "@pan_mail_pro/compat/tour";

/** The tour's name, here, in the registry and on its `web_tour.tour` row. */
export const INBOX_TOUR = "pan_mail_pro_inbox_tour";

const MONITOR = ".o_mailpro_inbox:not(.o_mailpro_small)";
const TABLET = ".o_mailpro_inbox.o_mailpro_narrow:not(.o_mailpro_small)";
const PHONE = ".o_mailpro_inbox.o_mailpro_small";
const CAN_LINK = ".o_mailpro_inbox.o_mailpro_can_link";

// A folded pane stays in the DOM at no width, so "visible" is asked of the
// pane rather than of what is inside it.
const RECORD_PANE = ".o_mailpro_odoo_record:not(.o_mailpro_folded)";
const CONVERSATION_PANE = ".o_mailpro_conversation:not(.o_mailpro_folded)";
// The record's divider button: on a monitor it folds the record pane, on a
// tablet it swaps the record and the conversation in the column they share.
const RECORD_TOGGLE = ".o_mailpro_split_odoo_record .o_mailpro_split_toggle";
const ROW_RECORD = ".o_mailpro_item .o_mailpro_chip:not(.o_mailpro_chip_live)";
const FILTER_MENU = ".o_mailpro_topbar .o_searchview_dropdown_toggler";
const SEARCH_BOX = ".o_mailpro_topbar .o_searchview_input";

/**
 * The steps, or none while the Inbox is not on screen.
 *
 * `tour_service` also starts a tour on its own: it resumes the running one
 * when a page loads, for somebody with Onboarding on, and it starts the next
 * one of Odoo's own when one ends. Both happen before the Inbox is mounted, or
 * on another screen altogether, and every step here waits on the Inbox's root
 * classes: the tour would skip all of them and count as done, seen by nobody.
 * With no steps `tour_service` leaves it current and does nothing, and the
 * Inbox starts it once it is on screen (`offer()`).
 */
function steps() {
    if (!document.querySelector(".o_mailpro_inbox")) {
        return [];
    }
    const record = _t("Every conversation sits on an Odoo record. Your reply lands in its chatter.");
    const link = _t("Wrong record? Link it to another one. The rest of the conversation follows.");
    const contact = markup(_t(
        "Mail that matched no record lands on the sender's contact. Find it under <b>On a contact only</b>."
    ));
    const search = _t("Search this folder by sender, subject or a word in the mail.");
    return [
        // A monitor, in the issue's order.
        {
            isActive: [MONITOR],
            // The record pane's head, or, while that pane is folded (always
            // on a tablet, where it waits behind the conversation), the
            // button that brings it in. Never both at once: an element that
            // matched twice would take one click for two steps. Never the
            // open row either: clicking it reads the conversation again, and
            // a tour whose next anchor blinks out while it loads steps back.
            trigger: `${RECORD_PANE} .o_mailpro_odoo_record_head, .o_mailpro_inbox:has(.o_mailpro_odoo_record.o_mailpro_folded) ${RECORD_TOGGLE}`,
            content: record,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            // A tablet has just swapped the record in; the same button gives
            // the column back to the conversation, where the link is.
            isActive: [TABLET, CAN_LINK],
            trigger: `.o_mailpro_inbox:has(.o_mailpro_conversation.o_mailpro_folded) ${RECORD_TOGGLE}`,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            isActive: [MONITOR, CAN_LINK],
            trigger: `${CONVERSATION_PANE} .o_mailpro_chips .o_mailpro_relink_toggle`,
            content: link,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            isActive: [MONITOR],
            trigger: FILTER_MENU,
            content: contact,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            isActive: [MONITOR],
            trigger: SEARCH_BOX,
            content: search,
            tooltipPosition: "bottom",
            run: "click",
        },
        // A phone opens on the list: the bar first, then into a conversation
        // and over to its record, where the link lives on a phone. The search
        // box before the filters, because on a phone Odoo opens the filter
        // menu when the box is tapped: the next step then points at the
        // caret with On a contact only in sight, and its tap closes the menu
        // over the list.
        {
            isActive: [PHONE],
            trigger: SEARCH_BOX,
            content: search,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            isActive: [PHONE],
            trigger: FILTER_MENU,
            content: contact,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            isActive: [PHONE],
            trigger: `.o_mailpro_conversation_list ${ROW_RECORD}`,
            content: record,
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            // The way over to the record. Nothing to say: the next step does.
            isActive: [PHONE, CAN_LINK],
            trigger: ".o_mailpro_odoo_record_button",
            tooltipPosition: "left",
            run: "click",
        },
        {
            isActive: [PHONE, CAN_LINK],
            trigger: `${RECORD_PANE} .o_mailpro_chips .o_mailpro_relink_toggle`,
            content: link,
            tooltipPosition: "bottom",
            run: "click",
        },
    ];
}

registry.category("web_tour.tours").add(INBOX_TOUR, {
    // Where Settings > Technical > Tours starts it from.
    url: "/odoo/action-pan_mail_pro.action_pan_mail_conversation",
    steps,
});

// Whether this page is running the tour. Module state rather than the
// component's: the tour outlives an Inbox that is left and opened again
// without a reload. `tourState` alone cannot say it, because it is
// localStorage and another tab writes it too, and because a tour
// `tour_service` started before the Inbox was on screen is current there and
// running nowhere (`steps()`).
let running = false;

function runningHere() {
    return running && tourState.getCurrentTour() === INBOX_TOUR;
}

/**
 * `offer()` once the Inbox has mail on screen; `start()` from the "?";
 * `take()` from Settings' Take the tour.
 */
export function useInboxTour() {
    const orm = useService("orm");
    const tours = useService("tour_service");
    let offered = false;

    async function start() {
        if (runningHere()) {
            return; // On screen already: a second run would fight it for the pointer.
        }
        running = true;
        await tours.startTour(INBOX_TOUR, { mode: "manual", redirect: false });
        if (!session.tour_enabled) {
            await orm.call("res.users", "switch_tour_enabled", [false]);
        }
    }

    /**
     * The first time this person sees an Inbox with mail in it. Recorded as
     * seen when it starts, not when it ends: Stop Tour is a skip, and a tour
     * that came back after a skip would not be a skip.
     *
     * Also the tour `tour_service` started while the Inbox was not on screen:
     * Odoo's Onboarding reaching it, a reload in the middle of it, or
     * Settings > Technical > Tours. It is current and waiting for this.
     */
    async function offer() {
        if (offered) {
            return;
        }
        offered = true;
        if (runningHere()) {
            return;
        }
        if (tourState.getCurrentTour() === INBOX_TOUR) {
            await orm.call("web_tour.tour", "consume", [INBOX_TOUR]);
            await start();
            return;
        }
        const seen = await orm.searchCount("web_tour.tour", [
            ["name", "=", INBOX_TOUR],
            ["user_consumed_ids", "in", [user.userId]],
        ]);
        if (seen) {
            return;
        }
        await orm.call("web_tour.tour", "consume", [INBOX_TOUR]);
        await start();
    }

    /**
     * Take the tour, from Settings once Mail Pro is up and running (#391):
     * the "?" pressed from another screen, so it opens on the Inbox and is
     * seen like the first run, and the Inbox does not offer it again.
     */
    async function take() {
        offered = true;
        await orm.call("web_tour.tour", "consume", [INBOX_TOUR]);
        await start();
    }

    /**
     * Whether this tour is the current one, for the Inbox's root class:
     * the panes do not ease while it runs. 20 places its pointer once, at
     * the trigger as it stands when the step opens, so a button that is
     * still sliding (the record swap at 1280px) leaves the pointer in the
     * middle of nowhere; 18 and 19 follow the trigger and lose nothing.
     */
    function touring() {
        return tourState.getCurrentTour() === INBOX_TOUR;
    }

    return { start, offer, take, touring };
}
