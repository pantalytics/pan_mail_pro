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

-- No mailbox may send or sync.
UPDATE x_microsoft_mailbox
   SET active = false,
       state = 'draft';

-- Drop the credentials themselves, for the same reason base drops `smtp_pass`:
-- a neutralized database gets copied around, and a dump carrying a live refresh
-- token can send mail from anywhere it lands.
UPDATE pan_mail_account
   SET active = false,
       connected = false,
       access_token_encrypted = NULL,
       refresh_token_encrypted = NULL,
       token_expiry = NULL,
       password_encrypted = NULL;
