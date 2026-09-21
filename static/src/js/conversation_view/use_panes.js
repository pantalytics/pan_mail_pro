/** @odoo-module */
/**
 * Pane sizing for the Inbox: drag the dividers, fold the three panes around
 * the conversation, and find the screen tomorrow the way you left it tonight.
 *
 * Three stored widths, not four. The conversation is whatever is left over, so it
 * has no width of its own -- it has a floor, and that floor is what a drag
 * runs into instead of eating the mail. It is also the one pane that never
 * folds on its own: a screen with no mail on it is not this screen.
 *
 * One control per pane, at the edge that pane went behind. The mailbox list
 * folds from a button in the top bar, top left, where every mail client has
 * kept the mailboxes since there were mail clients -- and where a phone's
 * drawer has to be, because there the mailbox list is not a pane with a
 * divider at all. The conversation list and the Odoo record fold from a
 * round button on their own divider: a chevron beside the open pane, that
 * pane's icon beside the folded one. A control on the other side of the
 * screen from the pane it folds is a control you hunt for, and nothing else
 * says what either of these says.
 *
 * Widths live in the browser, not the database. It is a per-monitor
 * preference, the same person has a laptop and a desk, and a table for it
 * would have to be read on every open.
 *
 * Zoom is the fourth state and the one that is not stored: the record pane
 * takes the whole screen while you read it, and the next open is the Inbox
 * again. A reading mode you have to remember you left on is a screen that
 * lost its mail.
 *
 * The window's shape is the other thing that is not stored. Below `narrow`
 * the conversation and the record share one column and take turns in it;
 * below `small` the panes stop sitting side by side and the screen shows one
 * at a time, with the mailbox list as a drawer. Both come from the browser's own
 * media queries, so a phone turned sideways gets the layout its width earns
 * without a reload.
 *
 * A folded pane is not removed, it is drawn at no width, so folding and
 * unfolding are a transition the stylesheet animates rather than a pane that
 * blinks out. `folded(name)` is the one answer the template asks, whichever
 * of the three shapes decided it; `toggleSide(name)` says where on its own
 * divider a pane's button sits, and `toggles()` which buttons the top bar
 * carries.
 */

