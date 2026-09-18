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
    3. thread_link           (provider, mailbox, thread key)      0.9
       thread_link_legacy    unscoped mail.message conversation   0.85
    4. record_reference      a document number quoted in the subject
                                                    unambiguous   0.85
                                                    ambiguous     0.5   proposal
    5. only_open_record      the sender's only open record in the
                             mailbox's target model               0.6   proposal
    6. subject_participants  normalised subject + same partner    0.5   proposal

Rules 1 and 2 are RFC 5322, so they work identically on Microsoft 365, Gmail,
plain IMAP, and anything else that speaks email. Rule 3 is the only one that
touches a provider concept, and it treats that concept as a *hint that is only
valid inside one mailbox* — which is what it actually is. Rule 4 is a lookup
rather than a guess, and it says so by refusing to route the moment the subject
names more than one record. Rules 5 and 6 never route on their own; they exist
to hand a candidate set to whatever decides the residue, which today is a
person clicking the suggestion on the inbox screen.

No rung is trusted alone. Every rung that can go quiet has a second way in:
rule 2 resolves a Message-ID through the ref index *and* through Odoo's own
`message_id`, and rule 3 looks a thread up under both handles a conversation
carries (see `thread_keys`). Threading is the feature that fails silently --
the mail still arrives, just on a 0.50 guess in a review queue -- so the
cheapest insurance is a second lookup, not a better single one.

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
RULE_RECORD_REFERENCE = 'record_reference'
RULE_ONLY_OPEN_RECORD = 'only_open_record'
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

# Where a reference pasted into a subject line is looked up: one model and the
# field that holds the reference people actually quote. Every entry is checked
# against the registry before it is searched, so an uninstalled module or a
# renamed field drops out instead of raising.
#
# The field has to be a *reference*, not a title. `crm.lead.name` and
# `project.task.name` are what somebody typed, so an equality match on them
# would be a coincidence rather than a lookup, and they are left off on
# purpose.
SUBJECT_REFERENCE_FIELDS = (
    ('sale.order', 'name'),
    ('purchase.order', 'name'),
    ('account.move', 'name'),
    ('stock.picking', 'name'),
    ('mrp.production', 'name'),
    ('repair.order', 'name'),
    ('helpdesk.ticket', 'ticket_ref'),
)

# What a reference looks like in a subject: letters and digits, possibly with
# separators, so `SO0042`, `INV/2026/00017` and `WH/OUT/00012` all qualify. The
# rule itself then demands at least one letter *and* one digit, which is what
# drops a bare year, a date and an ordinary word.
_REFERENCE_TOKEN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9/._-]{2,38}[A-Za-z0-9]')

