# -*- coding: utf-8 -*-
"""Where this Odoo runs, reported on the heartbeat (odoo-mcp-pro-admin#598).

The platform's heartbeat takes an optional `hosting` of `sh`, `cloudpepper`
or `self_hosted` and refuses anything else with a 400, so the detection may
return nothing else, whatever the environment says. Odoo.sh is `ODOO_STAGE`
(measured on a production branch on 2026-11-03), Cloudpepper is the path of
the config file, and a staging or dev branch on Odoo.sh is a copy.
"""
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

from odoo import tools
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils
from odoo.addons.pan_mail_pro.models.hosting import (
    HOSTING_VALUES, host_says_copy, hosting, odoo_sh_stage,
)
from odoo.addons.pan_mail_pro.models.neutralization import (
    database_is_copy, database_sees_a_copy,
)

CLOUDPEPPER_CONF = '/var/odoo/mailpro-dev/odoo.conf'


@contextmanager
def running_on(stage=None, config=None):
    """This process as a host would start it: `ODOO_STAGE` set or not, and
    the config file Odoo was started with."""
    environ = {key: value for key, value in os.environ.items() if key != 'ODOO_STAGE'}
    if stage is not None:
        environ['ODOO_STAGE'] = stage
    real_get = tools.config.get

    def config_get(key, default=None):
        if key == 'config' and config is not None:
            return config
        return real_get(key, default)

    with patch.dict(os.environ, environ, clear=True), \
            patch.object(tools.config, 'get', side_effect=config_get):
        yield


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestHostingDetection(TransactionCase):

    def test_odoo_stage_means_odoo_sh(self):
        for stage in ('production', 'staging', 'dev', 'Production'):
            self.assertEqual(hosting({'ODOO_STAGE': stage}, ''), 'sh', stage)

    def test_odoo_stage_wins_over_a_cloudpepper_path(self):
        self.assertEqual(hosting({'ODOO_STAGE': 'production'}, CLOUDPEPPER_CONF), 'sh')

    def test_cloudpepper_is_its_config_path(self):
        self.assertEqual(hosting({}, CLOUDPEPPER_CONF), 'cloudpepper')
        self.assertEqual(hosting({}, '/var/odoo/acme-staging/odoo.conf'), 'cloudpepper')

    def test_everything_else_is_self_hosted(self):
        for config in ('', '/etc/odoo/odoo.conf', '/root/.odoorc',
                       '/var/odoo/odoo.conf', '/var/odoo/a/b/odoo.conf',
                       '/var/odoo/a/odoo.conf.bak', '/srv/var/odoo/a/odoo.conf'):
            self.assertEqual(hosting({}, config), 'self_hosted', config)
        self.assertEqual(hosting({'ODOO_STAGE': ''}, ''), 'self_hosted')
        self.assertEqual(hosting({'ODOO_STAGE': '  '}, ''), 'self_hosted')

    def test_it_reads_this_process_environment_and_config(self):
        """The defaults are the process's own: `ODOO_STAGE` from `os.environ`
        and the config file from `odoo.tools.config`."""
        with running_on(stage='production'):
            self.assertEqual(hosting(), 'sh')
        with running_on(config=CLOUDPEPPER_CONF):
            self.assertEqual(hosting(), 'cloudpepper')
        with running_on(config='/etc/odoo/odoo.conf'):
            self.assertEqual(hosting(), 'self_hosted')

    def test_only_the_three_words_the_platform_takes(self):
        self.assertEqual(set(HOSTING_VALUES), {'sh', 'cloudpepper', 'self_hosted'})
        for environ, config in (({'ODOO_STAGE': 'something-new'}, ''),
                                ({}, '/var/odoo/x/odoo.conf'),
                                ({}, None), ({}, '/opt/odoo.conf')):
            self.assertIn(hosting(environ, config), HOSTING_VALUES)

    def test_a_staging_or_dev_branch_is_a_copy_and_production_is_not(self):
        self.assertEqual(odoo_sh_stage({'ODOO_STAGE': 'Staging'}), 'staging')
        self.assertIsNone(odoo_sh_stage({}))
        self.assertTrue(host_says_copy({'ODOO_STAGE': 'staging'}))
        self.assertTrue(host_says_copy({'ODOO_STAGE': 'dev'}))
        self.assertFalse(host_says_copy({'ODOO_STAGE': 'production'}))
        self.assertFalse(host_says_copy({}))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestHostingOnTheDatabase(TransactionCase):

    def body(self):
        return self.env['pan.mail.license']._heartbeat_body()

    def test_the_heartbeat_carries_where_it_runs(self):
        for stage, config, expected in (
                ('production', None, 'sh'),
                (None, CLOUDPEPPER_CONF, 'cloudpepper'),
                (None, '/etc/odoo/odoo.conf', 'self_hosted')):
            with running_on(stage=stage, config=config):
                self.assertEqual(self.body()['hosting'], expected)

    def test_hosting_is_one_word_and_nothing_about_the_host(self):
        """The heartbeat is the privacy boundary: no path, no stage name."""
        with running_on(stage='staging', config=CLOUDPEPPER_CONF):
            body = self.body()
        self.assertEqual(body['hosting'], 'sh')
        sent = json.dumps(body)
        self.assertNotIn('mailpro-dev', sent)
        self.assertNotIn('/var/odoo', sent)

    def test_the_heartbeat_carries_the_edition_from_inside(self):
        """`web_enterprise` installed is Enterprise, else Community. CI runs
        the community image, so the Enterprise branch is the module row
        marked installed."""
        self.assertEqual(self.body()['edition'], 'community')
        web_enterprise = self.env['ir.module.module'].search(
            [('name', '=', 'web_enterprise')], limit=1
        ) or self.env['ir.module.module'].create(
            {'name': 'web_enterprise', 'state': 'uninstalled'}
        )
        web_enterprise.state = 'installed'
        self.assertEqual(self.body()['edition'], 'enterprise')

    def test_odoo_sh_staging_makes_the_database_a_copy(self):
        secret = encryption_utils.encrypt_value(self.env, 'refresh-token')
        for stage in ('staging', 'dev'):
            with running_on(stage=stage):
                self.assertTrue(database_sees_a_copy(self.env), stage)
                self.assertTrue(database_is_copy(self.env), stage)
                self.assertIs(encryption_utils.decrypt_value(self.env, secret), False)
                self.assertIs(self.body()['staging'], True)

    def test_odoo_sh_production_is_not_a_copy(self):
        secret = encryption_utils.encrypt_value(self.env, 'refresh-token')
        with running_on(stage='production'):
            self.assertFalse(database_is_copy(self.env))
            self.assertEqual(encryption_utils.decrypt_value(self.env, secret), 'refresh-token')
            self.assertNotIn('staging', self.body())
