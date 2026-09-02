# -*- coding: utf-8 -*-
"""The provider-neutral rename, done once.

Model names read `microsoft.*` and fields `x_microsoft_*` since the module was
an Outlook-only add-on. Two more providers later, every one of those names was
wrong, and the docs promised the rename as "a single mechanical phase done
last, so historical data migrates once". This is that phase.

Everything here is a rename, and every rename is metadata-only in PostgreSQL:
`ALTER TABLE ... RENAME`, `RENAME COLUMN` and `ALTER INDEX ... RENAME` rewrite
no rows and hold their lock for milliseconds. That matters on `mail_message`,
the largest table in every production database.

Why pre-migrate, and why the `ir_model*` tables are touched by hand
-------------------------------------------------------------------
Odoo reflects models and fields into `ir_model` / `ir_model_fields` by name,
and gives each an xml id (`model_x`, `field_x__y`). Left alone, the upgrade
would create *new* rows under the new names, and at the end of the load
`ir.model.data._process_end` would unlink every record whose xml id the module
no longer declares - with the uninstall flag set, which makes
`ir.model.fields.unlink` drop the column. Renaming the rows and their xml ids
before the registry loads means the ORM finds the models and fields it expects
already in place, and nothing is orphaned.

Every step checks whether its rename has already happened, so a retried
upgrade is a no-op.
"""
import logging

_logger = logging.getLogger(__name__)

MODULE = 'pan_mail_pro'

# old model -> new model. Tables follow (dots become underscores).
RENAMED_MODELS = {
    'x_microsoft.mailbox': 'pan.mail.mailbox',
    'microsoft.incoming.mail.processor': 'pan.mail.fetcher',
}

# new model -> {old field: new field}. The mailbox drops its `x_` prefix: that
# convention is for fields added to Odoo's own models, not for a model of ours.
RENAMED_FIELDS = {
    'pan.mail.mailbox': {
        'x_provider': 'provider',
        'x_mailbox_type': 'mailbox_type',
        'x_owner_user_id': 'owner_user_id',
        'x_sync_mode': 'sync_mode',
        'x_routing_smart': 'routing_smart',
        'x_route_to_team': 'route_to_team',
        'x_queue_unknown_contacts': 'queue_unknown_contacts',
        'x_exclude_internal': 'exclude_internal',
        'x_sync_start_date': 'sync_start_date',
        'x_last_sync_date': 'last_sync_date',
        'x_alias_id': 'alias_id',
        'x_error_message': 'error_message',
    },
    'mail.mail': {
        'x_microsoft_mailbox_id': 'x_send_from_mailbox_id',
    },
    'mail.message': {
        'x_microsoft_conversation_id': 'x_provider_thread_id',
    },
    'res.users': {
        'x_microsoft_default_mailbox_id': 'x_default_mailbox_id',
    },
    'mail.compose.message': {
        'x_microsoft_send_from_id': 'x_send_from_mailbox_id',
        'x_microsoft_setup_warning': 'x_setup_warning',
    },
}

# Records that keep their identity under a new xml id: views, actions, menus,
# access rules and the cron. Renaming the xml id rather than letting Odoo
# create a fresh record keeps user bookmarks, and - for the inherited views -
# avoids a window where an old arch references a field this release renamed.
RENAMED_XMLIDS = {
    'view_microsoft_mailbox_list': 'view_pan_mail_mailbox_list',
    'view_microsoft_mailbox_form': 'view_pan_mail_mailbox_form',
    'action_microsoft_mailbox': 'action_pan_mail_mailbox',
    'menu_microsoft_mailbox': 'menu_pan_mail_mailbox',
    'access_microsoft_mailbox_user': 'access_pan_mail_mailbox_user',
    'access_microsoft_mailbox_manager': 'access_pan_mail_mailbox_manager',
    'ir_cron_microsoft_fetch_incoming_mail': 'ir_cron_pan_mail_fetch_incoming_mail',
    'view_users_list_inherit_microsoft': 'view_users_list_inherit_mail_pro',
    'view_users_form_inherit_microsoft_oauth': 'view_users_form_inherit_mail_pro',
    'view_users_preferences_inherit_microsoft_oauth': 'view_users_preferences_inherit_mail_pro',
    'view_partner_form_inherit_outlook_pro': 'view_partner_form_inherit_mail_pro',
    'mail_server_invalid_outlook_pro': 'mail_server_disabled',
}

# Configuration parameters. The encryption key is the one that must not be
# missed: the parameter *is* the key, and a fresh one would orphan every
# stored credential. `encryption_utils` keeps a read-side fallback for the
# old name as insurance against a deploy that skipped the version bump.
RENAMED_PARAMS = {
    'x_pan_outlook_pro.client_id': 'pan_mail_pro.microsoft_client_id',
    'x_pan_outlook_pro.client_secret_encrypted': 'pan_mail_pro.microsoft_client_secret_encrypted',
    'x_pan_outlook_pro.tenant_id': 'pan_mail_pro.microsoft_tenant_id',
    'x_pan_outlook_pro.google_client_id': 'pan_mail_pro.google_client_id',
    'x_pan_outlook_pro.google_client_secret_encrypted': 'pan_mail_pro.google_client_secret_encrypted',
    'x_pan_outlook_pro.setup_provider': 'pan_mail_pro.setup_provider',
    'x_pan_outlook_pro.encryption_key': 'pan_mail_pro.encryption_key',
    'x_pan_outlook_pro.smtp_takeover_done': 'pan_mail_pro.smtp_takeover_done',
    'x_pan_outlook_pro.internal_domains': 'pan_mail_pro.internal_domains',
    'x_pan_outlook_pro.sync_internal_email': 'pan_mail_pro.sync_internal_email',
}

