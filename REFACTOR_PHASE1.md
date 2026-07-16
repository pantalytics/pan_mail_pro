# Phase 1: Provider abstraction (Microsoft only)

Working checklist for Phase 1 of [REFACTOR_PLAN.md](REFACTOR_PLAN.md).

**Goal:** get the Graph code behind a provider interface, with the 66 tests still green and their Graph
payload assertions *unchanged*. Nothing user-visible changes. No new fields, no migration, no rename.

**Non-goals for Phase 1** — deliberately deferred so this phase stays provable:
- ❌ `pan.mail.provider` / `pan.mail.account` models (Phase 2)
- ❌ Any renaming (Phase 4)
- ❌ Google / IMAP (Phase 3)
- ❌ New settings or views

**The success criterion is unusually crisp:** `--test-tags=pan_outlook_pro` passes with zero edits to
any test's assertions about Graph JSON. Import paths may change; expectations may not. If a test
assertion needs changing, the refactor changed behaviour — stop and find out why.

---

## Correction to the plan

REFACTOR_PLAN.md §4 says "everything below `microsoft_incoming_mail.py:446` is provider-neutral."
**That is wrong.** `_process_message` (lines 162–446) interleaves Graph JSON parsing with business
logic all the way down:

| Lines | What | Kind |
|---|---|---|
| 174–197 | `internetMessageId`, dedup, `internetMessageHeaders`, X-Odoo loop guard | mixed |
| 199–224 | `toRecipients` / `from` → contact, internal-domain skip | mixed |
| 226–258 | partner lookup, block list, sync-mode routing | **neutral** |
| 260–272 | `hasAttachments`, `cid:` sniff, fetch attachments | Graph |
| 274–290 | partner create, `conversationId`, `body.content` | mixed |
| 292–316 | `contentBytes` / `isInline` / `contentId` / `@odata.type` → Odoo 2-/3-tuples | Graph → neutral |
| 318–446 | `ccRecipients`, `subject`, msg_dict, `message_new()` | mixed |

So the split is not a horizontal cut. It is: **extract every `.get('graphKey')` into
`_normalize_message()` on the provider, and leave the business logic reading a neutral dict.**

## Constraint to preserve

`_is_duplicate(internet_message_id)` runs on the **list preview**, before `get_message_with_headers()`
fetches the full body. That saves one API call per already-seen message on every cron run — with a
1-minute cron this matters. A naive `_fetch_messages() -> [fully normalized]` interface throws it away.

**Therefore the interface stays two-step**: list returns light previews; get returns the full message.

---

## Steps

Each step is one commit, tests green at every step.

### 1. Provider base + dispatch stub

New `models/providers/__init__.py`, `models/providers/base.py`:

```python
class PanMailProvider(models.AbstractModel):
    _name = 'pan.mail.provider.base'
    _description = 'Email Provider Interface'

    def _send(self, mail, mailbox, account): raise NotImplementedError
    def _fetch_message_previews(self, mailbox, folder, since, limit): raise NotImplementedError
    def _get_message(self, mailbox, provider_message_id): raise NotImplementedError
    def _get_sending_account(self, mailbox, mail): raise NotImplementedError
    def _supported_mailbox_types(self): return ['personal', 'shared', 'notification']
```

**Done when:** module loads, tests green (nothing calls it yet).

> **Ordering fix.** An earlier draft put the `_get_provider()` dispatch stub here, returning
> `self.env['pan.mail.provider.microsoft']` — a model that doesn't exist until step 3. Dispatch moves
> to step 3, once there is something to dispatch to.

### 2. Move the Graph client, unchanged

`git mv models/microsoft_graph_client.py models/providers/microsoft/graph_client.py`.
Keep `_name = 'microsoft.graph.client'` — renaming the model is Phase 4.

Only edits allowed: `__init__.py` chains, and import paths.

Three import sites, not two — `tests/test_attachments.py:16` imports the *module* to patch
`DIRECT_ATTACHMENT_LIMIT`, which a grep for the patch string alone misses:

```
tests/common.py:204,206          patch('...models.microsoft_graph_client.requests.post'/'.put')
tests/test_outgoing_mail.py:64   patch('...models.microsoft_graph_client.requests.post')
tests/test_attachments.py:16     from ...models import microsoft_graph_client as graph_mod
```

