/** @odoo-module */
/**
 * Brave's built-in wallet puts a one-line script into every page it opens,
 * Odoo included. On Brave for iOS that script writes
 * `window.ethereum.selectedAddress` before `window.ethereum` exists, and the
 * TypeError it throws is reported by Odoo's error service as an
 * UncaughtClientError dialog, on every page load, with no setting on the phone
 * that turns it off until a wallet has been created.
 *
 * Odoo already lets third-party script errors pass in silence; this one slips
 * through because an injected inline script reports the page's own URL as its
 * file. So it is swallowed here, by its message, and still logged to the
 * console by the error service. Nothing of ours touches `window.ethereum`, so
 * a match is never one of our errors.
 */

import { registry } from "@web/core/registry";
import { UncaughtClientError } from "@web/core/errors/error_service";

const WALLET_INJECTION = /window\.ethereum|selectedAddress/;

export function swallowWalletInjectionError(env, error, originalError) {
    return (
        error instanceof UncaughtClientError &&
        originalError instanceof Error &&
        WALLET_INJECTION.test(originalError.message || "")
    );
}

registry
    .category("error_handlers")
    .add("pan_mail_pro.walletInjection", swallowWalletInjectionError, { sequence: 1 });
