/** @odoo-module */
/**
 * Help improve Mail Pro: what the Inbox reports, and the recording of it.
 *
 * Off unless the server put `pan_mail_improve` in the session, which it only
 * does when the Pantalytics workspace switched it on, the signed answer named
 * where to send it, and nobody here refused it (`pan.mail.license
 * .improve_active`). Then, and only inside the Inbox: five named events, the
 * errors the screen meets, and a wireframe recording. Every text node, every
 * input and every attribute masked, no network bodies, no IP, a person id
 * that is an HMAC the server minted and nothing stored in the browser. We
 * give up reading the screen; we keep the shape of the interaction.
 *
 * An error is reported as PostHog's `$exception`: its class, its stack (file
 * names and function names of our own bundles) and where in the Inbox it
 * happened. Its message is scrubbed first (`scrubExceptionEvent`): one line,
 * every address and every quoted string gone, because an Odoo error message
 * is written for the person reading it and may name a record or a sender.
 * Odoo's own error dialog keeps the full text; only the shape travels.
 *
 * The SDK is a lazy bundle, so a screen that never records never loads it,
 * and it is told to send to `config.host` -- our own proxy -- never to
 * PostHog. `disable_external_dependency_loading` is what keeps that promise
 * on the SDK's side: the recorder is in the bundle, so nothing is ever
 * fetched from anywhere else.
 */

import { onMounted, onWillUnmount } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { session } from "@web/session";

const BUNDLE = "pan_mail_pro.assets_improve";

// Initialised once per page load, however many times the Inbox is opened:
// posthog-js treats a second `init` as a no-op with a warning, and a second
// recorder over the same page would be two recordings of one session.
let loading = null;
// The sample is rolled once per page load as well, so opening the Inbox
// twice is not a second chance to be recorded.
let sampled = null;

/** What a `capture` may carry: strings from a fixed list, never what a person typed. */
const EVENTS = new Set([
    "inbox_opened", "conversation_opened", "tab_opened", "reply_sent", "conversation_linked",
]);

