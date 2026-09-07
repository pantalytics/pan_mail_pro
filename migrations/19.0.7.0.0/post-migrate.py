# -*- coding: utf-8 -*-
"""Stability over features: the triage queue, the AI seam and the app menu go.

Three removals, one release, because they were one feature:

1. **`pan.mail.item`** — the queue an incoming mail landed in when no gate let
   it through. It is a copy of somebody's correspondence, kept outside the ACL
   of any document, waiting for a person who in practice never came. A mailbox
   on `known_partners` now simply refuses the mail and says so in the log; the
   way to change that answer is to widen the sync mode, which re-reads the
   mailbox from the provider.
2. **The AI seam** (`pan.mail.ai`, `pan.mail.ai.null`, `pan.mail.ai.claude`).
   Its only caller was the triage queue's classification cron. A seam with no
   caller is not a seam, it is code that compiles.
3. **The "Communication" application menu.** Every screen it held is reachable
   under Settings → Technical → Email, where the rest of the module already
   lives.

Records first, then the schema. Menus, views, actions and crons go through the
ORM because unlinking them has side effects worth having (an action still
referenced elsewhere refuses). The registry rows go through SQL: `ir.model`
guards itself against unlinking a non-manual model unless the uninstall flag
is set, and that flag is a private constant whose name has moved between
versions. `ir_model_fields`, `ir_model_access` and `ir_rule` all cascade from
`ir_model`, so one DELETE takes the four of them.

Idempotent throughout: every DROP is `IF EXISTS`, and every DELETE matches
nothing on a second run.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

REMOVED_MODELS = [
    'pan.mail.item',
    'pan.mail.ai',
    'pan.mail.ai.null',
    'pan.mail.ai.claude',
]

REMOVED_TABLES = ['pan_mail_item']

# Mailbox columns whose only behaviour lived in the removed feature.
# `queue_unknown_contacts` fed the triage queue; `routing_smart` was the
# interlock that kept AI auto-routing off, and there is no longer anything to
# keep off.
REMOVED_MAILBOX_COLUMNS = ['queue_unknown_contacts', 'routing_smart']

# Ordered child-before-parent: a menu with children cannot go first.
REMOVED_XML_IDS = [
    'ir_cron_pan_mail_item_ai',
    'ir_cron_pan_mail_item_gc',
    'view_pan_mail_item_list',
    'view_pan_mail_item_form',
    'view_pan_mail_item_search',
    'menu_communication_triage',
    'menu_communication_mailboxes',
    'menu_communication_config',
    'menu_communication_root',
    'action_pan_mail_item',
]

# Only the parameter this release stops honouring. The customer's own API key
# is deliberately not among them: losing a secret they pasted in is the one
# step they cannot redo from inside Odoo.
REMOVED_PARAMS = ['pan_mail_pro.ai_backend']


def _drop_records(env):
    """Delete the records this release no longer declares.

    `ir.model.data._process_end` would reach most of these at the end of the
    upgrade, but it runs after the views and menus have already been loaded
    against a model that is on its way out. Doing it here means the upgrade
    never meets the half-removed state.
    """
    for name in REMOVED_XML_IDS:
        data = env['ir.model.data'].search([
            ('module', '=', 'pan_mail_pro'), ('name', '=', name),
        ])
        if not data:
            continue
        record = env[data.model].browse(data.res_id).exists()
        data.unlink()
        if record:
            record.unlink()
        _logger.info('[Mail Pro] Removed pan_mail_pro.%s', name)


def _drop_mailbox_columns(env):
    for column in REMOVED_MAILBOX_COLUMNS:
        env.cr.execute(
            'ALTER TABLE pan_mail_mailbox DROP COLUMN IF EXISTS "%s"' % column
        )
    env.cr.execute(
        "DELETE FROM ir_model_fields WHERE model = 'pan.mail.mailbox' "
        "AND name IN %s",
        (tuple(REMOVED_MAILBOX_COLUMNS),),
    )
    _logger.info(
        '[Mail Pro] Dropped mailbox columns: %s',
        ', '.join(REMOVED_MAILBOX_COLUMNS),
    )


def _drop_models(env):
    """Drop the tables, then the registry rows that point at them.

    Deleting from `ir_model` cascades to `ir_model_fields`, `ir_model_access`
    and `ir_rule`, so the ACL row and the record rule this release removed need
    no separate statement.
    """
    for table in REMOVED_TABLES:
        env.cr.execute('DROP TABLE IF EXISTS "%s" CASCADE' % table)

    env.cr.execute(
        "SELECT id FROM ir_model WHERE model IN %s", (tuple(REMOVED_MODELS),)
    )
    model_ids = [row[0] for row in env.cr.fetchall()]
    if not model_ids:
        return

    env.cr.execute(
        "DELETE FROM ir_model_data WHERE model = 'ir.model' AND res_id IN %s",
        (tuple(model_ids),),
    )
    env.cr.execute("DELETE FROM ir_model WHERE id IN %s", (tuple(model_ids),))

    # The cascade above took the field, ACL and rule rows; their xml ids are
    # now dangling, so drop each one whose record is gone.
    for model, table in (
        ('ir.model.fields', 'ir_model_fields'),
        ('ir.model.access', 'ir_model_access'),
        ('ir.rule', 'ir_rule'),
    ):
        env.cr.execute(
            "DELETE FROM ir_model_data d WHERE d.model = %%s "
            "AND NOT EXISTS (SELECT 1 FROM %s t WHERE t.id = d.res_id)" % table,
            (model,),
        )
    _logger.info('[Mail Pro] Dropped models: %s', ', '.join(REMOVED_MODELS))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    _drop_records(env)
    _drop_mailbox_columns(env)
    _drop_models(env)

    removed = env['ir.config_parameter'].sudo().search([
        ('key', 'in', REMOVED_PARAMS),
    ])
    if removed:
        removed.unlink()
        _logger.info('[Mail Pro] Removed parameters: %s', ', '.join(REMOVED_PARAMS))

    _logger.info(
        '[Mail Pro] 19.0.7.0.0: triage queue, AI seam and the Communication '
        'app menu removed'
    )
