# Mailboxes and Email Marketing

Mail Pro sends mail from a person. Email Marketing sends mail to a list. They
are two different jobs, and Odoo gives each its own road.

| | Mail Pro mailbox | Email Marketing + a sending service (Brevo, Mailjet, ...) |
|---|---|---|
| What | One-to-one mail: quotes, invoices, replies, follow-ups | One message to many people: newsletters, campaigns, announcements |
| From | A real address the customer can reply to | A campaign address, with an unsubscribe link |
| Lands in | The customer's inbox, and in your Sent folder | The customer's inbox; statistics in Email Marketing |
| Replies | Come back into Odoo, on the right record | Rare, and not the point |
| Volume | Tens a day per person | Hundreds to tens of thousands per send |

## When to use what

**Would you write this mail to one person by hand?** Send it from Odoo, through
your mailbox. Everything you do in the chatter, the composer and the Inbox
goes this way.

**Is it the same mail to a list, with an unsubscribe link?** Use the Email
Marketing app. Mail Pro hands those mails to Odoo's outgoing mail server and
never sends them through your mailbox.

**Why not send a campaign from your mailbox?** Microsoft 365 and Google cap
how much one mailbox may send a day, and a mailbox that sends bulk mail gets
flagged as spam. When that happens, your quotes and invoices stop arriving
too. A sending service like Brevo exists to take that risk away from your own
address.

One case to watch: selecting 200 contacts in a list and choosing **Send
email** is not a campaign. It sends 200 separate mails from your mailbox. Keep
that for a handful of people; anything bigger belongs in Email Marketing.

## Setting up Brevo for Email Marketing

1. In Brevo, create an SMTP key (Brevo → SMTP & API).
2. In Odoo, go to **Settings → Technical → Email → Outgoing Mail Servers** and
   add the server: `smtp-relay.brevo.com`, port 587, TLS, your Brevo login and
   the SMTP key.
3. Go to **Settings → Email Marketing**, turn on **Dedicated Server** and pick
   the Brevo server.

Step 3 is the one that matters. Mail Pro switches off every other outgoing
mail server when your first mailbox is created, so mail cannot leave through
two doors at once. A campaign with no dedicated server has nowhere to go and
fails. With the dedicated server set, every campaign goes through Brevo, and
everything else keeps going through your mailboxes.

**Already had Brevo before Mail Pro?** Mail Pro switched that server off too.
Open it under Outgoing Mail Servers, tick **Active** again, then do step 3.
