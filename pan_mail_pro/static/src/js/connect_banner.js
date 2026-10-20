/** @odoo-module */

import { Component, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { WebClient } from "@web/webclient/webclient";

// Dismissal lives in sessionStorage rather than a stored field: "not now" is
// not a preference worth a column, and a banner that never comes back is a
// banner that stopped working for everyone who clicked it once by reflex.
const DISMISSED_KEY = "pan_mail_pro.connect_banner_dismissed";

/**
 * "Your mailbox is not connected" -- above every screen, until it is.
 *
 * The server has already decided whether this user should see it
 * (`res.users._pan_mail_should_prompt_connect`); this only draws it. The
 * button is a plain link to the same `/mail_pro/connect` route the invitation
 * email uses, so there is one way into consent rather than two.
 */
export class MailProConnectBanner extends Component {
    static template = "pan_mail_pro.ConnectBanner";
    static props = {};

    setup() {
        this.state = useState({
            visible:
                Boolean(session.pan_mail_connect_prompt) &&
                !browser.sessionStorage.getItem(DISMISSED_KEY),
        });
    }

    onDismiss() {
        this.state.visible = false;
        browser.sessionStorage.setItem(DISMISSED_KEY, "1");
    }
}

// Not `patch(WebClient, { components: ... })`. On Enterprise the class that is
// actually mounted is `WebClientEnterprise`, whose body does
// `static components = { ...WebClient.components, NavBar: EnterpriseNavBar }`.
// That spread runs when the class is defined, and `web_enterprise` sits before
// this module in the bundle -- so the patch lands after the snapshot, Owl
// cannot resolve the tag, and the whole webclient fails to mount: a white
// screen on every Enterprise database (#85).
//
// An instance attribute has no such ordering: `WebClientEnterprise.setup()`
// calls `super.setup()`, and the template reads the attribute at render time
// through `t-component`. Community and Enterprise take the same path.
patch(WebClient.prototype, {
    setup() {
        super.setup();
        this.MailProConnectBanner = MailProConnectBanner;
    },
});
