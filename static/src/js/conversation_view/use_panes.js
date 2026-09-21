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
 * One control, everywhere: a round button floating on the divider. Beside
 * an open pane it shows a chevron and folds it; beside a folded pane it
 * shows that pane's icon and brings it back. The button moves with the
 * divider, so it is always where the pane was. No button elsewhere says
 * the same thing.
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
 * at a time, with the rail as a drawer. Both come from the browser's own
 * media queries, so a phone turned sideways gets the layout its width earns
 * without a reload.
 *
 * A folded pane is not removed, it is drawn at no width, so folding and
 * unfolding are a transition the stylesheet animates rather than a pane that
 * blinks out. `folded(name)` is the one answer the template asks, whichever
 * of the three shapes decided it; `toggleSide(name)` says on which side of
 * its divider the button sits, and `lead(name)` how many folded panes stand
 * before an open one, so its header can leave room for their buttons.
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
// record, one at a time, and the rail is a drawer over whichever one is
// open. A tablet in portrait is `narrow`, not `small`: three panes fit, the
// fourth does not.
const BREAKPOINTS = { small: 767.98, narrow: 1400 };

// The panes in the order they sit, left to right. `lead()` walks it.
const ORDER = ["rail", "list", "conversation", "record"];

// What a folded pane's button shows: the thing it brings back.
const ICONS = {
    rail: "fa-bars",
    list: "fa-list-ul",
    conversation: "fa-envelope-o",
    record: "fa-cube",
};

// rem. The button's diameter plus the gap that separates two of them when
// neighbouring panes are folded together; the header padding uses the same
// step, so the text starts after the last button.
const TOGGLE_STEP = 2.625;
const TOGGLE_INSET = 0.375;

// px. Minimums are where a pane stops being readable rather than where it
// stops being visible: a folder name that wraps, a subject line with two
// words on it, a form field whose label eats the value.
const PANES = {
    rail: { start: 232, min: 140, max: 380 },
    list: { start: 352, min: 260, max: 620 },
    record: { start: 448, min: 300, max: 720 },
};

// Everything but the conversation folds away. Outlook folds the two outer
// ones; the list goes too, because on a tablet a long mail is worth more
// than the list beside it, and the strip brings the list back in a tap.
const COLLAPSIBLE = ["rail", "list", "record"];

const CONVERSATION_MIN = 360;
const STEP = 16;

