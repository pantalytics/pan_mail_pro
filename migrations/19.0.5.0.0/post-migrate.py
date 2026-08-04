# -*- coding: utf-8 -*-
"""Clean up after the 19.0.5.0.0 simplification.

Nothing here changes behaviour. It removes state that no longer has anything
reading it, so the database stops carrying columns and parameters whose only
remaining effect would be to confuse whoever opens it next.

Three groups:

1. **The res.users credential proxies.** Tokens have lived on
   `pan.mail.account` since 19.0.2.1.0; these columns were kept as the rollback
   for that release and have been dead ever since. The 19.0.2.1.0 migration
   reads them, but it can only have run before this one.

2. **Fields computed from `x_sync_mode`.** Five booleans describing one
   three-way choice. The choice itself is untouched.

3. **Config parameters for settings that no longer exist** - the overridable
   Microsoft OAuth endpoints (now constants, because they are the same for
   every tenant) and the stored result of the Azure "test configuration"
   round trip (which only ever reported on itself).

`DROP COLUMN IF EXISTS` and `DELETE` are both idempotent, so a retried upgrade
is safe.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

DROPPED_COLUMNS = {
    'res_users': [
        # Credentials now live on pan.mail.account.
        'x_microsoft_access_token_encrypted',
        'x_microsoft_refresh_token_encrypted',
        'x_microsoft_token_expiry',
        # Per-provider connected flags, replaced by x_pan_mail_connected.
        'x_microsoft_oauth_connected',
        'x_google_oauth_connected',
        # Two CSRF nonces, replaced by x_pan_mail_oauth_state.
        'x_microsoft_oauth_state',
        'x_google_oauth_state',
    ],
    'x_microsoft_mailbox': [
        # All five were computed from x_sync_mode, which survives.
        'x_incoming_sync',
        'x_sync_unknown_contacts',
        'x_incoming_enabled',
        'x_sync_inbox',
        'x_sync_sent',
        # Superseded by x_owner_user_id; nothing ever read it.
        'x_incoming_user_id',
    ],
    'pan_mail_account': [
        # The OAuth nonce is written before the account exists, so it never
        # lived here in practice.
        'oauth_state',
    ],
}

DROPPED_PARAMS = [
    # Microsoft's OAuth endpoints are the same for every tenant; they are
    # constants in the Graph client now.
    'x_pan_outlook_pro.auth_url',
    'x_pan_outlook_pro.token_url',
    # The Azure "test configuration" button and its stored verdict are gone:
    # it validated a tenant id, which the consent screen validates properly.
    'x_pan_outlook_pro.config_test_result',
    'x_pan_outlook_pro.config_test_message',
]


def migrate(cr, version):
    _realign_connection_flag(cr)

    for table, columns in DROPPED_COLUMNS.items():
        cr.execute("SELECT to_regclass(%s)", [table])
        if not cr.fetchone()[0]:
            continue
        for column in columns:
            cr.execute(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{column}"')
        _logger.info('[Mail Pro] Dropped %d obsolete column(s) from %s',
                     len(columns), table)

    cr.execute("DELETE FROM ir_config_parameter WHERE key IN %s", (tuple(DROPPED_PARAMS),))
    _logger.info('[Mail Pro] Removed %d obsolete configuration parameter(s)', cr.rowcount)

    # The OAuth wizard's model, view and ACL are deliberately left to Odoo's own
    # stale-data cleanup: it knows which ir_model_data rows this module no
    # longer declares and unlinks the records they point at. Deleting the rows
    # here would only make it forget, leaving the records behind for good.


def _realign_connection_flag(cr):
    """Recompute `res.users.x_pan_mail_connected` from the accounts.

    It is a stored compute, and the 19.0.2.1.0 migration creates accounts with
    raw SQL - Odoo does not recompute a stored field just because rows appeared
    underneath it. Left alone, every user migrated from the res.users columns
    reads as disconnected, the mailbox owner dropdown comes up empty, and
    nothing says why.

    Cheap and idempotent, so it runs unconditionally rather than trying to work
    out whether this particular upgrade path needed it.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    users = env['res.users'].with_context(active_test=False).search([])
    env.add_to_compute(users._fields['x_pan_mail_connected'], users)
    env.flush_all()
    _logger.info('[Mail Pro] Realigned the connection flag for %d user(s)', len(users))
