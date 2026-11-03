# -*- coding: utf-8 -*-
"""
Is this database a copy?

Odoo neutralizes a staging or restored database by deactivating every
`ir_mail_server`, inserting an invalid one, stopping the crons and setting
`database.is_neutralized`. That protection is SMTP-shaped, and Mail Pro is not:
it calls the Graph API, the Gmail API or its own SMTP host with credentials the
dump still carries, so it would pass every check Odoo has while mailing real
customers from the real address.

This one question is asked in two kinds of place, and the difference matters:

- `encryption_utils.decrypt_value` -- the hard gate. Every credential the module
  owns is read through that one function, so refusing there is the whole
  module's off switch, and it covers call sites nobody has written yet.
- The places that can say *why* -- routing an outgoing mail, the sync cron,
  "Sync Now". The gate below them makes the refusal certain; these make it
  readable.

It lives in its own file because it sits below both: encryption does not depend
on the provider contract, and the provider contract does not depend on
encryption, but both depend on this.

**On a copy, mail is shown, not sent** (pan_mail_pro#379). Outgoing mail is
routed as production would route it and handed to Odoo's own mail server, so
the host's catcher shows it (`mail.mail._send_on_staging`); incoming mail is
not read at all (ARCHITECTURE.md §9.9 says why). The settings page and the
Inbox say so in one line: "This is a copy. Mail is shown, not sent."

**A copy can be connected** (pan_mail_pro#384). Connect, Check approval and
the heartbeat run on a copy too, and say so with `staging: true`; the
platform links it as "staging of" a production and bills it nothing. Its
signed answer then says `staging: true` back, and that is the second way
`database_is_copy` answers yes: a database the platform treats as a copy
never sends through a provider and never reads a mailbox, neutralized or
not, because nobody pays for it. The license reads its own two secrets past
the gate (`encryption_utils.decrypt_link_secret`); they reach no mailbox.
"""
import json

# The mailbox a copy's outgoing mail would have left from, on the message the
# catcher shows. `ir.mail_server` reads the From back from it, and it is what
# still names the route when a host's mail server rewrites the From.
COPY_FROM_HEADER = 'X-Mail-Pro-Mailbox'


def database_is_neutralized(env):
    """True when Odoo neutralized this database (staging, a test restore).

    The flag alone. What Mail Pro does differently on a copy asks
    `database_is_copy`, which also hears the platform."""
    return bool(env['ir.config_parameter'].sudo().get_param('database.is_neutralized'))


def entitlement_says_staging(env):
    """True when the last signed answer from Pantalytics says this Odoo is a
    copy (staging, pan_mail_pro#384).

    Read with SQL rather than through the ORM: it is asked on every page load
    and by every decryption, and the ORM would load the whole row, whose
    newer columns may not exist yet on a database that runs the code before
    its `-u` (ir_http's SCHEMA_BEHIND). A database from before the link
    existed has no table at all, and every send decrypts through this: it is
    asked for first, so such a database sends as it did instead of failing on
    a query. Only a verified answer is ever stored in `entitlement_json`
    (`pan.mail.license._untrusted`), so nothing unsigned can say yes.
    """
    env.cr.execute("SELECT to_regclass('pan_mail_license') IS NOT NULL")
    if not env.cr.fetchone()[0]:
        return False
    env['pan.mail.license'].flush_model(['entitlement_json'])
    env.cr.execute('SELECT entitlement_json FROM pan_mail_license ORDER BY id LIMIT 1')
    row = env.cr.fetchone()
    if not row or not row[0]:
        return False
    try:
        payload = json.loads(row[0])
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get('staging') is True


def database_is_copy(env):
    """True when Mail Pro behaves as on a copy: outgoing mail goes to Odoo's
    own mail server, nothing is read, no credential is decrypted.

    Odoo neutralized it, or the platform's signed answer says it is staging.
    The second matters where the first is missing: a copy the platform bills
    nothing must not send as production does, whatever its host left set."""
    return database_is_neutralized(env) or entitlement_says_staging(env)