function paneLabel(name) {
    return {
        rail: _t("Mailboxes"),
        list: _t("Conversations"),
        conversation: _t("Conversation"),
        record: _t("Record"),
    }[name];
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function defaults() {
    return {
        rail: PANES.rail.start,
        list: PANES.list.start,
        record: PANES.record.start,
        collapsed: { rail: false, list: false, record: false },
        zoom: false,
        // Not stored: they describe the window, not a preference.
        small: false,
        narrow: false,
        railOpen: false,
        // A phone shows the list or the conversation; a tablet the
        // conversation or the record. Three positions, one word.
        stage: "list",
    };
}

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
            const { zoom, small, narrow, railOpen, stage, ...stored } = state;
            browser.localStorage.setItem(KEY, JSON.stringify(stored));
        } catch {
            // A width nobody can store is still a width you can drag today.
        }
    }

    /**
     * Whether a pane is drawn at no width right now. Three shapes, three
     * reasons: a wide screen folds what the chevron folded, a tablet folds
     * whichever of the conversation and the record is not on, and a phone
     * folds the rail until the drawer is asked for.
     */
    function isFolded(name) {
        if (state.zoom) {
            return false;
        }
        if (state.small) {
            return name === "rail" && !state.railOpen;
        }
        if (state.narrow && name === "record") {
            return state.stage !== "record";
        }
        if (state.narrow && name === "conversation") {
            return state.stage === "record";
        }
        return Boolean(state.collapsed[name]);
    }

    /**
     * Which side of a divider is folded, if any. The rail's and the list's
     * dividers have their pane on the left; the record's has the record on
     * the right and the conversation on the left, and on a tablet one of
     * those two is always folded.
     */
    function foldedSide(name) {
        if (state.small || state.zoom) {
            return null;
        }
        if (name === "record") {
            return isFolded("record") ? "right" : isFolded("conversation") ? "left" : null;
        }
        return isFolded(name) ? "left" : null;
    }

    /** The folded panes standing together right before this one. */
    function foldedRun(name) {
        let count = 0;
        for (let i = ORDER.indexOf(name) - 1; i >= 0 && isFolded(ORDER[i]); i--) {
            count++;
        }
        return count;
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
        return name === "record" ? -1 : 1;
    }

    function resize(name, width, total) {
        state[name] = clamp(width, PANES[name].min, ceiling(name, total));
    }

    // The window's shape, kept current by the browser rather than polled.
    // A phone rotated into landscape crosses `small` without a reload, and
    // the drawer must not stay open over a rail that is now a pane.
    const queries = Object.entries(BREAKPOINTS).map(([name, px]) => {
        const query = window.matchMedia(`(max-width: ${px}px)`);
        const apply = () => {
            state[name] = query.matches;
            if (name === "small" && !query.matches) {
                state.railOpen = false;
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

        collapsible(name) {
            return COLLAPSIBLE.includes(name);
        },

        folded(name) {
            return isFolded(name);
        },

        /**
         * Where the button sits: centred on the divider between two open
         * panes, or wholly inside the open neighbour of a folded one, where
         * there is room for it and a finger can find it.
         */
        toggleSide(name) {
            const side = foldedSide(name);
            return side === "left" ? "right" : side === "right" ? "left" : "center";
        },

        /**
         * A button inside the open neighbour, when the pane before it is
         * folded too: each folded pane in the run gets its own place, so
         * two folds do not stack two buttons on one spot.
         */
        toggleStyle(name) {
            if (this.toggleSide(name) !== "right") {
                return "";
            }
            const pane = name === "record" ? "conversation" : name;
            const index = foldedRun(pane);
            return `left: ${TOGGLE_INSET + index * TOGGLE_STEP}rem`;
        },

        /**
         * How many folded panes stand together right before this open one.
         * Their buttons float over its header, which leaves that much room.
         */
        lead(name) {
            if (state.small || state.zoom || isFolded(name)) {
                return 0;
            }
            return foldedRun(name);
        },

        /** The chevron points where the divider is about to go. */
        chevron(name) {
            const folded = isFolded(name);
            const rightwards = name === "record" ? !folded : folded;
            return rightwards ? "fa-chevron-right" : "fa-chevron-left";
        },

        /**
         * A chevron beside an open pane, that pane's own icon beside a
         * folded one: the menu for the mailboxes, the envelope for the
         * conversation, the cube the record chips already wear.
         */
        toggleIcon(name) {
            const side = foldedSide(name);
            if (!side) {
                return this.chevron(name);
            }
            return ICONS[name === "record" && side === "left" ? "conversation" : name];
        },

        /** The pane the button acts on: on a tablet the divider serves two. */
        toggleLabel(name) {
            const side = foldedSide(name);
            if (name === "record" && side === "left") {
                return _t("Show %s", paneLabel("conversation"));
            }
            return isFolded(name)
                ? _t("Show %s", paneLabel(name))
                : _t("Hide %s", paneLabel(name));
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

        /** A divider is a control, and a control answers a keyboard. */
        onKey(name, ev) {
            const collapsible = COLLAPSIBLE.includes(name);
            if ((ev.key === "Enter" || ev.key === " ") && collapsible) {
                ev.preventDefault();
                this.toggle(name);
                return;
            }
            if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") {
                return;
            }
            ev.preventDefault();
            if (foldedSide(name)) {
                return;
            }
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
         * The fold. On a tablet the record has no width of its own to fold:
         * it takes the conversation's column or gives it back, and that is
         * a step, not a preference, so it is not stored.
         */
        toggle(name) {
            if (!COLLAPSIBLE.includes(name)) {
                return;
            }
            if (name === "record" && state.narrow && !state.small) {
                state.stage = state.stage === "record" ? "conversation" : "record";
                return;
            }
            state.collapsed[name] = !state.collapsed[name];
            save();
        },

        /**
         * The rail from the top bar. On a phone that is the drawer; the
         * button is hidden everywhere else, where the divider is the
         * control, but a keyboard or a test may still reach it.
         */
        toggleRail() {
            if (state.small) {
                state.railOpen = !state.railOpen;
            } else {
                this.toggle("rail");
            }
        },

        /** A folder was picked: the drawer has done its job. */
        closeRail() {
            state.railOpen = false;
        },

        /**
         * The conversation: on a phone instead of the list, on a tablet
         * instead of the record. A conversation just picked is the thing
         * to look at, whichever pane had the column before.
         */
        showConversation() {
            state.stage = "conversation";
        },

        showList() {
            state.stage = "list";
        },

        /** Whether a divider has two panes to sit between. */
        splitterVisible(name) {
            return !state.zoom && !state.small;
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
