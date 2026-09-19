/** @odoo-module */
/**
 * Pane sizing for the Inbox: drag the dividers, collapse the two side panes,
 * and find the screen tomorrow the way you left it tonight.
 *
 * Three stored widths, not four. The thread is whatever is left over, so it
 * has no width of its own -- it has a floor, and that floor is what a drag
 * runs into instead of eating the mail. The same reason the list cannot be
 * collapsed: a screen with no conversations on it is not this screen.
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
 * the record pane steps aside; below `small` the panes stop sitting side by
 * side and the screen shows one at a time, with the rail as a drawer. Both
 * come from the browser's own media queries, so a phone turned sideways
 * gets the layout its width earns without a reload.
 */

import { onWillDestroy, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const KEY = "pan_mail_pro.panes";

// px. Where the screen changes shape rather than size. Below `narrow` the
// record pane steps aside and the thread head offers it on the whole screen
// instead; below `small` -- a phone, and Odoo's own mobile breakpoint -- the
// panes stop sitting side by side at all: the list, then the conversation,
// then the record, one at a time, and the rail is a drawer over whichever one
// is open. A tablet in portrait is `narrow`, not `small`: three panes fit,
// the fourth does not.
const BREAKPOINTS = { small: 767.98, narrow: 1400 };

// px. Minimums are where a pane stops being readable rather than where it
// stops being visible: a folder name that wraps, a subject line with two
// words on it, a form field whose label eats the value.
const PANES = {
    rail: { start: 232, min: 140, max: 380 },
    list: { start: 352, min: 260, max: 620 },
    record: { start: 448, min: 300, max: 720 },
};

// Only the two outer panes fold away. Outlook folds the same two.
const COLLAPSIBLE = ["rail", "record"];

const THREAD_MIN = 360;
const STEP = 16;

function paneLabel(name) {
    return { rail: _t("Mailboxes"), list: _t("Conversations"), record: _t("Record") }[name];
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function defaults() {
    return {
        rail: PANES.rail.start,
        list: PANES.list.start,
        record: PANES.record.start,
        collapsed: { rail: false, record: false },
        zoom: false,
        // Not stored: they describe the window, not a preference.
        small: false,
        narrow: false,
        railOpen: false,
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

    /** The widest this pane may get before the thread drops below its floor. */
    function ceiling(name, total) {
        const spec = PANES[name];
        if (!total) {
            return spec.max;
        }
        let others = 0;
        for (const other of Object.keys(PANES)) {
            if (other !== name && !state.collapsed[other]) {
                others += state[other];
            }
        }
        return clamp(total - others - THREAD_MIN, spec.min, spec.max);
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

        /** The chevron points where the divider is about to go. */
        chevron(name) {
            const folded = state.collapsed[name];
            const rightwards = name === "record" ? !folded : folded;
            return rightwards ? "fa-chevron-right" : "fa-chevron-left";
        },

        toggleLabel(name) {
            return state.collapsed[name]
                ? _t("Show %s", paneLabel(name))
                : _t("Hide %s", paneLabel(name));
        },

        startDrag(name, ev) {
            if (ev.button !== 0 || state.collapsed[name]) {
                return; // Nothing to drag; the chevron is the control.
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
            if (state.collapsed[name]) {
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

        toggle(name) {
            if (!COLLAPSIBLE.includes(name)) {
                return;
            }
            state.collapsed[name] = !state.collapsed[name];
            save();
        },

        /**
         * The rail from the top bar: the drawer on a phone, the fold
         * everywhere else. One button, one meaning -- show me the mailboxes
         * -- and the screen decides what that costs.
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

        /** On a phone, the conversation or the list; elsewhere both. */
        showThread() {
            state.stage = "thread";
        },

        showList() {
            state.stage = "list";
        },

        /** Whether a divider has two panes to sit between. */
        splitterVisible(name) {
            if (state.zoom || state.small) {
                return false;
            }
            return name !== "record" || !state.narrow;
        },

        /** Double-click is the way back from a width you regret. */
        reset(name) {
            state[name] = PANES[name].start;
            state.collapsed[name] = false;
            save();
        },
    };
}
