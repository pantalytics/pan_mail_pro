# -*- coding: utf-8 -*-
"""Where a mailbox's sync got to, kept off the mailbox row (#473).

The fetch cron is the only writer of these three dates, and it writes them
whenever mail arrives. On `pan_mail_mailbox` that was a write on the row that
every module upgrade alters, and Cloudpepper runs that upgrade next to the
live Odoo: on 26-11 the two met on Juffermans prod and the upgrade rolled
back (#471). #471 made an idle mailbox write once in five minutes; this moves
what the cron writes into a table of its own, so the mailbox row is written
by people only.

One row per mailbox. The mailbox shows the three dates as computed fields
with an inverse, so screens, the rewind on `sync_start_date` and the tests
read and write them as before; the sync itself writes here directly
(`pan.mail.mailbox._set_sync_state`), because a `write()` on the mailbox sets
its `write_date` even when every field in it is computed.
"""
from odoo import fields, models

from ..compat import Constraint

# The dates that live here, under the names the mailbox shows them by.
SYNC_STATE_FIELDS = ('last_sync_date', 'last_sent_sync_date', 'last_check_date')


class PanMailSyncState(models.Model):
    _name = 'pan.mail.sync.state'
    _description = 'Mail Pro Sync State'
    _rec_name = 'mailbox_id'

    mailbox_id = fields.Many2one(
        'pan.mail.mailbox', string='Mailbox', required=True,
        ondelete='cascade', index=True)
    last_sync_date = fields.Datetime(
        string='Newest mail read',
        help='The inbox cursor: the date of the newest message taken from it.')
    last_sent_sync_date = fields.Datetime(
        string='Newest sent mail read',
        help='The Sent folder cursor. Empty until that folder has its own.')
    last_check_date = fields.Datetime(
        string='Last checked',
        help='When a sync run last finished, whether or not it read anything.')

    _mailbox_unique = Constraint(
        'UNIQUE(mailbox_id)', 'A mailbox has one sync state.')
