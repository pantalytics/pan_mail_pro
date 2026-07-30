# -*- coding: utf-8 -*-
"""Building an RFC 5322 message out of a `mail.mail`.

Two providers send mail as MIME rather than as JSON: Gmail (base64url RFC822 in
its send endpoint) and IMAP/SMTP (MIME is the wire). Both need exactly the same
message built from the same Odoo record, so it is built once here.

This is not a third abstraction layer. It is a function that turns Odoo fields
into an `EmailMessage`, used *by* provider implementations; it decides nothing
about credentials, routing or folders. Microsoft does not use it at all - Graph
takes a JSON body, and pretending otherwise would mean building MIME only to
take it apart again.
"""
import mimetypes
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr


def collect_recipients(raw_list, partners=None):
    """Merge a comma-separated address string and Odoo partners into a
    de-duplicated list of formatted RFC 5322 addresses."""
    seen = set()
    result = []

    def _add(name, address):
        if address and address.lower() not in seen:
            seen.add(address.lower())
            result.append(formataddr((name, address)) if name else address)

    for raw in (raw_list or '').split(','):
        raw = raw.strip()
        if raw:
            name, address = parseaddr(raw)
            _add(name, address)
    for partner in (partners or []):
        if partner.email:
            _add(partner.name, partner.email)
    return result


def bare_addresses(formatted):
    """Strip display names — what an SMTP envelope wants."""
    return [parseaddr(address)[1] for address in formatted if parseaddr(address)[1]]


def new_message_id(from_email):
    """A Message-ID anchored to the sending domain.

    We generate it rather than receive it because both MIME senders set their
    own: having it up front is what lets dedup and reply-threading key on it.
    """
    domain = from_email.split('@')[-1] if from_email and '@' in from_email else None
    return make_msgid(domain=domain)


def build_message(mail_record, from_email, to_addrs, cc_addrs, message_id,
                  reply_context=None):
    """Build the outgoing `EmailMessage` for one `mail.mail`.

    Includes the X-Odoo-* loop guard the incoming sync keys on, so our own sent
    mail is never re-imported, and the In-Reply-To / References pair, which is
    the only threading signal a plain IMAP mailbox has.

    `reply_context` (see `mail.mail._build_reply_context`) wins over
    `mail.mail.references` when present, and the difference matters: Odoo's own
    field holds the Message-IDs *Odoo* generated, while the id that actually
    went on the wire is the one from `new_message_id()` here, or the one the
    provider minted for us. A chain built from Odoo's ids names messages the
    recipient never saw, so their reply comes back pointing at something we
    cannot resolve. The reply context is built from what was really sent.
    """
    msg = EmailMessage()
    msg['Subject'] = mail_record.subject or '(No Subject)'
    msg['From'] = from_email
    if to_addrs:
        msg['To'] = ', '.join(to_addrs)
    if cc_addrs:
        msg['Cc'] = ', '.join(cc_addrs)
    msg['Message-ID'] = message_id

    reply_context = reply_context or {}
    references = list(reply_context.get('references') or []) or _references(mail_record)
    in_reply_to = reply_context.get('in_reply_to') or (references[-1] if references else None)
    if references:
        msg['References'] = ' '.join(references)
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to

    # The X-Odoo-* loop guard: the incoming sync skips anything carrying these,
    # so our own sent mail is never re-imported from the mailbox.
    if mail_record.model and mail_record.res_id:
        msg['X-Odoo-Model'] = mail_record.model
        msg['X-Odoo-Record-Id'] = str(mail_record.res_id)
    msg['X-Odoo-Mail-Id'] = str(mail_record.id)
    if mail_record.mail_message_id:
        msg['X-Odoo-Message-Id'] = str(mail_record.mail_message_id.id)

    body_html = mail_record.body_html or mail_record.body or ''
    # Plain-text part first so set_content makes this the multipart/alternative
    # root; the HTML is the alternative clients actually render.
    msg.set_content('This email requires an HTML-capable client.')
    msg.add_alternative(body_html, subtype='html')

    attach_files(msg, mail_record.attachment_ids)
    return msg


def attach_files(msg, attachments):
    for attachment in attachments:
        content = attachment.raw  # decoded bytes, not base64
        if not content:
            continue
        content_type = (attachment.mimetype
                        or mimetypes.guess_type(attachment.name or '')[0]
                        or 'application/octet-stream')
        maintype, _, subtype = content_type.partition('/')
        msg.add_attachment(
            content, maintype=maintype, subtype=subtype or 'octet-stream',
            filename=attachment.name or 'attachment',
        )


def thread_key(msg, message_id):
    """The conversation handle for a MIME message.

    Graph has conversationId and Gmail has threadId; MIME has neither, so the
    root of the References chain stands in — every message in a thread carries
    the same root, which is exactly the property the caller needs. A message
    that starts a thread is its own root.
    """
    references = _header_ids(msg.get('References')) or _header_ids(msg.get('In-Reply-To'))
    return references[0] if references else message_id


def _references(mail_record):
    """Message-IDs this mail is a reply to, oldest first.

    `mail.mail.references` is Odoo's own field for the header of the same name —
    it fills it in for a notification on a threaded message and leaves it empty
    for a new thread.
    """
    return _header_ids(mail_record.references)


def _header_ids(value):
    return [token for token in (value or '').split() if token.startswith('<')]
