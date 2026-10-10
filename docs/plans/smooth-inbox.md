# A smooth Inbox: never frozen, always answering

Status: **agreed, not built.** Seven steps, in the order below; each one ships
on its own and is worth shipping without the ones after it.

## The problem

The Inbox is one client action over four panes and some thirty RPC methods,
several of which call the mail provider while the user waits. Three things make
it feel heavy, and none of them is raw speed:

1. **The first paint waits for everything.** `onWillStart` in
   `conversation_view.js` chains `sync_allowed` -> mailboxes + search view ->
   `searchModel.load` -> `refresh()` -> `select(first)` -> `read_conversation`
   -> `set_read`. Owl mounts nothing until the whole chain resolves, so the
   list skeleton that exists is never seen on the first open. The user stares
   at Odoo's own "Loading..." over a blank action.
2. **A click shows the wrong thing before it shows the right one.** `select()`
   resets to `EMPTY_CONVERSATION`, so "Not linked to a record yet" and "Link to
   a record" flash on every conversation that *is* linked. A folder switch keeps
   the old list on screen with no sign that anything is happening.
3. **Moving a pane costs a layout per frame.** Folds transition `flex-basis`,
   `flex-grow` and `padding` on all four panes, with a full conversation
   rendered inside them. On a busy mailbox that is the stutter people call
   "the app is slow".

## The rules (brand.pantalytics.com, applied to this screen)

The brand site sets two rules that decide most of this plan:

- **Skeleton loaders, not spinners.** A skeleton has the shape of the answer,
  so nothing jumps when the answer arrives.
- **150ms ease for transitions.** `$mailpro-tap` already is that.

From these, the screen's own budget:

| Moment | Budget |
|---|---|
| A click is acknowledged (pressed state, selection moves) | same frame, under 100ms |
| No answer yet | skeleton after **150ms**, never sooner. A faster answer shows no skeleton, so nothing flickers |
| A provider is slow | after **8s**, say so in the pane ("Outlook is slow to answer"). Never a frozen screen |
| A pane folds or slides | 60fps, no task over 50ms on the main thread |

One exception, made on purpose: `$mailpro-fold` stays at 280ms. A pane moving
400px in 150ms reads as a jump, not a motion. DESIGN_SYSTEM.md already names it
as the second of the two lengths; that sentence gets the reason added.

## The steps

### 1. Mount first, load after

`onWillStart` keeps only what the frame needs to draw: the mailboxes and the
search view id, in one `Promise.all` (today `loadMailboxes` runs its two calls
one after the other). Everything after it -- `searchModel.load`, `refresh()`,
the first `select()` -- moves to `onMounted`, so the four panes appear at once
with skeletons in them.

`set_read` stops being awaited inside `select()`: the row turns read
immediately (step 4), the RPC follows.

Cost: one hook move and a `loading` flag per pane. This is the single largest
change in how the screen feels.

### 2. A skeleton per pane, and a delay before it

- **Conversation pane:** a new state `loadingConversation` replaces the reset
  to `EMPTY_CONVERSATION`. The pane shows a header bar and three message
  blocks, not the "not linked" copy. The linked-to chips render only once the
  answer is in.
- **Conversation list:** on a folder or mailbox switch the old rows dim
  (`opacity: .5`, 150ms) at once and turn into skeleton rows at 150ms if the
  answer has not come. Today the skeleton only shows on an empty list.
- **Odoo record pane:** the same header-plus-blocks skeleton while the form
  loads.
- **The skeleton breathes:** an opacity pulse on `.o_mailpro_skeleton` (1.2s,
  ease-in-out, opacity only), off under `prefers-reduced-motion`. Today it is a
  static gradient, which reads as broken rather than busy.

One helper, `useDelayedFlag(150)`, so every pane gets the same delay and no
pane gets a flash.

### 3. Fewer round trips, in parallel

