# -*- coding: utf-8 -*-
"""The application credentials become rows, one per provider.

Five loose config parameters — `microsoft_client_id`, `microsoft_tenant_id`,
`microsoft_client_secret_encrypted`, `google_client_id`,
`google_client_secret_encrypted` — and the `setup_provider` parameter that
named which one was chosen become `pan.mail.provider` rows, `in_use` standing
in for `setup_provider`. See `models/pan_mail_provider.py` for why: a row per
provider is what lets switching providers keep the one you switch away from.

The secret is copied as the Fernet ciphertext it already is, never decrypted
and re-encrypted — same key, same database, so the ciphertext is portable as
a string and a decrypt/re-encrypt round trip only risks turning it into
garbage for no reason.

Idempotent: a provider with a row already skips creation (so a re-run, or a
database that somehow already has one, is left alone), and deleting a
parameter that is already gone is a no-op.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

PARAM_SETUP_PROVIDER = 'pan_mail_pro.setup_provider'

# provider code -> {new field name: old parameter key}. IMAP has no entry:
# it never had an application-credential parameter, only the (possible)
# choice recorded in PARAM_SETUP_PROVIDER.
CREDENTIAL_PARAMS = {
    'outlook': {
        'client_id': 'pan_mail_pro.microsoft_client_id',
        'client_secret_encrypted': 'pan_mail_pro.microsoft_client_secret_encrypted',
        'tenant_id': 'pan_mail_pro.microsoft_tenant_id',
    },
    'gmail': {
        'client_id': 'pan_mail_pro.google_client_id',
        'client_secret_encrypted': 'pan_mail_pro.google_client_secret_encrypted',
    },
}


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    Provider = env['pan.mail.provider'].sudo()

    active_code = ICP.get_param(PARAM_SETUP_PROVIDER) or False
    created = []

    for code, params in CREDENTIAL_PARAMS.items():
        if Provider.search_count([('provider', '=', code)]):
            continue
        values = {name: ICP.get_param(key) or False for name, key in params.items()}
        if not any(values.values()) and code != active_code:
            continue  # nothing to move, and it was never the chosen one
        Provider.create(dict(values, provider=code, in_use=(code == active_code)))
        created.append(code)

    # IMAP has no credential parameters of its own — the only trace of it
    # having been chosen is `setup_provider`. Without a row here, that choice
    # is lost and the database silently drops back into setup.
    if active_code == 'imap' and not Provider.search_count([('provider', '=', 'imap')]):
        Provider.create({'provider': 'imap', 'in_use': True})
        created.append('imap')

    if created:
        _logger.info(
            "[Mail Pro] Provider credentials moved into their own table: %s. "
            "Active provider: %s.", ', '.join(created), active_code or 'none',
        )

    all_params = [key for params in CREDENTIAL_PARAMS.values() for key in params.values()]
    ICP.search([('key', 'in', all_params + [PARAM_SETUP_PROVIDER])]).unlink()
