# -*- coding: utf-8 -*-
"""AI seam for mail triage — deliberately shaped like the provider seam.

`mail_provider_client.py` proved the pattern in this module: one abstract
contract, a registry of implementations, and a rule that nothing outside an
implementation may know how a vendor's API is shaped. This is the same idea
applied to AI, for the same reason — so a second backend, or none at all, is a
data change rather than a code change.

Three properties are load-bearing, and each has a test:

**AI is opt-in by data.** The default backend is `none`, a real implementation
that returns nothing. Exactly like `mail.mail.send()` falling through to SMTP
when no mailbox exists, a database that has not configured AI behaves as though
this file were not here.

**AI cannot block mail.** Nothing here is ever called from `mail.mail.send()` or
from `_process_message()`. Those run in a one-minute cron inside a savepoint,
where a twenty-second model call would stall a mailbox and a failure would roll
the message back. Enrichment is a separate cron over records that already exist.

**AI suggests; a human decides.** The backend returns a suggestion and a
confidence. Nothing in this module acts on one automatically. Auto-routing is
still guarded by the `routing_smart` constraint, and that guard does not move
until there is evidence from real suggestions that it should.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# code -> model name. Same registry shape as PROVIDER_CLIENTS.
AI_BACKENDS = {
    'none': 'pan.mail.ai.null',
    'claude': 'pan.mail.ai.claude',
}

AI_SELECTION = [
    ('none', 'Disabled'),
    ('claude', 'Claude'),
]

DEFAULT_AI_BACKEND = 'none'

# Bumped whenever the prompt changes. Stored on every suggestion, so a change
# in quality can be traced to a change in the prompt rather than guessed at.
PROMPT_VERSION = '1'


def get_ai_backend(env, code=None):
    """Resolve the configured AI backend. Never raises for a missing config."""
    if not code:
        code = env['ir.config_parameter'].sudo().get_param(
            'pan_mail_pro.ai_backend', DEFAULT_AI_BACKEND)
    model_name = AI_BACKENDS.get(code)
    if not model_name or model_name not in env:
        _logger.warning('[Mail AI] Unknown AI backend %r; falling back to none', code)
        model_name = AI_BACKENDS[DEFAULT_AI_BACKEND]
    return env[model_name]


class PanMailAI(models.AbstractModel):
    """The contract every AI backend implements.

    Input and output are normalised dicts, never Odoo records, so a backend
    cannot quietly reach into the ORM and act on something.

    `classify(payload)` takes:
        {'subject': str, 'from': str, 'to': str, 'date': str,
         'candidates': [{'model': str, 'id': int, 'name': str, 'why': str}]}

    and returns either `{}` (no opinion — always valid) or:
        {'suggested_model': str | False,   # must be one of the candidates
         'suggested_res_id': int | False,
         'confidence': float,              # 0.0 - 1.0
         'rationale': str,
         'backend_model': str}             # which model produced this
    """
    _name = 'pan.mail.ai'
    _description = 'Mail AI Backend'

    @api.model
    def is_available(self):
        """Whether this backend can actually be called right now."""
        return False

    @api.model
    def classify(self, payload):
        raise NotImplementedError(
            'AI backends must implement classify(payload)')

    # -- helpers shared by every backend ---------------------------------- #

    @api.model
    def _validate_suggestion(self, suggestion, payload):
        """Reject anything the backend invented.

        The candidate list is built by us from deterministic matching; a
        backend may only rank it. A suggestion naming a record that was not
        offered is dropped rather than stored — that is the difference between
        AI ranking a shortlist and AI choosing where somebody's mail goes.
        """
        if not suggestion:
            return {}
        model = suggestion.get('suggested_model')
        res_id = suggestion.get('suggested_res_id')
        if not model or not res_id:
            return {}
        allowed = {(c['model'], c['id']) for c in payload.get('candidates') or []}
        if (model, int(res_id)) not in allowed:
            _logger.warning(
                '[Mail AI] Backend suggested %s/%s which was not a candidate; dropping',
                model, res_id,
            )
            return {}
        try:
            confidence = float(suggestion.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            'suggested_model': model,
            'suggested_res_id': int(res_id),
            'confidence': max(0.0, min(1.0, confidence)),
            'rationale': (suggestion.get('rationale') or '')[:500],
            'backend_model': suggestion.get('backend_model') or '',
        }


class PanMailAINull(models.AbstractModel):
    """The default: a real backend that has no opinion.

    Not a stub. A database with no AI configured runs this, and every caller
    handles `{}` because this is the implementation they are written against.
    """
    _name = 'pan.mail.ai.null'
    _inherit = 'pan.mail.ai'
    _description = 'Mail AI (disabled)'

    @api.model
    def is_available(self):
        return False

    @api.model
    def classify(self, payload):
        return {}


class PanMailAIConfigError(UserError):
    """Raised by a backend that is selected but not configured."""

    def __init__(self, backend):
        super().__init__(_(
            'The %s AI backend is selected but not configured. '
            'Set its API key in Settings before enabling AI triage.'
        ) % backend)
