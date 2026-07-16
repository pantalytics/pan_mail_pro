# -*- coding: utf-8 -*-
"""The normalized message shape every provider returns.

A plain dict, not a model - it never touches the database and lives only for
the duration of one sync. Documented here because it is the contract the whole
incoming pipeline depends on, and because getting a key wrong is the kind of
mistake that fails silently rather than loudly.

    {
        # --- identity -------------------------------------------------------
        'message_id':          str,        # RFC5322 Message-ID, cross-provider.
                                           #   Graph: internetMessageId
                                           #   IMAP:  the Message-ID header
                                           # This is what dedup and threading key on.
        'provider_message_id': str,        # Provider-native handle, used to fetch
                                           # the full message.
                                           #   Graph: id      IMAP: UID

        # --- threading ------------------------------------------------------
        'thread_id':           str | None, # Provider thread handle.
                                           #   Graph: conversationId
                                           # None where the provider has no such
                                           # concept - callers must cope, and
                                           # _find_parent_message already falls
                                           # back to in_reply_to.
        'in_reply_to':         str | None, # RFC5322 In-Reply-To
        'references':          list[str],  # RFC5322 References, oldest first

        # --- envelope -------------------------------------------------------
        'subject':             str,
        'date':                datetime,   # NAIVE UTC. Compared against
                                           # x_last_sync_date, which is naive.
        'from':                (name, email),
        'to':                  [(name, email)],
        'cc':                  [(name, email)],

        # --- content --------------------------------------------------------
        'body_html':           str,
        'is_html':             bool,       # False => body is plain text
        'headers':             {str: str}, # ALL header names lowercased. The
                                           # X-Odoo-* loop guard reads this, and
                                           # header case is not guaranteed by
                                           # any provider.

        # --- attachments ----------------------------------------------------
        'has_attachments':     bool,       # Provider's own flag. Not reliable on
                                           # its own: Graph reports False for
                                           # inline-only images, so callers also
                                           # sniff body_html for 'cid:'.
        'attachments':         [],         # ALWAYS EMPTY from _get_message.
                                           # Filled by a separate
                                           # _get_attachments() call - see below.
            {
                'name':         str,
                'content':      bytes,     # DECODED. Graph hands back base64
                                           # (contentBytes); decode in the
                                           # provider, not in the caller.
                'content_type': str,
                'is_inline':    bool,
                'cid':          str | None,# Content-ID, without angle brackets.
                                           # Set iff is_inline.
            },
        ],
    }

Why attachments are fetched separately
--------------------------------------
`_get_message` deliberately does NOT fetch attachments; `_get_attachments` is a
second call the caller makes only once it has decided the message is worth
keeping. The skip checks (dedup, X-Odoo loop guard, internal domain, block list,
sync mode) run first, and on a mailbox syncing only known contacts most messages
never survive them. Folding the fetch into `_get_message` would download every
attachment of every message we are about to throw away, on a 1-minute cron.

The cost is that `attachments` is empty until it isn't, which is ugly. The
alternative was worse.

Four decisions that are easy to get wrong later:

1. `date` is naive UTC. `_fetch_folder` already strips tzinfo before advancing
   the sync cursor; a tz-aware datetime here would raise on comparison against
   x_last_sync_date rather than silently misbehave, but only at runtime.

2. `attachments[].content` is decoded bytes. Providers differ in encoding
   (Graph base64, IMAP raw MIME parts); normalizing this at the boundary is the
   whole point.

3. `headers` keys are lowercase. Graph returns internetMessageHeaders with
   provider-chosen case; IMAP preserves whatever the sender wrote.
"""
