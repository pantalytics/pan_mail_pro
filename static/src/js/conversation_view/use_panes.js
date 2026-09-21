/** @odoo-module */
/**
 * Pane sizing for the Inbox: drag the dividers, fold the three panes around
 * the conversation, and find the screen tomorrow the way you left it tonight.
 *
 * Three stored widths, not four. The thread is whatever is left over, so it
 * has no width of its own -- it has a floor, and that floor is what a drag
 * runs into instead of eating the mail. It is also the one pane that never
 * folds on its own: a screen with no mail on it is not this screen.
 *
 * One control, one place: a row of round buttons in the top bar, left of New
 * Email, one per pane that folds. Pressed is showing. They sit there rather
 * than on the dividers because a button floating over the conversation's own
 * header is a second menu bar on top of the screen's first one, and because
 * "show me the mailboxes" has been the top left corner of a mail client for
 * thirty years. The dividers are left as what they look like: a width to
 * drag. Nothing else says the same thing.
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
 * of the three shapes decided it, and `toggles()` which buttons the top bar
 * shows for the shape the window is in.
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

// What each pane's button in the top bar shows: the thing it brings back.
// The conversation never folds on its own, so it never wears one.
const ICONS = {
    rail: "fa-bars",
    list: "fa-list-ul",
    thread: "fa-envelope-o",
    record: "fa-cube",
};

// px. Minimums are where a pane stops being readable rather than where it
// stops being visible: a folder name that wraps, a subject line with two
// words on it, a form field whose label eats the value.
const PANES = {
    rail: { start: 232, min: 140, max: 380 },
    list: { start: 352, min: 260, max: 620 },
    record: { start: 448, min: 300, max: 720 },
};

// Everything but the conversation folds away, and the top bar carries one
// button for each, in this order. Outlook folds the two outer ones; the list
// goes too, because on a tablet a long mail is worth more than the list
// beside it, and one tap brings it back.
const COLLAPSIBLE = ["rail", "list", "record"];

const THREAD_MIN = 360;
const STEP = 16;

function paneLabel(name) {
    return {
        rail: _t("Mailboxes"),
        list: _t("Conversations"),
        thread: _t("Conversation"),
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
     * reasons: a wide screen folds what the top bar folded, a tablet folds
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
        if (state.narrow && name === "thread") {
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
            return isFolded("record") ? "right" : isFolded("thread") ? "left" : null;
        }
        return isFolded(name) ? "left" : null;
    }

    /** The widest this pane may get before the thread drops below its floor. */
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

        folded(name) {
            return isFolded(name);
        },

        /**
         * The buttons the top bar carries, left to right. On a phone the
         * panes take turns rather than sit together, so the only one that
         * means anything there is the rail: it is the drawer.
         */
        toggles() {
            return state.small ? ["rail"] : COLLAPSIBLE;
        },

        paneIcon(name) {
            return ICONS[name];
        },

        toggleLabel(name) {
            return isFolded(name)
                ? _t("Show %s", paneLabel(name))
                : _t("Hide %s", paneLabel(name));
        },

        /** The rail is a drawer on a phone and a pane everywhere else. */
        togglePane(name) {
            if (name === "rail") {
                this.toggleRail();
            } else {
                this.toggle(name);
            }
        },

        startDrag(name, ev) {
            if (ev.button !== 0) {
                return;
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
         * The fold, from the top bar's button. On a tablet the record has no
         * width of its own to fold: it takes the conversation's column or
         * gives it back, and that is a step, not a preference, so it is not
         * stored.
         */
        toggle(name) {
            if (!COLLAPSIBLE.includes(name)) {
                return;
            }
            if (name === "record" && state.narrow && !state.small) {
                state.stage = state.stage === "record" ? "thread" : "record";
                return;
            }
            state.collapsed[name] = !state.collapsed[name];
            save();
        },

        /** The rail: a drawer over the screen on a phone, a pane elsewhere. */
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
        showThread() {
            state.stage = "thread";
        },

        showList() {
            state.stage = "list";
        },

        /**
         * Whether a divider has two open panes to sit between. Beside a
         * folded one there is no width to drag, so there is nothing for a
         * col-resize cursor to promise.
         */
        splitterVisible(name) {
            return !state.zoom && !state.small && !foldedSide(name);
        },

        /** Double-click is the way back from a width you regret. */
        reset(name) {
            state[name] = PANES[name].start;
            state.collapsed[name] = false;
            save();
        },
    };
}
