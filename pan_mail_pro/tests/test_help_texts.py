# -*- coding: utf-8 -*-
"""The tooltips behind the Mail Pro settings stay short (Daniel's test,
2026-11-04): one plain line, no em-dash. A tooltip that needs a paragraph is
a screen that needs fixing, not a longer tooltip."""
from odoo.tests.common import TransactionCase, tagged

MAX_WORDS = 15
FIELDS = {
    'pan.mail.provider': None,
    'pan.mail.domain': None,
    'res.config.settings': ('x_improve_refused', 'x_internal_domain_ids'),
}


@tagged('post_install', '-at_install')
class TestHelpTexts(TransactionCase):

    def _helps(self):
        for model, names in FIELDS.items():
            fields = self.env[model]._fields
            for name in names or fields:
                field = fields.get(name)
                if field is None or getattr(field, '_module', None) != 'pan_mail_pro':
                    continue
                if field.help:
                    yield f'{model}.{name}', field.help

    def test_one_short_line(self):
        helps = list(self._helps())
        self.assertGreater(len(helps), 5, 'the check found no tooltips to read')
        for where, text in helps:
            with self.subTest(field=where):
                self.assertLessEqual(len(text.split()), MAX_WORDS, text)
                self.assertNotIn('—', text)
