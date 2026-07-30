# -*- coding: utf-8 -*-
"""Claude implementation of the mail AI contract.

The only file in this module allowed to import `anthropic` or know what a
Claude request looks like. CI enforces that with a grep, the same way the
provider boundary is enforced.

Two deliberate choices:

**Bring your own key.** The customer configures their own API key and the call
goes straight from their Odoo to Anthropic. Pantalytics never proxies it, is
never a sub-processor, and never needs a DPA with every Apps Store buyer — a
one-off licence purchase does not give you that contract. It also means the
data-flow statement in the manifest can be true.

**Haiku, and a shortlist.** The model ranks candidates that deterministic
matching already found; it never searches the database and never invents a
target. That is what keeps this cheap (a fraction of a cent per mail) and what
keeps a wrong answer to "ranked the wrong one of three" rather than "filed a
customer's mail under a stranger".
"""
import json
import logging

from odoo import _, api, models

from ..pan_mail_ai import PROMPT_VERSION, PanMailAIConfigError

_logger = logging.getLogger(__name__)

MODEL = 'claude-haiku-4-5'
MAX_TOKENS = 512
TIMEOUT_SECONDS = 20

SYSTEM_PROMPT = """You triage business email for an ERP system.

You are given one email's envelope (never its body) and a shortlist of Odoo
records it might belong to. The shortlist was built by exact matching on email
address and mail thread; your job is only to rank it.

Rules:
- You may only choose a record from the candidate list. Never invent one.
- If none of the candidates is a good fit, return suggested_model and
  suggested_res_id as null. That is a useful answer, not a failure.
- confidence is your probability that a human would file the mail there.
  Be calibrated: below 0.5 means you are guessing.
- rationale is one short sentence a non-technical user can read."""

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'suggested_model': {'type': ['string', 'null']},
        'suggested_res_id': {'type': ['integer', 'null']},
        'confidence': {'type': 'number'},
        'rationale': {'type': 'string'},
    },
    'required': ['suggested_model', 'suggested_res_id', 'confidence', 'rationale'],
    'additionalProperties': False,
}


class PanMailAIClaude(models.AbstractModel):
    _name = 'pan.mail.ai.claude'
    _inherit = 'pan.mail.ai'
    _description = 'Mail AI (Claude)'

    @api.model
    def _api_key(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'pan_mail_pro.ai_api_key')

    @api.model
    def is_available(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(self._api_key())

    @api.model
    def classify(self, payload):
        """Rank the candidate records for one email.

        Returns `{}` on any failure. A triage suggestion is a convenience; it
        must never be the reason a queue stops being worked.
        """
        try:
            import anthropic
        except ImportError:
            raise PanMailAIConfigError('Claude') from None

        api_key = self._api_key()
        if not api_key:
            raise PanMailAIConfigError('Claude')

        client = anthropic.Anthropic(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                output_config={
                    'format': {'type': 'json_schema', 'schema': RESPONSE_SCHEMA},
                },
                messages=[{'role': 'user', 'content': self._render(payload)}],
            )
        except anthropic.APIStatusError as error:
            _logger.warning('[Mail AI] Claude returned %s: %s',
                            error.status_code, error.message)
            return {}
        except anthropic.APIConnectionError:
            _logger.warning('[Mail AI] Could not reach Claude', exc_info=True)
            return {}

        if response.stop_reason == 'refusal':
            _logger.info('[Mail AI] Claude declined to classify this message')
            return {}

        text = next((b.text for b in response.content if b.type == 'text'), '')
        try:
            suggestion = json.loads(text)
        except ValueError:
            _logger.warning('[Mail AI] Claude returned unparseable output')
            return {}

        suggestion['backend_model'] = MODEL
        return self._validate_suggestion(suggestion, payload)

    @api.model
    def _render(self, payload):
        """Build the user turn.

        Envelope only — no body, no attachments. The security review drew that
        line and it is cheaper as well: subject plus sender is enough to rank a
        shortlist, and it means enabling AI does not send anybody's
        correspondence anywhere.
        """
        lines = [
            _('Email to triage (envelope only):'),
            'subject: %s' % (payload.get('subject') or '(none)'),
            'from: %s' % (payload.get('from') or '(unknown)'),
            'to: %s' % (payload.get('to') or '(unknown)'),
            'date: %s' % (payload.get('date') or '(unknown)'),
            '',
            _('Candidate records:'),
        ]
        candidates = payload.get('candidates') or []
        if not candidates:
            lines.append(_('(none — answer with nulls)'))
        for candidate in candidates:
            lines.append('- model=%s id=%s name=%s (%s)' % (
                candidate['model'], candidate['id'],
                candidate.get('name') or '', candidate.get('why') or '',
            ))
        lines.append('')
        lines.append('prompt_version: %s' % PROMPT_VERSION)
        return '\n'.join(lines)