const EMAIL = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
const QUOTED = /(["'`“‘]).*?(["'`”’])/g;
const MAX_VALUE = 200;

/** An error message with nothing in it that could name a person or a record. */
export function scrubValue(value) {
    const line = String(value ?? "").split("\n")[0];
    return line.replace(EMAIL, "<email>").replace(QUOTED, "$1…$2").slice(0, MAX_VALUE);
}

/**
 * posthog-js `before_send`: every `$exception` leaves with its messages
 * scrubbed; every other event passes untouched. Returning `null` would drop
 * the event, so a shape this does not recognise is still sent, scrubbed of
 * the two message properties the SDK is known to set.
 */
export function scrubExceptionEvent(event) {
    if (!event || event.event !== "$exception" || !event.properties) {
        return event;
    }
    const props = event.properties;
    for (const item of props.$exception_list || []) {
        if (item && "value" in item) {
            item.value = scrubValue(item.value);
        }
    }
    if ("$exception_message" in props) {
        props.$exception_message = scrubValue(props.$exception_message);
    }
    return event;
}

/** The name Odoo gave a server-side error, when there is one, and nothing else of it. */
function odooExceptionName(error) {
    const name = error?.data?.name;
    return typeof name === "string" ? name.slice(0, MAX_VALUE) : undefined;
}

function init(config) {
    if (loading) {
        return loading;
    }
    loading = (async () => {
        await loadBundle(BUNDLE);
        const posthog = window.posthog;
        if (!posthog || !posthog.init) {
            return null;
        }
        posthog.init(config.token, {
            api_host: config.host,
            ui_host: "https://eu.posthog.com",
            // Named events only. Nothing is captured that we did not name.
            autocapture: false,
            capture_pageview: false,
            capture_pageleave: false,
            capture_dead_clicks: false,
            capture_heatmaps: false,
            disable_surveys: true,
            disable_web_experiments: true,
            // The recorder is in this bundle; nothing is fetched from anywhere.
            disable_external_dependency_loading: true,
            // Started by hand when the Inbox mounts, stopped when it unmounts.
            disable_session_recording: true,
            ip: false,
            person_profiles: "never",
            // Errors are reported by hand (`report` below): the SDK's own
            // capture would fetch an extension, and nothing is fetched.
            capture_exceptions: false,
            before_send: scrubExceptionEvent,
            // Nothing lands in the customer's browser storage: a page load is
            // a session, and the id is the server's HMAC, not a cookie.
            persistence: "memory",
            bootstrap: { distinctID: config.user },
            session_recording: {
                maskAllInputs: true,
                maskTextSelector: "*",
                maskAllElementAttributes: true,
                blockSelector: "img, video, canvas, iframe, svg image",
                maskCapturedNetworkRequestFn: () => null,
                recordCrossOriginIframes: false,
            },
        });
        posthog.register({ mailpro_version: config.version || "" });
        return posthog;
    })().catch((error) => {
        // A blocked bundle, an old browser: the Inbox works, nothing records.
        console.debug("[Mail Pro] improve: off", error);
        return null;
    });
    return loading;
}

function rollSample(share) {
    if (sampled === null) {
        sampled = Math.random() < Number(share || 0);
    }
    return sampled;
}

/**
 * The hook the Inbox uses. Returns `{ capture }`; when the session carries no
 * config, `capture` is a no-op and nothing is loaded.
 */
export function useImprove() {
    const config = session.pan_mail_improve;
    if (!config || !config.host || !config.token) {
        return { capture() {}, failed() {} };
    }
    let posthog = null;
    let recording = false;

    const report = (error, where) => {
        if (!posthog || !posthog.captureException) {
            return;
        }
        try {
            const value = error instanceof Error ? error : new Error(scrubValue(error));
            posthog.captureException(value, {
                where,
                odoo_exception: odooExceptionName(error),
            });
        } catch (_) {
            // Reporting an error must never be a second one.
        }
    };
    // What escapes every handler while the Inbox is open: an Owl render that
    // threw, a promise nobody awaited. Odoo's error service shows the dialog;
    // this says it happened.
    const onWindowError = (event) => report(event.error || event.message, "window");
    const onUnhandledRejection = (event) => report(event.reason, "promise");

    let unmounted = false;
    onMounted(async () => {
        posthog = await init(config);
        // The bundle loads lazily. If the Inbox closed while it did, the
        // listeners and the recording below would outlive the screen and
        // nothing would ever remove them: the rest of Odoo would be recorded
        // until the next reload, which is exactly what this file promises not
        // to do.
        if (!posthog || unmounted) {
            return;
        }
        window.addEventListener("error", onWindowError);
        window.addEventListener("unhandledrejection", onUnhandledRejection);
        if (rollSample(config.sample) && !posthog.sessionRecordingStarted?.()) {
            posthog.startSessionRecording();
            recording = true;
        }
        posthog.capture("inbox_opened");
    });

    onWillUnmount(() => {
        unmounted = true;
        window.removeEventListener("error", onWindowError);
        window.removeEventListener("unhandledrejection", onUnhandledRejection);
        if (posthog && recording) {
            // Only the Inbox is recorded. Leaving it ends the recording, even
            // though the SDK stays loaded for the next visit.
            posthog.stopSessionRecording();
            recording = false;
        }
    });

    return {
        capture(event, properties = {}) {
            if (!posthog || !EVENTS.has(event)) {
                return;
            }
            posthog.capture(event, properties);
        },
        /**
         * An error the Inbox caught and turned into a line on the screen. The
         * screen says "could not load"; this says which call, with what, so a
         * failure that one customer sees twice a day is a chart and not a
         * ticket. `where` is a fixed string from the call site.
         */
        failed(where, error) {
            report(error, where);
        },
    };
}
