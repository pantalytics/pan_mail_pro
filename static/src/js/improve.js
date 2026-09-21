/** @odoo-module */
/**
 * Help improve Mail Pro: what the Inbox reports, and the recording of it.
 *
 * Off unless the server put `pan_mail_improve` in the session, which it only
 * does when the Pantalytics workspace switched it on, the signed answer named
 * where to send it, and nobody here refused it (`pan.mail.license
 * .improve_active`). Then, and only inside the Inbox: five named events and a
 * wireframe recording. Every text node, every input and every attribute
 * masked, no network bodies, no IP, a person id that is an HMAC the server
 * minted and nothing stored in the browser. We give up reading the screen; we
 * keep the shape of the interaction.
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
        return { capture() {} };
    }
    let posthog = null;
    let recording = false;

    onMounted(async () => {
        posthog = await init(config);
        if (!posthog) {
            return;
        }
        if (rollSample(config.sample) && !posthog.sessionRecordingStarted?.()) {
            posthog.startSessionRecording();
            recording = true;
        }
        posthog.capture("inbox_opened");
    });

    onWillUnmount(() => {
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
    };
}
