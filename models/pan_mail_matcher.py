# -*- coding: utf-8 -*-
"""
Provider-neutral thread matching: "which Odoo record does this email belong to?"

This is deliberately a separate unit from the fetcher. The fetcher decides
whether a message is worth keeping (dedup, loop guard, block list, sync mode)
and then *delivers* it; deciding where it goes is a different question, it is
the one that goes wrong most visibly, and it is the one worth being able to
test on its own without a provider, an HTTP mock, or a mailbox.

The ladder
----------
Rules run strongest first. The first rule that produces a candidate at or above
`AUTO_ROUTE_CONFIDENCE` wins and the ladder stops. If nothing reaches that bar,
every candidate found along the way is still returned — as a *proposal*, with
`model` left empty so no caller can route on it by accident.

    1. odoo_headers          X-Odoo-Model / X-Odoo-Record-Id      1.0
    2. references            In-Reply-To + the References chain   1.0
    3. thread_link           (provider, mailbox, thread id)       0.9
       thread_link_legacy    unscoped mail.message conversation   0.85
    4. subject_participants  normalised subject + same partner    0.5   proposal

Rules 1 and 2 are RFC 5322, so they work identically on Microsoft 365, Gmail,
plain IMAP, and anything else that speaks email. Rule 3 is the only one that
touches a provider concept, and it treats that concept as a *hint that is only
valid inside one mailbox* — which is what it actually is. Rule 4 never routes
on its own; it exists to hand a candidate set to whatever decides the residue.

Adding an AI tier
-----------------
Nothing here calls a model, and it should stay that way for rules 1-3: a
`References` chain is exact, free and reproducible, and replacing it with a
language model would make a solved problem probabilistic. The genuinely
ambiguous residue — a customer who starts a fresh mail instead of replying, a
known contact with three open tickets — is where a model earns its place, and
it plugs in as one more rule:

    class PanMailMatcherAI(models.AbstractModel):
        _inherit = 'pan.mail.matcher'

        def _match_rules(self):
            return super()._match_rules() + ['_rule_ai']

        def _rule_ai(self, ctx):
            # ctx['candidates'] holds everything the deterministic rules found.
            # Return the same candidate shape; confidence decides whether the
            # caller routes or queues it.
            ...

Because it runs last, it is only ever asked about mail the deterministic rules
could not place, which is what keeps it cheap enough for a one-minute cron.
"""
import logging
import re
from datetime import timedelta

from odoo import models, api, fields

_logger = logging.getLogger(__name__)

# Rule identifiers. Stored in the decision so a log line, and later a UI, can
# say *why* a mail landed where it did.
RULE_ODOO_HEADERS = 'odoo_headers'
RULE_REFERENCES = 'references'
RULE_THREAD_LINK = 'thread_link'
RULE_THREAD_LINK_LEGACY = 'thread_link_legacy'
RULE_SUBJECT_PARTICIPANTS = 'subject_participants'

# At or above this, a caller routes the mail automatically. Below it, the
# decision carries candidates but no target, and the caller falls back to
# creating a record (or, later, to asking a human or a model).
AUTO_ROUTE_CONFIDENCE = 0.8

# How stale a provider thread handle may be before it stops being evidence.
# Microsoft derives conversationId from the conversation topic, so a common
# subject can resurface on an unrelated thread months later; without a bound,
# that reopens a record nobody expected. Overridable per database via the
# ir.config_parameter of the same name.
DEFAULT_THREAD_MAX_AGE_DAYS = 180
# Subject matching is a guess to begin with; keep its window short.
DEFAULT_SUBJECT_MAX_AGE_DAYS = 30

# Reply/forward prefixes, in the languages this module actually meets. The
# optional [12] catches mailing-list counters ("Re[2]: ...").
_REPLY_PREFIX_RE = re.compile(
    r'^\s*(?:(?:re|aw|antw|antwoord|fw|fwd|fwd?ed|vs|sv|res|enc|tr|doorst)'
    r'\s*(?:\[\d+\])?\s*:\s*)+',
    re.IGNORECASE,
)

# RFC 5322 msg-id, as it appears inside In-Reply-To / References.
_MESSAGE_ID_RE = re.compile(r'<[^<>@\s]+@[^<>\s]+>')

# Walking an unbounded References chain on a one-minute cron is not free.
# Twenty hops is far beyond any real thread.
_MAX_REFERENCES = 20


