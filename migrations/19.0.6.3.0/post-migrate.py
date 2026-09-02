# -*- coding: utf-8 -*-
"""Mark the notifications of already-cancelled mails as cancelled.

Until 19.0.6.3.0 the cancel branch in `mail.mail._send_via_mail_pro` wrote
`state = 'cancel'` and left the linked `mail.notification` rows at `ready`,
which means "queued, not sent yet". The chatter shows those as pending
forever. At Juffermans Machinebouw seventeen rows from one sync run still read
`ready` eleven days later.

The code no longer produces them. This clears the ones already written.

Only rows whose `mail.mail` still exists can be repaired -- `mail.mail` is
garbage-collected, so a notification whose mail is gone has nothing left to
say what happened to it. Those are left alone rather than guessed at.

Idempotent: rows already `sent` or `canceled` are excluded, so a re-run is a
no-op.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE mail_notification n
           SET notification_status = 'canceled'
          FROM mail_mail m
         WHERE n.mail_mail_id = m.id
           AND m.state = 'cancel'
           AND n.notification_type = 'email'
           AND n.notification_status NOT IN ('sent', 'canceled')
    """)
    if cr.rowcount:
        _logger.info(
            "[Mail Pro] %s notification(s) of cancelled mails were still "
            "marked as queued and have been marked cancelled.",
            cr.rowcount,
        )
