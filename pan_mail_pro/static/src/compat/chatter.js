/** @odoo-module */
// The JS half of the series seam (docs/plans/multi-version.md): a name whose
// path moved between Odoo series is re-exported from here, and the module
// imports it from `@pan_mail_pro/compat/...`. This file is the 18 and 19
// path; tools/series/20.0 carries the 20 one.
export { Chatter } from "@mail/chatter/web_portal/chatter";