class PanMailMatcher(models.AbstractModel):
    """Decides which Odoo record an incoming email belongs to."""

    _name = 'pan.mail.matcher'
    _description = 'Email Thread Matcher'

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @api.model
    def match(self, message, mailbox=None, partner=None, exclude_models=None):
        """Decide where `message` belongs.

        Args:
            message:        normalized message dict (see mail_provider_client).
                            Only `headers`, `thread_id`, `subject` and `date`
                            are read, so a caller can pass a partial one.
            mailbox:        the mailbox the message was fetched from. Without
                            it, thread-id matching is skipped entirely rather
                            than done unscoped — an unscoped thread id is the
                            bug this model exists to fix.
            partner:        res.partner of the correspondent, if already
                            resolved. Only the subject rule uses it.
            exclude_models: model names that must never be a target. The team
                            routing path passes ('res.partner',) so a reply
                            cannot thread onto contact chatter instead of the
                            ticket.

        Returns:
            dict:
                model:             target model, or False if undecided
                res_id:            target id, or False
                parent_message_id: mail.message id to thread under, or False
                rule:              identifier of the winning rule, or False
                confidence:        0.0 - 1.0
                reason:            one line, safe to log or show
                thread_id:         effective thread id (may be synthesised)
                candidates:        everything considered, best first

            `model` is only ever set when confidence >= AUTO_ROUTE_CONFIDENCE.
            A caller can therefore branch on `model` alone and still be safe.
        """
        headers = {k.lower(): v for k, v in (message.get('headers') or {}).items()}
        ctx = {
            'message': message,
            'headers': headers,
            'mailbox': mailbox,
            'partner': partner,
            'exclude_models': tuple(exclude_models or ()),
            'reference_ids': self._reference_ids(headers),
            'candidates': [],
        }
        ctx['thread_id'] = self._effective_thread_id(message, ctx['reference_ids'])

        for rule_method in self._match_rules():
            try:
                found = getattr(self, rule_method)(ctx) or []
            except Exception:
                # A broken rule must not stop the mail. Drop it and continue
                # down the ladder — a weaker match beats an unhandled traceback
                # in the middle of a cron batch.
                _logger.exception("[Mail Matcher] Rule %s raised, skipping it", rule_method)
                continue
            ctx['candidates'].extend(found)
            if found and found[0]['confidence'] >= AUTO_ROUTE_CONFIDENCE:
                break

        # Stable sort: equal confidence keeps ladder order, so a stronger rule
        # always outranks a weaker one that happened to score the same.
        candidates = sorted(ctx['candidates'], key=lambda c: -c['confidence'])
        best = candidates[0] if candidates else None

        if best and best['confidence'] >= AUTO_ROUTE_CONFIDENCE:
            decision = dict(best, thread_id=ctx['thread_id'], candidates=candidates)
        else:
            decision = {
                'model': False,
                'res_id': False,
                'parent_message_id': False,
                'rule': False,
                'confidence': best['confidence'] if best else 0.0,
                'reason': (
                    'No rule reached the routing threshold (%d proposal(s))'
                    % len(candidates)
                ),
                'thread_id': ctx['thread_id'],
                'candidates': candidates,
            }

        _logger.info("[Mail Matcher] %s", self.describe(decision))
        return decision

    @api.model
    def describe(self, decision):
        """One-line, human-readable summary of a decision. For logs and UI."""
        if decision.get('model'):
            return '%s/%s via %s (%.2f) — %s' % (
                decision['model'], decision['res_id'], decision['rule'],
                decision['confidence'], decision['reason'],
            )
        return 'unmatched — %s' % decision.get('reason', 'no candidates')

    # ------------------------------------------------------------------ #
    # Rule registry
    # ------------------------------------------------------------------ #

    def _match_rules(self):
        """Ordered rule method names, strongest first.

        Override in a subclass to append a rule (see the module docstring on
        adding an AI tier). Order is the contract: a rule may assume every rule
        before it has already failed to settle the question.
        """
        return [
            '_rule_odoo_headers',
            '_rule_references',
            '_rule_thread_link',
            '_rule_subject_participants',
        ]

    # ------------------------------------------------------------------ #
    # Rules
    # ------------------------------------------------------------------ #

    def _rule_odoo_headers(self, ctx):
        """Our own X-Odoo-* headers, when a mail we sent comes back to us.

        Exact by construction. The fetcher's loop guard drops most of these
        before the matcher ever sees them, but a forward or a re-send that
        survives with the headers intact should still land on the right record.
        """
        headers = ctx['headers']
        model = headers.get('x-odoo-model')
        res_id = headers.get('x-odoo-record-id')
        if not model or not res_id:
            return []
        try:
            res_id = int(res_id)
        except (TypeError, ValueError):
            return []
        if not self._is_routable(model, res_id, ctx['exclude_models']):
            return []
        return [self._candidate(
            model, res_id, RULE_ODOO_HEADERS, 1.0,
            'X-Odoo headers point at this record',
        )]

    def _rule_references(self, ctx):
        """In-Reply-To and the full References chain.

        The chain is walked nearest-ancestor first, which is why In-Reply-To is
        prepended and References is reversed: References is ordered oldest to
        newest, and the newest ancestor we recognise is the most specific
        answer. Walking the *whole* chain — not just In-Reply-To — is what
        survives forwards, mailing lists and clients that only set References.

        Portable across every provider, including IMAP, because it reads the
        message itself rather than anything the provider added.
        """
        candidates = []
        for position, message_id in enumerate(ctx['reference_ids']):
            parent = self._resolve_message_id(message_id)
            if not parent or not parent.model or not parent.res_id:
                continue
            if not self._is_routable(parent.model, parent.res_id, ctx['exclude_models']):
                continue
            candidates.append(self._candidate(
                parent.model, parent.res_id, RULE_REFERENCES,
                # The nearest ancestor is the answer; anything further up the
                # chain is corroboration, not a second opinion.
                1.0 if position == 0 else 0.9,
                'Replies to %s' % message_id,
                parent_message=parent,
            ))
            if len(candidates) >= 3:
                break
        return candidates

    def _rule_thread_link(self, ctx):
        """The provider's own thread handle, scoped to the mailbox that saw it.

        Two lookups, in order:

        1. `pan.mail.thread.link`, keyed on (provider, mailbox, thread id).
           This is the correct one and the only one new mail writes.
        2. The legacy `mail.message.x_microsoft_conversation_id` column, for
           databases that threaded on it before this model existed. It carries
           no mailbox, so it can in principle match another mailbox's thread —
           it is kept because dropping it would break threading on existing
           conversations, and it is bounded three ways the original lookup was
           not: newest match instead of oldest, an age limit, and the caller's
           excluded models. It scores below the scoped lookup and will simply
           stop matching as conversations age out.
        """
        thread_id = ctx['thread_id']
        mailbox = ctx['mailbox']
        if not thread_id or not mailbox:
            return []

        cutoff = fields.Datetime.now() - timedelta(
            days=self._max_age_days('thread_max_age_days', DEFAULT_THREAD_MAX_AGE_DAYS)
        )
        candidates = []

        link = self.env['pan.mail.thread.link'].sudo().search([
            ('provider', '=', mailbox.x_provider),
            ('mailbox_id', '=', mailbox.id),
            ('thread_id', '=', thread_id),
            ('last_seen', '>=', cutoff),
        ], limit=1)
        if link and self._is_routable(link.model, link.res_id, ctx['exclude_models']):
            candidates.append(self._candidate(
                link.model, link.res_id, RULE_THREAD_LINK, 0.9,
                'Thread %s is already linked for mailbox %s' % (thread_id, mailbox.email),
                parent_message=link.last_message_id,
            ))
            return candidates

        legacy = self.env['mail.message'].sudo().search([
            ('x_microsoft_conversation_id', '=', thread_id),
            ('model', '!=', False),
            ('res_id', '!=', False),
            ('date', '>=', cutoff),
        # Newest, not oldest: the original implementation took `order='id asc'`
        # and threaded replies onto whatever record first touched the
        # conversation, which is how a reply ends up on a months-old contact
        # chatter post instead of the open ticket.
        ], order='id desc', limit=1)
        if legacy and self._is_routable(legacy.model, legacy.res_id, ctx['exclude_models']):
            candidates.append(self._candidate(
                legacy.model, legacy.res_id, RULE_THREAD_LINK_LEGACY, 0.85,
                'Legacy conversation id %s, most recent message' % thread_id,
                parent_message=legacy,
            ))
        return candidates

    def _rule_subject_participants(self, ctx):
        """Same normalised subject, same correspondent, recent enough.

        A guess, and scored as one: it never reaches the routing threshold, so
        it only ever contributes a proposal. Two different customers writing
        "Factuur" in the same month is exactly the case that makes this unsafe
        to act on alone — and exactly the case a later AI tier is meant to
        settle, using these candidates as its shortlist.
        """
        partner = ctx['partner']
        subject = self._normalize_subject(ctx['message'].get('subject'))
        if not partner or not subject:
            return []

        cutoff = fields.Datetime.now() - timedelta(
            days=self._max_age_days('subject_match_max_age_days', DEFAULT_SUBJECT_MAX_AGE_DAYS)
        )
        messages = self.env['mail.message'].sudo().search([
            ('subject', 'ilike', subject),
            ('model', '!=', False),
            ('res_id', '!=', False),
            ('date', '>=', cutoff),
            '|',
            ('author_id', '=', partner.id),
            ('partner_ids', 'in', partner.id),
        ], order='id desc', limit=20)

        seen = set()
        candidates = []
        for msg in messages:
            # `ilike` is a substring match; the normalised comparison is what
            # actually decides, so "Re: Order 12" and "Order 12" collapse while
            # "Order 123" does not.
            if self._normalize_subject(msg.subject) != subject:
                continue
            key = (msg.model, msg.res_id)
            if key in seen:
                continue
            if not self._is_routable(msg.model, msg.res_id, ctx['exclude_models']):
                continue
            seen.add(key)
            candidates.append(self._candidate(
                msg.model, msg.res_id, RULE_SUBJECT_PARTICIPANTS, 0.5,
                'Same subject "%s" from %s within the matching window'
                % (subject, partner.display_name),
                parent_message=msg,
            ))
            if len(candidates) >= 5:
                break
        return candidates

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @api.model
    def _effective_thread_id(self, message, reference_ids=None):
        """The thread handle to key on, synthesised when the provider has none.

        Microsoft supplies conversationId and Gmail supplies threadId, so for
        those this is just what the provider said. IMAP and SMTP have no such
        concept at all — there the root of the References chain is the closest
        stable equivalent, and it has the useful property of being identical
        for every participant in the thread.
        """
        thread_id = message.get('thread_id')
        if thread_id:
            return thread_id
        if reference_ids is None:
            headers = {k.lower(): v for k, v in (message.get('headers') or {}).items()}
            reference_ids = self._reference_ids(headers)
        # reference_ids is nearest-first; the root is the last entry.
        return reference_ids[-1] if reference_ids else False

    @api.model
    def _reference_ids(self, headers):
        """Message-IDs this mail claims to descend from, nearest ancestor first.

        In-Reply-To names the direct parent, so it leads. References is ordered
        root-first, so it is reversed and appended. Duplicates are dropped while
        keeping the first (nearest) occurrence.
        """
        ordered = []
        ordered.extend(_MESSAGE_ID_RE.findall(headers.get('in-reply-to') or ''))
        ordered.extend(reversed(_MESSAGE_ID_RE.findall(headers.get('references') or '')))

        seen = set()
        unique = []
        for message_id in ordered:
            if message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
            if len(unique) >= _MAX_REFERENCES:
                break
        return unique

    @api.model
    def _resolve_message_id(self, message_id):
        """Find the `mail.message` a Message-ID refers to.

        Three places to look, because an Odoo message can be reachable under
        more than one id: the ref index (every id we ever saw for it), Odoo's
        own `message_id` (set by message_post on import), and the legacy
        `x_microsoft_message_id` column (the wire id of mail we sent before the
        ref index existed).
        """
        if not message_id:
            return self.env['mail.message'].browse()
        message_id = message_id.strip()

        parent = self.env['pan.mail.message.ref'].lookup(message_id)
        if parent:
            return parent

        Message = self.env['mail.message'].sudo()
        parent = Message.search([('message_id', '=', message_id)], order='id desc', limit=1)
        if parent:
            return parent
        return Message.search(
            [('x_microsoft_message_id', '=', message_id)], order='id desc', limit=1)

    @api.model
    def _normalize_subject(self, subject):
        """Strip reply/forward prefixes and collapse whitespace."""
        if not subject:
            return ''
        return ' '.join(_REPLY_PREFIX_RE.sub('', subject).split()).strip()

    @api.model
    def _is_routable(self, model, res_id, exclude_models=()):
        """Can a message actually be posted onto this record right now?

        Guards three ways a stored reference goes stale: the model was
        uninstalled, the record was deleted, or the model was never a thread to
        begin with. All three are silent failures if left to `message_post`.
        """
        if not model or not res_id or model in exclude_models:
            return False
        if model not in self.env:
            return False
        Model = self.env[model]
        if not hasattr(Model, 'message_post'):
            return False
        return bool(Model.sudo().browse(res_id).exists())

    @api.model
    def _candidate(self, model, res_id, rule, confidence, reason, parent_message=None):
        """Build one candidate in the shape `match()` returns."""
        return {
            'model': model,
            'res_id': res_id,
            'parent_message_id': parent_message.id if parent_message else False,
            'rule': rule,
            'confidence': confidence,
            'reason': reason,
        }

    @api.model
    def _max_age_days(self, key, default):
        """Read an age bound from ir.config_parameter, falling back to default."""
        raw = self.env['ir.config_parameter'].sudo().get_param('pan_mail_pro.%s' % key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default
