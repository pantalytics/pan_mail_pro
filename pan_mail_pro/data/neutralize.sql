-- Mail Pro's share of database neutralization.
--
-- Odoo protects a staging or test copy by deactivating every `ir_mail_server`
-- and inserting an invalid one, so any SMTP send fails. Mail Pro never touches
-- `ir_mail_server` -- it talks to the Graph API, the Gmail API or an SMTP host
-- of its own -- so none of that reaches it, and a restored dump still holds
-- working OAuth refresh tokens and mailbox passwords. Without this file a
-- staging database mails real customers from the real address.
--
-- Odoo runs `data/neutralize.sql` for every installed module; it is found by
-- path, so it is deliberately not listed in `__manifest__.py`.
--
-- Neutralization runs on the restored dump BEFORE the module is upgraded, so
-- the schema is whatever the source database had, which can be any older
-- version of this module. Every statement is therefore guarded: a table or
-- column that does not exist yet is skipped instead of failing the whole build.
-- That covers the pre-rename schema too (`x_microsoft_mailbox`, tokens on
-- `res_users`, the secret in `ir_config_parameter`).

DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT * FROM (VALUES
            -- No mailbox may send or sync.
            ('pan_mail_mailbox', 'active', 'false'),
            ('pan_mail_mailbox', 'state', '''draft'''),
            -- Drop the credentials themselves, for the same reason base drops
            -- `smtp_pass`: a neutralized database gets copied around, and a
            -- dump carrying a live refresh token can send mail from anywhere
            -- it lands.
            ('pan_mail_account', 'active', 'false'),
            ('pan_mail_account', 'connected', 'false'),
            ('pan_mail_account', 'access_token_encrypted', 'NULL'),
            ('pan_mail_account', 'refresh_token_encrypted', 'NULL'),
            ('pan_mail_account', 'token_expiry', 'NULL'),
            ('pan_mail_account', 'password_encrypted', 'NULL'),
            -- The application secret is a credential too, and it outlives
            -- every token.
            ('pan_mail_provider', 'client_secret_encrypted', 'NULL'),
            -- Pre-rename schema.
            ('x_microsoft_mailbox', 'active', 'false'),
            ('res_users', 'x_microsoft_access_token_encrypted', 'NULL'),
            ('res_users', 'x_microsoft_refresh_token_encrypted', 'NULL'),
            ('res_users', 'x_microsoft_token_expiry', 'NULL')
        ) AS t(tbl, col, val)
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = target.tbl
               AND column_name = target.col
        ) THEN
            EXECUTE format('UPDATE %I SET %I = %s', target.tbl, target.col, target.val);
        END IF;
    END LOOP;
END
$$;

DELETE FROM ir_config_parameter
 WHERE key = 'x_pan_outlook_pro.client_secret_encrypted';