# How many tokens of one subject are looked up. Each one costs an indexed
# equality search per model in the registry, and this rule only ever runs on
# mail the deterministic rules above it could not place. Three is well past
# any subject that quotes a document number on purpose.
_MAX_REFERENCE_TOKENS = 3

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
                thread_keys:       every handle this thread may be keyed under
                reference_ids:     the References chain as read off the mail
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
        ctx['thread_keys'] = self.thread_keys(message, ctx['reference_ids'])
        ctx['thread_id'] = ctx['thread_keys'][0] if ctx['thread_keys'] else False

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
            decision = dict(best, candidates=candidates)
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
                'candidates': candidates,
            }
        # The evidence, on every decision including the ones that failed. A
        # fallback used to be indistinguishable from "the headers were empty",
        # which is the single most useful thing to know about a mail that did
        # not thread — and it took four tables to reconstruct after the fact.
        decision.update({
            'thread_id': ctx['thread_id'],
            'thread_keys': ctx['thread_keys'],
            'reference_ids': ctx['reference_ids'],
        })

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
            '_rule_record_reference',
            '_rule_only_open_record',
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
        """The thread handles this conversation carries, scoped to the mailbox.

        Every key from `thread_keys()` is tried, strongest first, because the
        provider handle alone is not dependable: Microsoft hands back the
        *draft's* conversationId on send, and the reply can arrive under a
        different one, at which point a link keyed only on the first is silent
        on the one case threading exists for. The References root is tried
        after it and cannot drift — every participant carries the same root.

        Then the legacy `mail.message.x_provider_thread_id` column, written
        until 19.0.6.0.0 and never since. It carries no mailbox, so it can in
        principle match another mailbox's thread — it is kept because dropping
        it would break threading on conversations that predate the link index,
        and it is bounded three ways the original lookup was not: newest match
        instead of oldest, an age limit, and the caller's excluded models. It
        scores below the scoped lookup and stops matching by itself as those
        conversations pass the age limit.
        """
        thread_keys = ctx['thread_keys']
        mailbox = ctx['mailbox']
        if not thread_keys or not mailbox:
            return []

        cutoff = fields.Datetime.now() - timedelta(
            days=self._max_age_days('thread_max_age_days', DEFAULT_THREAD_MAX_AGE_DAYS)
        )
        candidates = []

        Link = self.env['pan.mail.thread.link'].sudo()
        for thread_id in thread_keys:
            link = Link.search([
                ('provider', '=', mailbox.provider),
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
            ('x_provider_thread_id', 'in', list(thread_keys)),
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
                'Legacy conversation id %s, most recent message'
                % legacy.x_provider_thread_id,
                parent_message=legacy,
            ))
        return candidates

    def _rule_record_reference(self, ctx):
        """A record's own reference, quoted in the subject.

        "Re: SO0042" and "vraag over INV/2026/00017" are the case the rules
        above are silent on: somebody writes a fresh mail about a document
        instead of replying to one, so there is no References chain and no
        thread the provider has seen before. The reference is still exact, and
        finding it is a lookup rather than a guess, which is why this sits
        above everything that scores on resemblance.

        It is a lookup **only while it is unambiguous**. One token that
        resolves to one record is routed on; a subject that names two
        documents, or a token that two models both claim, is a question and
        not an answer, so every hit drops to a proposal and the ladder
        continues. That is the whole difference between this rule and a
        regular-expression guess.

        It runs after `_rule_thread_link` on purpose: a mail that continues a
        thread Odoo already filed belongs on that thread's record even when
        the subject still quotes the order number the thread started from.
        """
        subject = ctx['message'].get('subject') or ''
        if not subject:
            return []

        seen = set()
        hits = []
        for token in self._reference_tokens(subject):
            for model, field in SUBJECT_REFERENCE_FIELDS:
                if model in ctx['exclude_models'] or model not in self.env:
                    continue
                Model = self.env[model]
                if field not in Model._fields:
                    continue
                # Two, not one: a second row means the reference does not
                # identify a record even inside its own model.
                records = Model.sudo().search([(field, '=', token)], limit=2)
                if len(records) != 1:
                    continue
                record = records[0]
                key = (model, record.id)
                if key in seen:
                    continue
                if not self._is_routable(model, record.id, ctx['exclude_models']):
                    continue
                seen.add(key)
                hits.append((model, record.id, token))

        if not hits:
            return []

        # One record named, one answer. More than one and the subject is
        # ambiguous, so the hits become a shortlist instead of a decision.
        confidence = 0.85 if len(hits) == 1 else 0.5
        reason_tail = '' if len(hits) == 1 else ' (the subject names %d records)' % len(hits)
        return [
            self._candidate(
                model, res_id, RULE_RECORD_REFERENCE, confidence,
                'The subject quotes %s%s' % (token, reason_tail),
            )
            for model, res_id, token in hits
        ]

    def _rule_only_open_record(self, ctx):
        """The contact's single open record in the mailbox's own target model.

        A mailbox that routes to a team says what its mail is about: `support@`
        makes tickets, so mail arriving there is about a ticket. When the
        sender has exactly one open one, that is the only record it can
        sensibly be.

        Scored as a proposal, never a routing decision, and the distinction is
        deliberate. "There is exactly one candidate" is arithmetic; "this mail
        is about it" is still a guess, and the customer with one open printer
        ticket who writes in about an invoice is not a rare case. What it earns
        is the suggestion on screen, which somebody confirms in one click --
        and that click writes a thread link, so the rest of the conversation
        is matched by rule 3 from then on, exactly and for free.

        Nothing here is asked when the mailbox routes to nobody: without a
        target model there is no set to be alone in.
        """
        partner = ctx['partner']
        mailbox = ctx['mailbox']
        if not partner or not mailbox or not mailbox.route_to_team:
            return []

        alias = mailbox.alias_id
        model = alias.alias_model_id.model if alias and alias.alias_model_id else False
        if not model or model in ctx['exclude_models'] or model not in self.env:
            return []

        Model = self.env[model]
        if 'partner_id' not in Model._fields:
            return []

        # `active` needs no clause: an archived record is already absent from
        # an ordinary search. A folded stage is the other half of "closed" and
        # has to be asked for, where the model has stages at all. A record
        # with no stage counts as open -- `stage_id.fold = False` does not
        # match a NULL, and a lead that has not reached a stage yet is the
        # opposite of closed.
        domain = [('partner_id', '=', partner.id)]
        stage = Model._fields.get('stage_id')
        if stage is not None and stage.comodel_name in self.env \
                and 'fold' in self.env[stage.comodel_name]._fields:
            domain += ['|', ('stage_id', '=', False), ('stage_id.fold', '=', False)]

        records = Model.sudo().search(domain, limit=2)
        if len(records) != 1:
            return []
        record = records[0]
        if not self._is_routable(model, record.id, ctx['exclude_models']):
            return []

        return [self._candidate(
            model, record.id, RULE_ONLY_OPEN_RECORD, 0.6,
            'The only open %s for %s' % (Model._description or model, partner.display_name),
        )]

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
    def thread_keys(self, message, reference_ids=None):
        """Every handle this conversation may be keyed under, strongest first.

        Two of them, deliberately, because neither is dependable alone:

        1. The provider's own thread handle — Graph's conversationId, Gmail's
           threadId. Exact where it holds, and on Microsoft it does not always
           hold: the id Graph reports for a *draft* is the one the send path
           indexes, and the reply can arrive in the same mailbox under a
           different one. A conversation keyed only on that goes quiet on the
           commonest case there is, someone answering mail we sent.
        2. The root of the References chain. RFC 5322 rather than a vendor
           concept, identical for every participant in the thread, and it
           cannot be reassigned. It is the only handle IMAP has at all, and
           the one that survives when the provider's drifts. A message that
           starts a thread is its own root.

        Links are written under every key and looked up under every key, so a
        thread that loses one still matches on the other.
        """
        if reference_ids is None:
            headers = {k.lower(): v for k, v in (message.get('headers') or {}).items()}
            reference_ids = self._reference_ids(headers)

        keys = []
        provider_key = message.get('thread_id')
        if provider_key:
            keys.append(provider_key)
        # reference_ids is nearest-first; the root is the last entry.
        rfc_key = reference_ids[-1] if reference_ids else message.get('message_id')
        if rfc_key and rfc_key not in keys:
            keys.append(rfc_key)
        return keys

    @api.model
    def _effective_thread_id(self, message, reference_ids=None):
        """The single handle to report as *the* thread id. See `thread_keys`."""
        keys = self.thread_keys(message, reference_ids)
        return keys[0] if keys else False

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

        Two places to look, because an Odoo message can be reachable under more
        than one id: the ref index (every id we ever saw for it, including the
        one the provider minted on send) and Odoo's own `message_id` (set by
        message_post on import, or generated for mail composed here).
        """
        if not message_id:
            return self.env['mail.message'].browse()
        message_id = message_id.strip()

        parent = self.env['pan.mail.message.ref'].lookup(message_id)
        if parent:
            return parent

        return self.env['mail.message'].sudo().search(
            [('message_id', '=', message_id)], order='id desc', limit=1)

    @api.model
    def _reference_tokens(self, subject):
        """Tokens in a subject that could be a document reference.

        A reference carries at least one letter and at least one digit. That
        one condition is what separates `SO0042` from a bare year, a date, an
        ordinary word and the reply prefix, without a per-format pattern for
        every model a customer might install.
        """
        tokens = []
        for token in _REFERENCE_TOKEN_RE.findall(subject or ''):
            if not any(c.isdigit() for c in token):
                continue
            if not any(c.isalpha() for c in token):
                continue
            if token in tokens:
                continue
            tokens.append(token)
            if len(tokens) >= _MAX_REFERENCE_TOKENS:
                break
        return tokens

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
