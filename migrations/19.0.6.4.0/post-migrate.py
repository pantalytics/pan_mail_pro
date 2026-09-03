# -*- coding: utf-8 -*-
"""Retire the internal-mail escape hatch.

19.0.6.4.0 does three things to stored data. It removes both ways of switching
the internal filter off — the global `sync_internal_email` parameter and the
per-mailbox `exclude_internal` boolean — it moves the internal domain list out
of a comma-separated parameter into `pan.mail.domain` rows, and it turns the
`notification` mailbox type into a tick box on the mailbox.
Mail between the company's own domains is never synced. See ARCHITECTURE.md
§9.12 for why.

Two populations need handling, and only one of them is at risk:

* A database that opted out **and never filled the domain list** passed the
  mailbox gate on the opt-out alone. With the opt-out gone that gate now
  refuses, so the admin would meet a validation error the next time they touch
  any mailbox. The list is derived here instead, the same way 19.0.6.2.0
  derives it, from the addresses already in the database.
* A database with mailboxes that had `exclude_internal` unticked simply starts
  filtering. Nothing to repair — the change only ever copies *less* mail into
  Odoo — but it is logged, because somebody chose that setting and deserves to
  read why their team mailbox went quiet.

Odoo drops the `exclude_internal` column itself when the field disappears from
the model, so this script only reads it. It runs before that happens.

Idempotent: deleting a parameter that is already gone is a no-op, and the
domain list is only written when it is empty.

`_domains_become_rows()` has to run before the `opted_out` check below reads
`Domains.is_configured()` — otherwise the table is still empty at that point
regardless of what the old parameter held, and a database with a perfectly
good explicit list gets a derived guess in its place instead of what the
admin actually typed. `_notification_is_a_flag()` has no such ordering
requirement, but runs alongside it for the same reason: a migration script
that defines a step and never calls it is a step that silently does not run.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

PARAM_SYNC_INTERNAL = 'pan_mail_pro.sync_internal_email'
PARAM_DOMAINS = 'pan_mail_pro.internal_domains'


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Domains = env['pan.mail.domain']

    _domains_become_rows(env)
    _notification_is_a_flag(env, cr)

    opted_out = env['ir.config_parameter'].sudo().get_param(
        PARAM_SYNC_INTERNAL) in ('True', 'true', '1')

    if opted_out and not Domains.is_configured():
        suggested = Domains.suggest_domains()
        if suggested:
            Domains.set_domains(suggested)
            _logger.warning(
                "[Mail Pro] Internal email is no longer synced. This database "
                "had opted in and had no domain list, so it was derived: %s. "
                "Check it in Settings — a domain that is missing there is "
                "treated as a customer's.",
                ', '.join(suggested),
            )
        else:
            _logger.error(
                "[Mail Pro] Internal email is no longer synced and no domain "
                "list could be derived. Mailboxes cannot be edited until "
                "Settings → Mail Pro → Internal Domains is filled in."
            )
    elif opted_out:
        _logger.warning(
            "[Mail Pro] Internal email is no longer synced. This database had "
            "opted in; from now on mail between %s is filtered.",
            ', '.join(Domains.get_domains()),
        )

    env['ir.config_parameter'].sudo().search(
        [('key', 'in', (PARAM_SYNC_INTERNAL, PARAM_DOMAINS))]).unlink()

    cr.execute("""
        SELECT count(*) FROM information_schema.columns
         WHERE table_name = 'pan_mail_mailbox' AND column_name = 'exclude_internal'
    """)
    if cr.fetchone()[0]:
        cr.execute("SELECT count(*) FROM pan_mail_mailbox WHERE exclude_internal IS FALSE")
        unticked = cr.fetchone()[0]
        if unticked:
            _logger.warning(
                "[Mail Pro] %s mailbox(es) had 'Exclude Internal' switched off. "
                "They now filter internal mail like every other mailbox.",
                unticked,
            )


def _notification_is_a_flag(env, cr):
    """`mailbox_type = 'notification'` becomes a tick box.

    A notification mailbox was a third kind of mailbox, which forced every rule
    about types to carry an exception and made "personal or shared?" answerable
    with "neither". It is a property of one mailbox now. Those rows become
    personal — they always had an owner and always sent with that owner's
    credentials, which is exactly what personal means.
    """
    cr.execute("""
        SELECT count(*) FROM information_schema.columns
         WHERE table_name = 'pan_mail_mailbox' AND column_name = 'is_notification_mailbox'
    """)
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        UPDATE pan_mail_mailbox
           SET is_notification_mailbox = TRUE, mailbox_type = 'personal'
         WHERE mailbox_type = 'notification'
     RETURNING email
    """)
    moved = [row[0] for row in cr.fetchall()]
    if moved:
        _logger.info(
            "[Mail Pro] Notification mailbox is now a tick box: %s", ', '.join(moved))


def _domains_become_rows(env):
    """The comma-separated setting becomes one row per domain.

    Written before the parameter is deleted, and only when the table is empty,
    so a re-run cannot duplicate the list or undo an edit made since.
    """
    Domain = env['pan.mail.domain']
    if Domain.sudo().search_count([]):
        return
    raw = env['ir.config_parameter'].sudo().get_param(PARAM_DOMAINS, '')
    domains = Domain._parse(raw)
    if not domains:
        return
    Domain.sudo().create([{'name': d} for d in domains])
    _logger.info("[Mail Pro] Internal domains moved into their own table: %s",
                 ', '.join(domains))
