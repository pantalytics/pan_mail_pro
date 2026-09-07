# -*- coding: utf-8 -*-
"""The application credentials become a row.

Five loose config parameters — `microsoft_client_id`, `microsoft_tenant_id`,
`microsoft_client_secret_encrypted`, `google_client_id`,
`google_client_secret_encrypted` — and the `setup_provider` parameter that
named which one was chosen become one `pan.mail.provider` row: the provider
this database is set up for.

It created a row per provider when it shipped, with `in_use` naming the
chosen one. 19.0.7.2.0 dropped that toggle — a database runs on one provider
— so this script now creates only the chosen provider's row, and a second
provider's registration is dropped with its parameters. It is recoverable
from that provider's console, which is where the secret came from.

The secret is copied as the Fernet ciphertext it already is, never decrypted
and re-encrypted — same key, same database, so the ciphertext is portable as
a string and a decrypt/re-encrypt round trip only risks turning it into
garbage for no reason.

Idempotent: a database that already has a provider row is left alone, and
deleting a parameter that is already gone is a no-op.
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

    def credentials_for(code):
        params = CREDENTIAL_PARAMS.get(code, {})
        return {name: ICP.get_param(key) or False for name, key in params.items()}

    def has_credentials(code):
        return any(credentials_for(code).values())

    # `setup_provider` only exists in databases that went through the setup
    # flow. One that predates it carries its credentials and nothing saying
    # which provider they belong to — but one set of credentials is not
    # ambiguous: it is the provider this database is set up for.
    code = active_code
    if not code:
        configured = [c for c in CREDENTIAL_PARAMS if has_credentials(c)]
        code = configured[0] if len(configured) == 1 else False

    dropped = [c for c in CREDENTIAL_PARAMS if c != code and has_credentials(c)]

    if code and not Provider.search_count([]):
        # IMAP has no credential parameters of its own — the only trace of it
        # having been chosen is `setup_provider`. Without a row here, that
        # choice is lost and the database silently drops back into setup.
        Provider.create(dict(credentials_for(code), provider=code))
        _logger.info(
            "[Mail Pro] Application credentials moved into their own table. "
            "Provider: %s.", code,
        )

    if dropped:
        _logger.info(
            "[Mail Pro] Registration dropped for %s: Mail Pro runs on one "
            "provider (%s). Re-enter it from that provider's console if you "
            "switch.", ', '.join(dropped), code or 'none',
        )

    all_params = [key for params in CREDENTIAL_PARAMS.values() for key in params.values()]
    ICP.search([('key', 'in', all_params + [PARAM_SETUP_PROVIDER])]).unlink()
