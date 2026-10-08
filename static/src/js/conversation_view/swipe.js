/** @odoo-module */
/**
 * A sideways swipe on a phone pane, the way a phone mail app moves between
 * them: the list is to the left of the conversation and the Odoo record to
 * the right of it, and a finger moves the pane it is on.
 *
 * The pane follows the finger while it moves, and on release either goes
 * where it was pushed or springs back. Going is the caller's: this only says
 * which way and how far, so the slide that finishes the move is the same one
 * a button press gets.
 *
 * Three things are left alone, because something else already owns them:
 * a touch that starts at the edge of the glass (iOS Safari's own back
 * gesture lives there), a touch in something that scrolls sideways itself
 * (a wide table in a mail), and a touch that starts out vertical (that is
 * reading). A direction nobody handles still moves a little, against
 * resistance, so the pane says "nothing that way" rather than going dead.
 */

// px kept for the browser's own edge gesture.
const EDGE = 24;
// px a finger moves before the swipe decides it is sideways or not.
const LOCK = 10;
// Share of the pane's width that commits a slow drag.
const COMMIT = 0.3;
// A flick commits on less: this far, this fast.
const FLICK_PX = 40;
const FLICK_MS = 250;
// How far a direction nobody handles still gives, as a share of the drag.
const RESIST = 0.2;
const SPRING_MS = 200;
// Longer than the slide that finishes a move ($mailpro-fold in the stylesheet).
const SETTLE_MS = 400;

/** Whether something between the touch and the pane scrolls sideways itself. */
function scrollsSideways(target, pane) {
    for (let el = target; el && el !== pane; el = el.parentElement) {
        if (el.scrollWidth > el.clientWidth + 1) {
            const overflow = getComputedStyle(el).overflowX;
            if (overflow === "auto" || overflow === "scroll") {
                return true;
            }
        }
    }
    return false;
}

export class Swipe {
    /**
     * @param {Object} options
     * @param {() => boolean} options.enabled  asked at every touch
     * @param {(dx: number) => void} [options.onRight]  the finger went right
     * @param {(dx: number) => void} [options.onLeft]   the finger went left
     * @param {(dir: "left"|"right") => boolean} [options.can]  whether that
     *        way leads anywhere right now; asked when a swipe starts
     */
    constructor({ enabled, onRight, onLeft, can = () => true }) {
        this.enabled = enabled;
        this.onRight = onRight;
        this.onLeft = onLeft;
        this.can = can;
        this.pane = null;
    }

    /** The handler for that direction, if it leads anywhere right now. */
    handler(dx) {
        const [fn, dir] = dx > 0 ? [this.onRight, "right"] : [this.onLeft, "left"];
        return fn && this.can(dir) ? fn : null;
    }

    start(ev) {
        if (ev.touches.length !== 1 || !this.enabled()) {
            return;
        }
        const touch = ev.touches[0];
        if (touch.clientX < EDGE || touch.clientX > window.innerWidth - EDGE) {
            return;
        }
        if (scrollsSideways(ev.target, ev.currentTarget)) {
            return;
        }
        this.pane = ev.currentTarget;
        this.x0 = touch.clientX;
        this.y0 = touch.clientY;
        this.t0 = Date.now();
        this.dx = 0;
        this.axis = null;
    }

    move(ev) {
        if (!this.pane) {
            return;
        }
        const touch = ev.touches[0];
        const dx = touch.clientX - this.x0;
        const dy = touch.clientY - this.y0;
        if (!this.axis) {
            if (Math.abs(dx) < LOCK && Math.abs(dy) < LOCK) {
                return;
            }
            this.axis = Math.abs(dx) > Math.abs(dy) * 1.5 ? "x" : "y";
            if (this.axis === "y") {
                this.pane = null; // Reading. The scroll is the browser's.
                return;
            }
            this.pane.style.transition = "none";
            this.pane.closest(".o_mailpro_panes")?.classList.add("o_mailpro_swiping");
        }
        ev.preventDefault();
        this.dx = this.handler(dx) ? dx : dx * RESIST;
        this.pane.style.transform = `translateX(${this.dx}px)`;
    }

    end() {
        const pane = this.pane;
        this.pane = null;
        if (!pane || this.axis !== "x") {
            return;
        }
        pane.closest(".o_mailpro_panes")?.classList.remove("o_mailpro_swiping");
        const dx = this.dx;
        const far = Math.abs(dx) > pane.getBoundingClientRect().width * COMMIT;
        const flick = Math.abs(dx) > FLICK_PX && Date.now() - this.t0 < FLICK_MS;
        const go = this.handler(dx);
        if (go && (far || flick)) {
            // The pane stays where the finger left it: the move that follows
            // takes it from there, and putting it back first is a frame of
            // the old screen in between.
            pane.style.transition = "";
            go(dx);
            // A pane that is still here once the next one has slid over it
            // is a pane somebody will come back to: put it where they left it.
            setTimeout(() => {
                if (pane.isConnected) {
                    pane.style.transform = "";
                }
            }, SETTLE_MS);
            return;
        }
        this.springBack(pane);
    }

    /** Back where it was, at the speed a released spring would go. */
    springBack(pane) {
        pane.style.transition = `transform ${SPRING_MS}ms ease-out`;
        pane.style.transform = "";
        setTimeout(() => (pane.style.transition = ""), SPRING_MS);
    }
}