# Tables that name a model in a plain char column.
MODEL_NAME_COLUMNS = [
    ('ir_ui_view', 'model'),
    ('ir_act_window', 'res_model'),
    ('ir_filters', 'model_id'),
    ('ir_attachment', 'res_model'),
    ('mail_message', 'model'),
    ('mail_followers', 'res_model'),
    ('mail_activity', 'res_model'),
    ('ir_model_fields', 'model'),
    ('ir_model_fields', 'relation'),
]


def _table(model):
    return model.replace('.', '_')


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", [table])
    return bool(cr.fetchone()[0])


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _rename_xmlid(cr, old, new):
    cr.execute("""
        UPDATE ir_model_data SET name = %s
         WHERE module = %s AND name = %s
           AND NOT EXISTS (SELECT 1 FROM ir_model_data
                            WHERE module = %s AND name = %s)
    """, (new, MODULE, old, MODULE, new))
    return cr.rowcount


def rename_models(cr):
    for old, new in RENAMED_MODELS.items():
        cr.execute("UPDATE ir_model SET model = %s WHERE model = %s", (new, old))
        if not cr.rowcount:
            continue
        for table, column in MODEL_NAME_COLUMNS:
            if _table_exists(cr, table):
                cr.execute(f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" = %s',
                           (new, old))
        _rename_xmlid(cr, f'model_{_table(old)}', f'model_{_table(new)}')
        cr.execute("""
            UPDATE ir_model_data
               SET name = %s || substr(name, %s)
             WHERE module = %s AND name LIKE %s
        """, (f'field_{_table(new)}__', len(f'field_{_table(old)}__') + 1,
              MODULE, f'field_{_table(old)}__%'))
        _logger.info('[Mail Pro] Renamed model %s to %s', old, new)


def rename_tables(cr):
    for old, new in RENAMED_MODELS.items():
        old_table, new_table = _table(old), _table(new)
        if not _table_exists(cr, old_table) or _table_exists(cr, new_table):
            continue
        cr.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
        cr.execute("SELECT to_regclass(%s)", [f'{old_table}_id_seq'])
        if cr.fetchone()[0]:
            cr.execute(f'ALTER SEQUENCE "{old_table}_id_seq" RENAME TO "{new_table}_id_seq"')
        cr.execute("""
            SELECT 1 FROM pg_constraint
             WHERE conrelid = %s::regclass AND conname = %s
        """, (new_table, f'{old_table}_pkey'))
        if cr.fetchone():
            cr.execute(f'ALTER TABLE "{new_table}" RENAME CONSTRAINT '
                       f'"{old_table}_pkey" TO "{new_table}_pkey"')
        # Odoo looks indexes up by name; the ORM rebuilds these on a table of
        # a few dozen rows, and duplicates under the old names would only rot.
        # Indexes that back a constraint (the primary key) are left alone.
        cr.execute("""
            SELECT c.relname
              FROM pg_index i
              JOIN pg_class c ON c.oid = i.indexrelid
              JOIN pg_class t ON t.oid = i.indrelid
             WHERE t.relname = %s AND c.relname LIKE %s
               AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
        """, (new_table, f'{old_table}\\_%'))
        for (index,) in cr.fetchall():
            cr.execute(f'DROP INDEX IF EXISTS "{index}"')
        _logger.info('[Mail Pro] Renamed table %s to %s', old_table, new_table)


def rename_fields(cr):
    for model, fields in RENAMED_FIELDS.items():
        table = _table(model)
        stored = _table_exists(cr, table)
        for old, new in fields.items():
            if stored and _column_exists(cr, table, old) and not _column_exists(cr, table, new):
                cr.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')
                # Same name the ORM would give it, so `_add_index` finds it and
                # skips a rebuild. On mail_message that rebuild is the whole cost.
                cr.execute("SELECT to_regclass(%s)", [f'{table}_{old}_index'])
                if cr.fetchone()[0]:
                    cr.execute(f'ALTER INDEX "{table}_{old}_index" RENAME TO "{table}_{new}_index"')
            cr.execute("""
                UPDATE ir_model_fields SET name = %s
                 WHERE model = %s AND name = %s
                   AND NOT EXISTS (SELECT 1 FROM ir_model_fields
                                    WHERE model = %s AND name = %s)
            """, (new, model, old, model, new))
            _rename_xmlid(cr, f'field_{table}__{old}', f'field_{table}__{new}')
    _logger.info('[Mail Pro] Renamed fields on %d model(s)', len(RENAMED_FIELDS))


def rename_xmlids(cr):
    renamed = sum(_rename_xmlid(cr, old, new) for old, new in RENAMED_XMLIDS.items())
    _logger.info('[Mail Pro] Renamed %d xml id(s)', renamed)


def rename_params(cr):
    renamed = 0
    for old, new in RENAMED_PARAMS.items():
        cr.execute("""
            UPDATE ir_config_parameter SET key = %s
             WHERE key = %s
               AND NOT EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = %s)
        """, (new, old, new))
        renamed += cr.rowcount
    _logger.info('[Mail Pro] Renamed %d configuration parameter(s)', renamed)


def migrate(cr, version):
    rename_models(cr)
    rename_tables(cr)
    rename_fields(cr)
    rename_xmlids(cr)
    rename_params(cr)

    # 19.0.5.0.0's pre-migrate settles NULL sync modes on the old table name.
    # A database jumping several releases at once reaches that script after
    # this one has renamed the table, so the same fix is repeated here.
    if _column_exists(cr, 'pan_mail_mailbox', 'sync_mode'):
        cr.execute("UPDATE pan_mail_mailbox SET sync_mode = 'none' WHERE sync_mode IS NULL")
