# -*- coding: utf-8 -*-
"""Code pulled, `-u` not yet run: Odoo keeps working, Mail Pro waits.

The session flags run on every page load. When they read a table the upgrade
has not created yet, the page must still load, with Mail Pro reporting itself
as not connected, and the transaction must stay usable for the rest of the
request.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

FLAGS = ('pan_mail_connect_prompt', 'pan_mail_improve', 'pan_mail_connected')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSessionSchemaBehind(HttpCase):

    @staticmethod
    def _missing_table(model):
        model.env.cr.execute("SELECT id FROM pan_mail_table_not_upgraded_yet")

    def test_a_missing_table_leaves_the_session_loading(self):
        self.authenticate('admin', 'admin')
        License = type(self.env['pan.mail.license'])
        with patch.object(License, 'current', self._missing_table), \
                self.assertLogs('odoo.addons.pan_mail_pro.models.ir_http', 'WARNING'):
            info = self.make_jsonrpc_request('/web/session/get_session_info')
        self.assertEqual(info['uid'], self.env.ref('base.user_admin').id)
        for flag in FLAGS:
            self.assertIs(info[flag], False, flag)

    def test_a_healthy_schema_still_fills_the_flags(self):
        self.authenticate('admin', 'admin')
        info = self.make_jsonrpc_request('/web/session/get_session_info')
        self.assertTrue(set(FLAGS) <= set(info))
