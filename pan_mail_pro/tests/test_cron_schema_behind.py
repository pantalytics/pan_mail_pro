# -*- coding: utf-8 -*-
"""Code pulled, `-u` not yet run: the crons wait and say why (#280).

Cloudpepper and Odoo.sh restart on the new code before (or without) the
module upgrade. A stored field the upgrade has not added yet made every run
of the fetch cron and the heartbeat raise `UndefinedColumn`: a traceback a
minute and no sync, with nothing saying what to do. They now ask the schema
first and log one warning naming the fix.
"""
from unittest.mock import patch

from psycopg2 import errors

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.ir_http import missing_columns

FETCHER_LOG = 'odoo.addons.pan_mail_pro.models.pan_mail_fetcher'
LICENSE_LOG = 'odoo.addons.pan_mail_pro.models.pan_mail_license'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCronSchemaBehind(TransactionCase):

    def _behind(self):
        """The column a release adds, as it is before `-u`: not there.
        Rolled back with the test."""
        self.env.flush_all()
        self.env.cr.execute(
            'ALTER TABLE pan_mail_mailbox RENAME COLUMN sync_enabled TO sync_enabled_before_u')
        self.env.invalidate_all()

    def test_a_healthy_schema_misses_nothing(self):
        self.assertEqual(missing_columns(self.env), [])

    def test_the_missing_column_is_named(self):
        self._behind()
        self.assertIn('pan_mail_mailbox.sync_enabled', missing_columns(self.env))

    def test_the_setup_is_real(self):
        """Without the check, the cron's reads do raise: the case is not
        stood in, it is the one Juffermans and odoo.pantalytics.com had."""
        self._behind()
        # The bad query is the point; caught here so the log stays clean for
        # tools/ci_assert_tests.sh.
        with self.assertRaises(errors.UndefinedColumn), \
                self.assertLogs('odoo.sql_db', 'ERROR'), self.env.cr.savepoint():
            self.env['pan.mail.mailbox'].search_count([('sync_enabled', '=', True)])

    def test_the_fetch_cron_warns_once_and_reads_nothing(self):
        self._behind()
        License = type(self.env['pan.mail.license'])
        with patch.object(License, '_retry_if_stuck') as retry, \
                self.assertLogs(FETCHER_LOG, 'WARNING') as logs:
            self.env['pan.mail.fetcher']._cron_fetch_incoming_mail()
        retry.assert_not_called()
        self.assertEqual(len(logs.output), 1, logs.output)
        self.assertIn('run -u pan_mail_pro', logs.output[0])
        self.assertIn('pan_mail_mailbox.sync_enabled', logs.output[0])

    def test_the_heartbeat_cron_warns_once_and_reads_nothing(self):
        self._behind()
        License = type(self.env['pan.mail.license'])
        with patch.object(License, 'current') as current, \
                self.assertLogs(LICENSE_LOG, 'WARNING') as logs:
            self.env['pan.mail.license']._cron_heartbeat()
        current.assert_not_called()
        self.assertEqual(len(logs.output), 1, logs.output)
        self.assertIn('run -u pan_mail_pro', logs.output[0])


    def test_outgoing_mail_stays_queued(self):
        """#247: the chatter's Send and the mail queue read the mailbox. Behind
        the code, the mail waits in the queue instead of a 500 at the sender,
        and goes once `-u` has run."""
        partner = self.env['res.partner'].create({'name': 'Customer', 'email': 'c@example.test'})
        mail = self.env['mail.mail'].create({
            'subject': 'Quote', 'body_html': '<p>Hi</p>',
            'email_from': 'sales@company.test', 'recipient_ids': [(4, partner.id)],
        })
        self._behind()
        with self.assertLogs('odoo.addons.pan_mail_pro.models.mail_mail', 'WARNING') as logs:
            self.assertTrue(mail.send())
        self.assertIn('run -u pan_mail_pro', logs.output[0])
        self.assertEqual(mail.state, 'outgoing')

# The stored columns this module puts on the models Odoo reads on every
# request (`res.users`: the logged-in user, before any module code runs) or
# on every chatter (`mail.message`, `res.partner`). A column on one of these
# that `-u` has not added yet is a 500 on every page or every record, and no
# savepoint of ours can catch it: the read is Odoo's own (#255, #247).
PER_REQUEST_COLUMNS = {
    'res.users': {'x_default_mailbox_id', 'x_pan_mail_connected',
                  'x_pan_mail_connected_as', 'x_pan_mail_oauth_state'},
    'res.partner': {'x_email_sync_blocked'},
    'mail.message': {'x_account_id', 'x_direction', 'x_email_cc', 'x_email_to',
                     'x_is_read', 'x_mailbox_id', 'x_provider_message_id',
                     'x_provider_thread_id', 'x_res_model_id'},
}


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNoNewPerRequestColumns(TransactionCase):
    """Not a runtime fix, because there is none: a guard on the change.

    A new stored field on `res.users` puts per-user state in a model of its
    own instead (`pan.mail.account` is the example). One on `mail.message` or
    `res.partner` may be worth it, but only with the upgrade run on every
    instance the moment it is deployed: add it to the list on purpose, and
    say so in the release.
    """

    def test_no_new_stored_field_on_a_per_request_model(self):
        new = {}
        for model, allowed in PER_REQUEST_COLUMNS.items():
            found = {name for name, field in self.env[model]._fields.items()
                     if field.store and field.column_type
                     and field._module == 'pan_mail_pro'}
            if found - allowed:
                new[model] = sorted(found - allowed)
        self.assertEqual(
            new, {},
            'A new stored field of pan_mail_pro on a model Odoo reads on every '
            'request: until `-u pan_mail_pro` runs, every such request fails '
            'on the missing column (#255). Put the state in a model of its '
            'own, or add it to PER_REQUEST_COLUMNS knowing every instance '
            'needs the upgrade at once.')
