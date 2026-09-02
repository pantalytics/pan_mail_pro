# -*- coding: utf-8 -*-
"""The AI seam.

Mirrors tests/test_provider_contract.py, because the seam is deliberately the
same shape. Three properties are the reason the seam exists, and each is
asserted here rather than promised in a docstring:

1. AI is opt-in by data — an unconfigured database behaves as though the
   feature were absent.
2. AI cannot block mail — a backend that raises must leave both sending and
   incoming processing working.
3. AI cannot invent a target — a suggestion naming a record that was never a
   candidate is dropped, not stored.

No network: the null backend is patched to stand in for a real one, which also
exercises the registry that resolves it.
"""
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.ai import pan_mail_ai

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestAIContract(MailProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.ai_backend', 'none')

    def _item(self, **overrides):
        vals = {
            'provider_message_id': 'P1',
            'message_id': '<ai-1@example.com>',
            'mailbox_id': self.personal_mailbox.id,
            'reason': 'unknown_contact',
            'subject': 'Where is my order?',
            'email_from': 'customer@example.com',
            'partner_id': self.external_partner.id,
        }
        vals.update(overrides)
        return self.env['pan.mail.item'].sudo().create(vals)

    # -- the registry ------------------------------------------------------ #

    def test_every_registered_backend_resolves(self):
        for code, model_name in pan_mail_ai.AI_BACKENDS.items():
            self.assertIn(
                model_name, self.env,
                f"backend {code!r} points at a model that does not exist",
            )

    def test_every_backend_implements_the_contract(self):
        for model_name in pan_mail_ai.AI_BACKENDS.values():
            backend = self.env[model_name]
            for method in ('classify', 'is_available'):
                self.assertTrue(
                    hasattr(backend, method),
                    f"{model_name} is missing {method}()",
                )

    def test_selection_and_registry_agree(self):
        self.assertEqual(
            {code for code, _label in pan_mail_ai.AI_SELECTION},
            set(pan_mail_ai.AI_BACKENDS),
            "a backend that can be selected but not resolved is a runtime error",
        )

    def test_unknown_backend_falls_back_to_none(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.ai_backend', 'does-not-exist')
        backend = pan_mail_ai.get_ai_backend(self.env)
        self.assertFalse(backend.is_available())
        self.assertEqual(backend.classify({}), {})

    # -- opt-in by data ---------------------------------------------------- #

    def test_default_backend_is_disabled(self):
        backend = pan_mail_ai.get_ai_backend(self.env)
        self.assertFalse(backend.is_available())

    def test_cron_is_a_no_op_when_disabled(self):
        item = self._item()
        self.assertEqual(self.env['pan.mail.item']._cron_ai_classify(), 0)
        self.assertEqual(item.ai_state, 'todo')

    # -- a suggestion is only ever a suggestion ---------------------------- #

    def _fake_suggestion(self, item):
        """What a backend that ranks the first candidate would return."""
        payload = item._ai_payload()
        first = payload['candidates'][0]
        return self.env['pan.mail.ai'].sudo()._validate_suggestion({
            'suggested_model': first['model'],
            'suggested_res_id': first['id'],
            'confidence': 0.9,
            'rationale': 'because the test says so',
            'backend_model': 'fake-1',
        }, payload)

    def test_suggestion_is_stored_but_nothing_is_filed(self):
        item = self._item()
        item.write(item._ai_values(self._fake_suggestion(item)))

        self.assertEqual(item.ai_state, 'done')
        self.assertEqual(item.ai_suggested_model, 'res.partner')
        self.assertEqual(item.ai_suggested_res_id, self.external_partner.id)
        self.assertEqual(item.state, 'pending', "AI must not change the item state")
        self.assertFalse(item.mail_message_id, "AI must not file anything")

    def test_invented_target_is_dropped(self):
        """The safety property: a backend may rank, never invent."""
        payload = {'candidates': [
            {'model': 'res.partner', 'id': self.external_partner.id, 'name': 'X'},
        ]}
        result = self.env['pan.mail.ai'].sudo()._validate_suggestion({
            'suggested_model': 'res.users',
            'suggested_res_id': 1,
            'confidence': 0.99,
        }, payload)
        self.assertEqual(result, {}, "a non-candidate suggestion must be discarded")

    def test_confidence_is_clamped(self):
        payload = {'candidates': [
            {'model': 'res.partner', 'id': self.external_partner.id, 'name': 'X'},
        ]}
        result = self.env['pan.mail.ai'].sudo()._validate_suggestion({
            'suggested_model': 'res.partner',
            'suggested_res_id': self.external_partner.id,
            'confidence': 42,
        }, payload)
        self.assertEqual(result['confidence'], 1.0)

    def test_candidates_come_from_deterministic_matching(self):
        item = self._item()
        candidates = item._build_candidates()
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]['model'], 'res.partner')
        self.assertEqual(candidates[0]['id'], self.external_partner.id)

    def test_no_partner_means_no_candidates(self):
        item = self._item(message_id='<ai-2@example.com>', partner_id=False)
        self.assertEqual(item._build_candidates(), [])

    # -- AI cannot block mail ---------------------------------------------- #

    def test_a_failing_backend_does_not_break_sending(self):
        def boom(*args, **kwargs):
            raise RuntimeError('AI is down')

        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Quote',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
            'x_send_from_mailbox_id': self.shared_mailbox.id,
        })
        with patch.object(type(self.env['pan.mail.ai.null']), 'classify', boom), \
                self.mock_graph():
            mail.send()

        self.assertEqual(mail.state, 'sent', "a broken AI must not cost a mail")

    def test_a_failing_backend_leaves_the_item_workable(self):
        item = self._item()

        def boom(*args, **kwargs):
            raise RuntimeError('AI is down')

        with patch.object(
            type(self.env['pan.mail.ai.null']), 'is_available', lambda s: True,
        ), patch.object(
            type(self.env['pan.mail.ai.null']), 'classify', boom,
        ):
            self.env['pan.mail.item']._cron_ai_classify()

        item.invalidate_recordset()
        self.assertEqual(item.ai_state, 'failed')
        self.assertEqual(
            item.state, 'pending',
            "a failed suggestion must leave the item as human work, not lose it",
        )