**Gotcha:** `graph_client.py:12` has `from . import encryption_utils`. After the move `.` is
`models/providers/microsoft/`, so it must become `from ... import encryption_utils`. This fails loudly
at module load (`ImportError: cannot import name 'encryption_utils' from partially initialized
module`), so it can't slip through — but it will look like a circular-import problem and isn't.

**Done when:** tests green with only import-path edits. **No assertion edits.**

**Status: done.** 64 tests, 0 failed, 0 errors. `tests/` diff is 4 lines, all import paths.

### 3. Microsoft provider adapter

`models/providers/microsoft/provider.py` — `_inherit = 'pan.mail.provider.base'`,
`_name = 'pan.mail.provider.microsoft'`. Thin: delegates to `microsoft.graph.client`, normalizes the
result.

```python
def _send(self, mail, mailbox, account):
    r = self.env['microsoft.graph.client'].send_email_via_graph(mail, mailbox, account)
    return {'success': r['success'], 'error': r.get('error'), 'error_code': r.get('error_code'),
            'message_id': r.get('microsoft_message_id'), 'thread_id': r.get('microsoft_conversation_id')}
```

Note the rename at the boundary: `microsoft_message_id` → `message_id`, `microsoft_conversation_id` →
`thread_id`. **Only inside the return dict** — the `mail.mail` fields keep their `x_microsoft_*` names
until Phase 4.

**Done when:** provider exists, still unused. Tests green.

### 4. Route sending through the provider

`mail_mail.py:_send_via_microsoft_graph()` — replace the direct
`self.env['microsoft.graph.client'].send_email_via_graph(...)` with
`mailbox._get_provider()._send(self, mailbox, user)`, and map the neutral keys back onto the
`x_microsoft_*` fields.

Do **not** touch `_get_mailbox_and_user()`, `_is_internal_user_notification()`, or any error-message
method. That logic is already neutral and moving it is Phase 2 scope creep.

**Done when:** `test_outgoing_mail`, `test_attachments`, `test_compose_*`, `test_system_notifications`,
`test_internal_notes` green, unchanged.

### 5. `_get_sending_account` — the Gmail-shaped seam

Move the "whose token" decision out of `mail_mail.py` and `microsoft_mailbox.py` into the provider.
This is the seam that makes Google possible later (§2 of the plan), so it is worth doing now even
though it looks like pure movement today.

- `microsoft_mailbox.py:get_sending_user()` (line 443) → `provider._get_sending_account(mailbox, mail)`
- `mail_mail.py:_resolve_sender_for_selected_mailbox()` (line 317) → folded into the same

Microsoft's implementation keeps today's exact semantics: notification/personal → owner; shared →
author's user, else `env.user`. Preserve the cron-context fallback at `mail_mail.py:328-333` verbatim
— it's subtle and `test_mailbox_routing` covers it.

**Done when:** `test_mailbox_routing` (15 tests) green, unchanged.

### 6. Define the normalized message dict

`models/providers/message.py` — a plain dict contract, documented, no model:

```python
{
  'message_id':          str,    # RFC5322 Message-ID   (Graph: internetMessageId)
  'provider_message_id': str,    # Graph: id            (IMAP: UID)
  'thread_id':           str|None,  # Graph: conversationId
  'in_reply_to':         str|None,
  'references':          list[str],
  'subject':             str,
  'date':                datetime,   # naive UTC, matching x_last_sync_date
  'from':                (name, email),
  'to':                  [(name, email)],
  'cc':                  [(name, email)],
  'body_html':           str,
  'is_html':             bool,
  'headers':             {lower_name: value},
  'attachments':         [{'name', 'content': bytes, 'content_type', 'is_inline', 'cid'}],
}
```

Decisions worth pinning down here, because they're the ones that bite later:
- `date` is **naive UTC** — matches how `_fetch_folder:148` already strips tzinfo for the cursor.
- `attachments[].content` is **decoded bytes**, not base64. Graph's `contentBytes` is base64; decode in
  the provider. `_process_message:301` already does this — it just moves.
- `thread_id` is `None` for providers without one. The processor must already handle that
  (`_find_parent_message` falls back to `In-Reply-To`).

### 7. Split `_process_message` — the hard one

