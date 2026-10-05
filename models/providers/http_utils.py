# -*- coding: utf-8 -*-
"""One HTTP request, retried the way both REST providers need it.

Microsoft Graph and the Gmail API throttle the same way -- a 429 with a
Retry-After, the odd 5xx under load, a dropped connection -- and the two
clients had grown one retry loop each, with the same three constants and the
same cap. Two loops drift: one raised on a long Retry-After at every attempt
and the other only before the last, and nobody had decided that. This is the
one loop, and the clients keep a two-line `_request_with_retry` wrapper each
so that what a test patches (`graph_client.time.sleep`,
`gmail_client.requests.get`) stays where it is.

Not a model, and no provider URL is built here: the caller hands over the
URL and the headers, and gets the final `requests.Response` back. That is
also why it may live under `models/providers/` without an ARCHITECTURE
entry -- it is a function the clients share, like `mime_utils`.
"""
import logging
import time

import requests

from odoo import _

from ..mail_provider_client import ThrottledError

_logger = logging.getLogger(__name__)

# How many times a refused or lost request is tried again.
MAX_RETRIES = 3
# The first pause between attempts; doubled after each one.
INITIAL_BACKOFF_SECONDS = 2
# Longer than this, the cron does not wait: see `request_with_retry`.
MAX_RETRY_AFTER_SECONDS = 15


def request_with_retry(method, url, *, label, log_tag, headers=None, timeout=30,
                       idempotent=True, **kwargs):
    """Execute one request with rate limiting and exponential backoff.

    A 429 is honoured up to `MAX_RETRY_AFTER_SECONDS` and refused beyond it:
    sleeping longer inside the one-minute cron holds every other mailbox's
    turn hostage and, past the worker's time limit, rolls the whole run back.
    The caller records the throttle instead (`ThrottledError`, a UserError
    the fetcher and the send path both know to treat as "later").

    `idempotent=False` is for a request the server may have carried out
    before the answer was lost (sending a message, creating a draft or an
    attachment): it is retried on a 429 only, because a 429 means the request
    was refused. A timeout on `/send` retried three times is a customer
    mailed four times.

    Args:
        method:     HTTP method name, looked up on `requests` at call time so
                    a test can patch `requests.get` and the like.
        url:        the request URL
        label:      who asked for the pause, for the person reading the
                    throttle message ("Microsoft", "Google")
        log_tag:    the client's log tag ("[Graph API]", "[Gmail API]")
        headers:    request headers
        timeout:    seconds per attempt
        idempotent: whether a lost answer may be retried (see above)
        **kwargs:   passed to `requests` (json, data, params, ...)

    Returns:
        requests.Response: the final response, whatever its status. The
        caller judges it; on the last attempt a 429 is raised for status
        here so it cannot read as success.

    Raises:
        ThrottledError: on a Retry-After above the cap.
        requests.exceptions.RequestException: when every attempt failed.
    """
    last_exception = None
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = getattr(requests, method)(url, headers=headers, timeout=timeout, **kwargs)

            if response.status_code == 429:
                retry_after = response.headers.get('Retry-After')
                try:
                    wait_time = int(retry_after) if retry_after else backoff
                except ValueError:
                    # An HTTP-date rather than seconds: back off, do not crash.
                    wait_time = backoff
                if wait_time > MAX_RETRY_AFTER_SECONDS:
                    raise ThrottledError(_(
                        '%(label)s asked to wait %(wait)s seconds before more requests '
                        'for this mailbox. Try again in a minute.',
                        label=label, wait=wait_time,
                    ), wait_time)

                if attempt < MAX_RETRIES:
                    _logger.warning('%s Rate limited (429), waiting %ss before retry %s/%s',
                                    log_tag, wait_time, attempt + 1, MAX_RETRIES)
                    time.sleep(wait_time)
                    backoff *= 2
                    continue
                response.raise_for_status()  # Raise on final attempt

            if response.status_code in (500, 502, 503, 504) and attempt < MAX_RETRIES and idempotent:
                _logger.warning('%s Server error (%s), retrying in %ss (%s/%s)',
                                log_tag, response.status_code, backoff, attempt + 1, MAX_RETRIES)
                time.sleep(backoff)
                backoff *= 2
                continue

            return response

        except requests.exceptions.Timeout as e:
            last_exception = e
            if attempt < MAX_RETRIES and idempotent:
                _logger.warning('%s Request timeout, retrying in %ss (%s/%s)',
                                log_tag, backoff, attempt + 1, MAX_RETRIES)
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

        except requests.exceptions.ConnectionError as e:
            last_exception = e
            if attempt < MAX_RETRIES and idempotent:
                _logger.warning('%s Connection error, retrying in %ss (%s/%s)',
                                log_tag, backoff, attempt + 1, MAX_RETRIES)
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise requests.exceptions.RequestException('Max retries exceeded')