| Where | Today | After |
|---|---|---|
| `loadMailboxes` | two calls in sequence | `Promise.all` |
| `onReplySent`, `discardDraft`, `onDraftSaved` | `readConversation`, then `refresh` | both at once |
| `linkTo` | `refresh`, then `select` | both at once |
| `importLive` | `refresh()` selects row 0, then `select(row)` reads again | `refresh({keepSelection: true})`, one read |
| `onActivityChanged` | `select()` blanks the pane and rereads it all | reload the activities only |

### 4. Answer first, confirm after

Mark read, flag, move to folder and link: the row changes in the same frame, the
RPC runs behind it, and a failure puts the row back with Odoo's own
notification. These four are the clicks people make fifty times a day, and each
one currently waits on a round trip, three of them through the provider.

### 5. A newer click wins

`listSeq` and `conversationSeq` exist and guard most writes. Five do not, and a
slow answer can still land over a newer one: `readLiveFolder` (writes
`liveConnected`), `loadCounts`, `unfold`, `markRead` / `liveMark`, and the
thread count in `chatter_door.js`. Each gets the same token check.

Nothing is aborted today, so a user who clicks through ten conversations makes
the server read ten. Where Odoo's `rpc` hands back an abortable promise the
superseded request is aborted; where it does not, the token is enough for the
screen and the server cost stays.

### 6. A slow provider is a message, not a freeze

The provider calls inside an RPC (`live_messages`, `read_live_message`,
`import_live_message`, `live_mark`, `refresh_read_state`) inherit the HTTP
client's 30s timeout and three retries: worst case about two minutes with the
pane in a skeleton. The rule CLAUDE.md already set for the probe applies here
too: **a call made inside a request is one call, no retry loop, 10s timeout.**
The cron retries; the person does not wait for it.

On the client, after 8s with no answer, the pane says which provider is slow
and offers Try again. The live folder already returns `connected: False` on an
error; a timeout becomes that same answer instead of a hang.

`read_conversation` builds the messages *and* files, activities, drafts and the
suggestion in one call. The tabs other than Mail load when they are opened, so
the call the user waits on returns the messages only.

### 7. Cheaper frames

- **Folds:** `contain: layout paint` on each pane, so a pane moving does not lay
  out the other three; drop `padding` from the transitioned properties. Moving
  the panes to `transform` would be smoother still, but it fights the flex
  layout the drag handles depend on, and containment gets most of the gain for
  two lines. Dropped: a transform rewrite.
- **The list:** `content-visibility: auto` with a fixed `contain-intrinsic-size`
  on each row, so 200 loaded rows render like 15. No virtual list: paging at 30
  already bounds it.
- **Per-row getters:** `rowMenuItems()` builds a new array of closures for every
  row on every render; it builds on open instead. `get activities` sorts the
  whole store each render and `countsFollowReadState` runs `JSON.stringify` on
  the domain; both become values computed once per load.

## How we know it worked

`tools/ui_check.py` gets a slow-network case: Playwright delays
`read_conversation` and `search_conversations` by 2s with `page.route`, and the
check asserts that

- the four panes are drawn before either answers;
- the conversation pane shows the skeleton and never the "Not linked to a
  record yet" copy while a linked conversation loads;
- a second click during the delay ends on the second conversation, not the
  first;
- an answer under 150ms shows no skeleton at all.

Plus one timing, read from the browser trace in the same run: a pane fold has no
long task over 50ms. A budget nobody measures is a wish.

## Not in this plan

- **A spinner anywhere.** The brand rule is skeletons, and Odoo's own top-bar
  "Loading..." stays as it is.
- **Prefetching the next conversation.** It doubles provider calls for a gain
  only on keyboard navigation; revisit when step 6 has made reads cheap.
- **A service worker or a client-side cache of mail.** The Inbox stores nothing
  on purpose (ARCHITECTURE.md, the live mailbox); a browser cache would be a
  second copy of somebody's correspondence.
