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
"""


def database_is_neutralized(env):
    """True when this database is a neutralized copy (staging, a test restore)."""
    return bool(env['ir.config_parameter'].sudo().get_param('database.is_neutralized'))