Extract into `provider._normalize_message(raw)` (Microsoft):
- `internetMessageHeaders` → `headers` (already lowercased at line 194)
- `from` / `toRecipients` / `ccRecipients` → `(name, email)` tuples
- `body.content` + `contentType` → `body_html` + `is_html`
- attachment fetch + `contentBytes`/`isInline`/`contentId`/`@odata.type` filter → `attachments`
- `internetMessageId`/`id`/`conversationId`/`subject`/`receivedDateTime` → the scalar keys

Leave in the processor, now reading the neutral dict:
- dedup, X-Odoo loop guard, internal-domain skip, partner find/create, block list, sync-mode routing
- 2-/3-tuple attachment conversion (`is_inline` + `cid` → Odoo's tuple formats) — **neutral, keep it**
- `Markup()` wrapping, msg_dict building, `message_new()`, alias routing

Keep the attachment fetch **inside** `_normalize_message` (it needs `hasAttachments` + the `cid:` sniff,
both Graph-specific), but keep it lazy — only when the message will actually be processed.

**This is the step most likely to change behaviour.** `test_incoming_mail` (19 tests) is the guard.

**Done when:** `microsoft_incoming_mail.py` contains zero `.get('<graphKey>')` calls. Verify:
```bash
grep -nE "\.get\('(internetMessage|toRecipients|ccRecipients|conversationId|contentBytes|isInline|contentId|receivedDateTime|emailAddress)" models/microsoft_incoming_mail.py
# must return nothing
```

### 8. Rename the processor

`git mv models/microsoft_incoming_mail.py models/pan_mail_fetcher.py`. Keep
`_name = 'microsoft.incoming.mail.processor'` — model rename is Phase 4. Update
`data/ir_cron_data.xml` only if it references the file (it references the *model*, so probably not —
check).

**Done when:** cron still fires. Test manually, not just with unit tests.

---

## Verification

Per step:
```bash
cd .local
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_outlook_pro --test-enable --test-tags=pan_outlook_pro --stop-after-init
docker-compose start odoo
```

Unit tests do not cover the cron path or a real Graph round-trip. Before calling Phase 1 done, also:
1. Send from a personal mailbox via the composer dropdown
2. Send with an attachment >3MB (exercises the upload-session path at `graph_client.py:327`)
3. Send with an inline image (exercises `_prepare_inline_images` and cid handling)
4. Receive a reply and confirm it threads onto the original (exercises the `x_microsoft_message_id` path)
5. Let the incoming cron run once against a real mailbox and confirm Sent Items still dedup

Item 4 is the one that silently breaks and that no test catches.

## Definition of done

- [x] `models/providers/microsoft/` is the only place Graph JSON keys appear
- [x] Tests green, zero assertion changes to the pre-existing suite
- [x] Steps 1-8 complete
- [ ] The 5 manual checks below pass ← **only thing left**
- [ ] `ARCHITECTURE.md` §1 module structure updated

**Status: code complete, pending manual verification.** 76 tests, 0 failed (was 64;
`test_incoming_sync.py` adds 12). Nothing in the pre-existing suite changed except
four import paths and one docstring.

> **Criterion corrected.** An earlier draft demanded `grep -rl "microsoft" models/*.py`
> return almost nothing. That was never achievable in Phase 1 and misreads what is left:
> `x_microsoft.mailbox` and `x_microsoft_*` are *field and model names*, which stay until
> the Phase 4 rename by design. The criterion that matters is the Graph-key grep above -
> it is about coupling, not spelling.

## What the tests turned out not to cover

Worth recording, because the suite looked like it had this covered and did not.

`test_incoming_mail.py`'s 19 tests are all unit tests of helpers - `_is_duplicate`,
`_find_partner`, `_is_internal_domain`, `_route_email_via_alias`. **Not one of them drives
`_process_mailbox` or `_process_message`.** The 280-line method this phase rewrote had zero
coverage, and `mock_graph` in `tests/common.py` patches only `requests.post`/`.put` - the
incoming path is all `requests.get`, which nothing mocked.

So "0 failed" after step 7 meant nothing. `tests/test_incoming_sync.py` was written to close
that, entering at `_process_mailbox` because its signature survives the refactor: the same
tests were run against the pre-refactor processor (14 Graph refs) and the post-refactor one
(0), passing identically. That is what makes step 7 provably behaviour-preserving rather
than merely green.

**Lesson for Phase 3:** a green suite is not coverage. Check what a test actually drives
before trusting it as a safety net.
