# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Copy per-user Microsoft OAuth tokens onto pan.mail.account records.

    Three deliberate choices, each of which has a way to go wrong that this
    docstring exists to prevent someone "cleaning up" later:

    1. Pure SQL, no ORM. `_compute_decrypted_tokens` raises on a ciphertext it
       cannot read, so an ORM-based migration would explode partway through and
       leave half the users migrated. SQL either moves every row or none.

    2. The ciphertext is copied verbatim, never decrypted and re-encrypted.
       Same database, same Fernet key, so the encrypted string is portable as-is.
       A decrypt/re-encrypt cycle can silently produce garbage if the key is
       missing or rotated, and nobody finds out until the next send.

    3. The res_users columns are left in place. They are the rollback for this
       release; a later one drops them, once production has run on accounts for
       a while.

    Idempotent: re-running skips users that already have a Microsoft account, so
    a half-finished upgrade can be retried.

    x_microsoft_oauth_state is not copied on purpose. It is a CSRF nonce for an
    OAuth round trip that cannot survive a service restart anyway, and nothing
    reads the account's copy until step 5.
    """
    cr.execute("""
        INSERT INTO pan_mail_account (
            email, provider, user_id, active,
            access_token_encrypted, refresh_token_encrypted, token_expiry,
            connected, create_uid, create_date, write_uid, write_date
        )
        SELECT COALESCE(NULLIF(p.email, ''), u.login),
               'outlook',
               u.id,
               u.active,
               u.x_microsoft_access_token_encrypted,
               u.x_microsoft_refresh_token_encrypted,
               u.x_microsoft_token_expiry,
               TRUE,
               1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
          FROM res_users u
          JOIN res_partner p ON p.id = u.partner_id
         WHERE u.x_microsoft_refresh_token_encrypted IS NOT NULL
           AND u.x_microsoft_refresh_token_encrypted != ''
           AND NOT EXISTS (
               SELECT 1 FROM pan_mail_account a
                WHERE a.user_id = u.id AND a.provider = 'outlook'
           )
    """)
    migrated = cr.rowcount

    # The verification query from REFACTOR_PHASE2.md, run automatically rather
    # than left for someone to remember. Every connected user must have exactly
    # one account holding their refresh token.
    cr.execute("""
        SELECT (SELECT count(*) FROM res_users
                 WHERE x_microsoft_refresh_token_encrypted IS NOT NULL
                   AND x_microsoft_refresh_token_encrypted != ''),
               (SELECT count(*) FROM pan_mail_account
                 WHERE provider = 'outlook'
                   AND refresh_token_encrypted IS NOT NULL)
    """)
    users_connected, accounts_connected = cr.fetchone()

    # x_microsoft_oauth_connected is stored, and this upgrade repoints its
    # @api.depends at the account. Odoo does not recompute a stored field just
    # because its depends changed, so the old True/False values survive - right
    # for every user whose tokens were copied above, wrong for any user the copy
    # missed. Realign it in SQL so the field and the accounts cannot disagree;
    # a user without an account must read as disconnected, because that is what
    # the compute would now say.
    cr.execute("""
        UPDATE res_users u
           SET x_microsoft_oauth_connected = EXISTS (
                   SELECT 1 FROM pan_mail_account a
                    WHERE a.user_id = u.id
                      AND a.provider = 'outlook'
                      AND a.refresh_token_encrypted IS NOT NULL
               )
         WHERE u.x_microsoft_oauth_connected IS DISTINCT FROM EXISTS (
                   SELECT 1 FROM pan_mail_account a
                    WHERE a.user_id = u.id
                      AND a.provider = 'outlook'
                      AND a.refresh_token_encrypted IS NOT NULL
               )
    """)
    realigned = cr.rowcount

    _logger.info(
        '[Mail Pro] Migrated %s user token set(s) to pan.mail.account '
        '(%s connected users, %s connected accounts, %s connection flag(s) realigned)',
        migrated, users_connected, accounts_connected, realigned,
    )
    if users_connected != accounts_connected:
        _logger.error(
            '[Mail Pro] Token migration mismatch: %s connected users but %s '
            'connected Microsoft accounts. Sending will fall back to the '
            'res.users columns until this is resolved - do not drop them.',
            users_connected, accounts_connected,
        )