import { onWillDestroy, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const KEY = "pan_mail_pro.panes";

// px. Where the screen changes shape rather than size. Below `narrow` the
// record pane and the conversation share the third column: one of them is
// open, the other is a strip on the divider that a tap swaps in. Below
// `small` -- a phone, and Odoo's own mobile breakpoint -- the panes stop
// sitting side by side at all: the list, then the conversation, then the
// record, one at a time, and the mailbox list is a drawer over whichever one is
// open. A tablet in portrait is `narrow`, not `small`: three panes fit, the
// fourth does not.
const BREAKPOINTS = { small: 767.98, narrow: 1400 };

// What a folded pane's button shows: the thing it brings back. The
// conversation never folds on its own, but a tablet folds it to give the
// record the column they share, and the record's divider brings it back.
const ICONS = {
    mailbox_list: "fa-bars",
    conversation_list: "fa-list-ul",
    conversation: "fa-envelope-o",
    odoo_record: "fa-cube",
};

// px. Minimums are where a pane stops being readable rather than where it
// stops being visible: a folder name that wraps, a subject line with two
// words on it, a form field whose label eats the value.
const PANES = {
    mailbox_list: { start: 232, min: 140, max: 380 },
    conversation_list: { start: 352, min: 260, max: 620 },
    odoo_record: { start: 448, min: 300, max: 720 },
};

// Everything but the conversation folds away. Outlook folds the two outer
// ones; the list goes too, because on a tablet a long mail is worth more
// than the list beside it, and one tap brings it back.
const COLLAPSIBLE = ["mailbox_list", "conversation_list", "odoo_record"];

// Which of them fold from a button on their own divider. Not the mailbox
// list: its divider is the only one a phone does not draw, and the top bar's
// menu icon is already that drawer's control. Two controls for one fold is
// the inconsistency, not two places for two different folds.
const DIVIDER_TOGGLES = ["conversation_list", "odoo_record"];

const CONVERSATION_MIN = 360;
const STEP = 16;

function paneLabel(name) {
    return {
        mailbox_list: _t("Mailboxes"),
        conversation_list: _t("Conversations"),
        conversation: _t("Conversation"),
        odoo_record: _t("Odoo record"),
    }[name];
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function defaults() {
    return {
        mailbox_list: PANES.mailbox_list.start,
        conversation_list: PANES.conversation_list.start,
        odoo_record: PANES.odoo_record.start,
        collapsed: { mailbox_list: false, conversation_list: false, odoo_record: false },
        zoom: false,
        // Not stored: they describe the window, not a preference.
        small: false,
        narrow: false,
        mailboxListOpen: false,
        // A phone shows the list or the conversation; a tablet the
        // conversation or the record. Three positions, one word.
        stage: "conversation_list",
    };
}

// What the panes were called before 19.0.14.1.0. A browser that stored a
// layout under the old names keeps it; the names it wrote are not coming
// back, so this reads them once and never writes them.
const LEGACY = { rail: "mailbox_list", list: "conversation_list", record: "odoo_record" };

/** Stored state is somebody else's data by the time we read it back. */
function restore() {
    const state = defaults();
    let stored;
    try {
        stored = JSON.parse(browser.localStorage.getItem(KEY) || "null");
    } catch {
        return state; // Private window, cleared storage, a half-written value.
    }
    if (!stored || typeof stored !== "object") {
        return state;
    }
    for (const [was, now] of Object.entries(LEGACY)) {
        if (!(now in stored) && was in stored) {
            stored[now] = stored[was];
        }
        if (stored.collapsed && !(now in stored.collapsed) && was in stored.collapsed) {
            stored.collapsed[now] = stored.collapsed[was];
        }
    }
    for (const [name, spec] of Object.entries(PANES)) {
        if (Number.isFinite(stored[name])) {
            state[name] = clamp(stored[name], spec.min, spec.max);
        }
    }
    for (const name of COLLAPSIBLE) {
        state.collapsed[name] = Boolean(stored.collapsed && stored.collapsed[name]);
    }
    return state;
}

export function usePanes() {
    const state = useState(restore());

    function save() {
        try {
            const { zoom, small, narrow, mailboxListOpen, stage, ...stored } = state;
            browser.localStorage.setItem(KEY, JSON.stringify(stored));
        } catch {
            // A width nobody can store is still a width you can drag today.
        }
    }

    /**
     * Whether a pane is drawn at no width right now. Three shapes, three
     * reasons: a wide screen folds what a button folded, a tablet folds
     * whichever of the conversation and the record is not on, and a phone
     * folds the mailbox list until the drawer is asked for.
     */
    function isFolded(name) {
        if (state.zoom) {
            return false;
        }
        if (state.small) {
            return name === "mailbox_list" && !state.mailboxListOpen;
        }
        if (state.narrow && name === "odoo_record") {
            return state.stage !== "odoo_record";
        }
        if (state.narrow && name === "conversation") {
            return state.stage === "odoo_record";
        }
        return Boolean(state.collapsed[name]);
    }

    /**
     * Which side of a divider is folded, if any. The mailbox list's and the list's
     * dividers have their pane on the left; the record's has the record on
     * the right and the conversation on the left, and on a tablet one of
     * those two is always folded.
     */
    function foldedSide(name) {
        if (state.small || state.zoom) {
            return null;
        }
        if (name === "odoo_record") {
            return isFolded("odoo_record") ? "right" : isFolded("conversation") ? "left" : null;
        }
        return isFolded(name) ? "left" : null;
    }

    /** The widest this pane may get before the conversation drops below its floor. */
    function ceiling(name, total) {
        const spec = PANES[name];
        if (!total) {
            return spec.max;
        }
        let others = 0;
        for (const other of Object.keys(PANES)) {
            if (other !== name && !isFolded(other)) {
                others += state[other];
            }
        }
        return clamp(total - others - CONVERSATION_MIN, spec.min, spec.max);
    }

    function containerWidth(handle) {
        const panes = handle.closest(".o_mailpro_panes");
        return panes ? panes.getBoundingClientRect().width : 0;
    }

    // The record pane sits to the right of its divider, so the same gesture
    // means the opposite thing there.
    function direction(name) {
        return name === "odoo_record" ? -1 : 1;
    }

    function resize(name, width, total) {
        state[name] = clamp(width, PANES[name].min, ceiling(name, total));
    }

    // The window's shape, kept current by the browser rather than polled.
    // A phone rotated into landscape crosses `small` without a reload, and
    // the drawer must not stay open over a mailbox list that is now a pane.
    const queries = Object.entries(BREAKPOINTS).map(([name, px]) => {
        const query = window.matchMedia(`(max-width: ${px}px)`);
        const apply = () => {
            state[name] = query.matches;
            if (name === "small" && !query.matches) {
                state.mailboxListOpen = false;
            }
        };
        apply();
        query.addEventListener("change", apply);
        return () => query.removeEventListener("change", apply);
    });
    onWillDestroy(() => queries.forEach((off) => off()));

    return {
        state,
        panes: PANES,

        label(name) {
            return paneLabel(name);
        },

        folded(name) {
            return isFolded(name);
        },

        /**
         * The buttons the top bar carries. One: the mailbox list, which is a
         * pane on a monitor and the drawer on a phone, and folds from the
         * same place in both. The other two fold from their own divider.
         */
        toggles() {
            return ["mailbox_list"];
        },

        /** Whether this divider carries its pane's fold button. */
        collapsible(name) {
            return DIVIDER_TOGGLES.includes(name);
        },

        paneIcon(name) {
            return ICONS[name];
        },

        /**
         * Where that button sits: centred on the divider between two open
         * panes, or wholly inside the open neighbour of a folded one, where
         * there is room for it and a finger can find it.
         */
        toggleSide(name) {
            const side = foldedSide(name);
            return side === "left" ? "right" : side === "right" ? "left" : "center";
        },

        /** The chevron points where the divider is about to go. */
        chevron(name) {
            const folded = isFolded(name);
            const rightwards = name === "odoo_record" ? !folded : folded;
            return rightwards ? "fa-chevron-right" : "fa-chevron-left";
        },

        /**
         * A chevron beside an open pane, that pane's own icon beside a
         * folded one: the list for the conversations, the cube the record
         * chips already wear, and the envelope for a conversation a tablet
         * folded away to give the record their column.
         */
        toggleIcon(name) {
            const side = foldedSide(name);
            if (!side) {
                return this.chevron(name);
            }
            return ICONS[name === "odoo_record" && side === "left" ? "conversation" : name];
        },

        /** The pane the button acts on: on a tablet the divider serves two. */
        toggleLabel(name) {
            if (name === "odoo_record" && foldedSide(name) === "left") {
                return _t("Show %s", paneLabel("conversation"));
            }
            return isFolded(name)
                ? _t("Show %s", paneLabel(name))
                : _t("Hide %s", paneLabel(name));
        },

        /** The mailbox list is a drawer on a phone and a pane everywhere else. */
        togglePane(name) {
            if (name === "mailbox_list") {
                this.toggleMailboxList();
            } else {
                this.toggle(name);
            }
        },

        startDrag(name, ev) {
            if (ev.button !== 0 || foldedSide(name)) {
                return; // Nothing to drag; the button is the control.
            }
            const handle = ev.currentTarget;
            const total = containerWidth(handle);
            const startX = ev.clientX;
            const startWidth = state[name];
            ev.preventDefault();

            // Pointer capture keeps the move events on the handle, so a fast
            // drag that leaves the 5px strip does not drop the gesture and
            // does not need a listener on the document to catch it.
            handle.setPointerCapture?.(ev.pointerId);
            handle.classList.add("o_mailpro_split_dragging");

            const onMove = (move) => {
                resize(name, startWidth + (move.clientX - startX) * direction(name), total);
            };
            const stop = () => {
                handle.removeEventListener("pointermove", onMove);
                handle.removeEventListener("pointerup", stop);
                handle.removeEventListener("pointercancel", stop);
                handle.classList.remove("o_mailpro_split_dragging");
                save();
            };
            handle.addEventListener("pointermove", onMove);
            handle.addEventListener("pointerup", stop);
            handle.addEventListener("pointercancel", stop);
        },

        /** A width is a control, and a control answers a keyboard. */
        onKey(name, ev) {
            if (foldedSide(name)) {
                return; // No width beside a folded pane, so no step to take.
            }
            if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") {
                return;
            }
            ev.preventDefault();
            const step = (ev.key === "ArrowRight" ? STEP : -STEP) * direction(name);
            resize(name, state[name] + step, containerWidth(ev.currentTarget));
            save();
        },

        zoomLabel() {
            return state.zoom ? _t("Back to the Inbox") : _t("Expand");
        },

        /**
         * The record on its own. Nothing else is collapsed, only hidden: the
         * widths are where you left them when you come back.
         */
        toggleZoom() {
            state.zoom = !state.zoom;
        },

        /**
         * The fold, from the button on the pane's divider, or the top bar's
         * for the mailbox list. On a tablet the record has no
         * width of its own to fold: it takes the conversation's column or
         * gives it back, and that is a step, not a preference, so it is not
         * stored.
         */
        toggle(name) {
            if (!COLLAPSIBLE.includes(name)) {
                return;
            }
            if (name === "odoo_record" && state.narrow && !state.small) {
                state.stage = state.stage === "odoo_record" ? "conversation" : "odoo_record";
                return;
            }
            state.collapsed[name] = !state.collapsed[name];
            save();
        },

        /** The mailbox list: a drawer over the screen on a phone, a pane elsewhere. */
        toggleMailboxList() {
            if (state.small) {
                state.mailboxListOpen = !state.mailboxListOpen;
            } else {
                this.toggle("mailbox_list");
            }
        },

        /** A folder was picked: the drawer has done its job. */
        closeMailboxList() {
            state.mailboxListOpen = false;
        },

        /**
         * The conversation: on a phone instead of the list, on a tablet
         * instead of the record. A conversation just picked is the thing
         * to look at, whichever pane had the column before.
         */
        showConversation() {
            state.stage = "conversation";
        },

        showConversationList() {
            state.stage = "conversation_list";
        },

        /**
         * Whether the divider is drawn at all. Between two open panes it is
         * a width to drag; beside a folded one there is no width, so it is
         * drawn only when it carries that pane's button, which is the one
         * way back. The mailbox list's divider carries none and goes.
         */
        splitterVisible(name) {
            if (state.zoom || state.small) {
                return false;
            }
            return !foldedSide(name) || DIVIDER_TOGGLES.includes(name);
        },

        /** Double-click is the way back from a width you regret. */
        reset(name) {
            if (foldedSide(name)) {
                return; // No width to regret; a double tap is two taps.
            }
            state[name] = PANES[name].start;
            state.collapsed[name] = false;
            save();
        },
    };
}
