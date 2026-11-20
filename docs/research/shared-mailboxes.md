# Shared vs personal mailboxes: Google and Microsoft are different models

Research of 20 Nov 2026, after Dorset Wellbeing (Google Workspace) saw a
shared address treated as personal. Status of each claim: **documented** is
the vendor's own page, **community** is a forum or vendor blog, **unknown**
is a question no source answered.

## Conclusion

Microsoft has a shared mailbox *type*; Google does not. On Exchange the
address says what it is (`userPurpose = shared`) and colleagues reach it with
their own sign-in. On Gmail every mailbox is an ordinary user account, the API
calls every primary address a user, and a token reaches its own mailbox only.
"Shared" on Google is a fact about how people use the account, never a fact
the API returns. So the module cannot read "shared" from Google; it can only
infer it from who signed in as the address.

## Side by side

| | Microsoft 365 (Exchange Online) | Google Workspace (Gmail) |
|---|---|---|
| Is "shared" a type? | Yes. `mailboxSettings.userPurpose`: `user`, `linked`, `shared`, `room`, `equipment`, `others`. Read-only. **documented** | No. A "shared inbox" is a regular account with delegates: "In Gmail, delegated accounts and shared inboxes are the same." **documented** |
| What sits behind the address | A user account "with a system-generated password that isn't known or intended for use"; "Always block sign-in". **documented** | A normal Workspace user with its own password; people sign in as it or are its delegates. **documented** (delegation), **community** (seat) |
| License | None up to 50 GB. **documented** | Not stated by Google. Blogs: one paid seat like any user. **community** |
| How a colleague reaches it | Own sign-in + Full Access (read) and Send As / Send on Behalf (send), granted by an admin. Graph: `/users/{address}/...` with `Mail.Read.Shared` / `Mail.Send.Shared`. **documented** | In the Gmail web UI: delegation (up to 1000 delegates, a Google Group can be one). **documented** |
| Same thing through the API | Works with the colleague's delegated token. **documented** | Does not. A delegate's token sees its own mailbox only; UI delegation does not carry over to the API. **community**, consistent across sources; Google's `messages.list` page is silent |
| The API route to someone else's mailbox | Not needed (above) | Sign in as the address itself, or a service account with domain-wide delegation that impersonates it (admin grant, reaches every mailbox in the domain). **documented** for DWD |
| Can the API say who has access | No: "not currently possible to use Microsoft Graph to query which mailboxes the authenticated user has permissions for". Send As is PowerShell only (`Get-RecipientPermission`). **documented** | `settings.delegates.list` exists but "only available to service account clients that have been delegated domain-wide authority". **documented** |
| Send identity | Send As: `from` = `sender` = shared address, no trace. Send on Behalf: `sender` is the person. **documented** | Signed in as the address: sent as the address. Delegate in the UI: admin decides whether the delegate's address is shown. **documented** |
| Aliases | `proxyAddresses` on one mailbox; the directory may not resolve an alias. **documented** (probes doc) | `sendAs` list: `isPrimary`, `treatAsAlias`, `verificationStatus`. "An alias isn't a Google Account", so it cannot have delegates. **documented** |
| Sign-in identity | `oid` (stable), `tid` (tenant), UPN. A shared mailbox never signs in. | `sub` (stable), `email`, `hd` (Workspace domain). A shared address that signs in has its own `sub`, indistinguishable from a person's. |
| Convert user <-> shared | Both ways in the admin center; mail is kept. **documented** | Google's new admin "Shared inbox" can convert any existing account. What happens to its password is **unknown** |

## Google's new native "Shared inbox"

Admin console > Users > "Set up a shared email address", Business Starter,
Standard and Plus, "being released in phases" (page updated 10 Nov 2026,
first seen by admins Oct 2025). **documented**: it is a mailbox with
delegates; Google's own steps say "Sign into Gmail with the shared inbox
account". **unknown**: whether sign-in can be blocked, whether it takes a
seat, and whether the Directory API marks it. Nothing found says it gets an
API type like Exchange's.

This is the one change that could break the Google path: if such an address
cannot sign in, "Sign in as this address" has nothing to sign in with, and
only domain-wide delegation reaches it. To verify on the first tenant that
has the feature.

## What this means for Mail Pro

- `GoogleGmailClient.inspect_mailbox` maps the primary send-as to `user`, and
  `ADDRESS_KIND_TYPES` maps `user` to Personal. On Google that is the only
  answer the API can give, for every shared address. The type is therefore
  decided by `provider_type`'s override (`signs_in_as_itself`), not by
  Google.
- 19.0.40.2.2 (#459, Dorset accounts@) made an address signed in as itself
  with no person behind it read Shared, and stopped lending the owner's token.
  That follows the model above.
- The override does not fire when a person's own Odoo user holds the Gmail
  sign-in of the shared address (they pressed Connect while their browser was
  signed in as accounts@): the address is then "theirs" by the owner rule and
  reads Personal. Google gives no signal to tell this apart from a person's
  own mailbox. Whether Dorset is in this shape is the question for their
  database.
- Domain-wide delegation is the only Google route where colleagues keep their
  own sign-in, like Microsoft. It costs a super-admin grant over every
  mailbox in the domain, the opposite of the consent-per-address model.

## Sources

- Google, Create a shared inbox: https://knowledge.workspace.google.com/admin/users/create-a-shared-inbox
- Google, Delegate a user's email address: https://knowledge.workspace.google.com/admin/users/delegate-a-users-email-address
- Google, Let users delegate access (groups): https://support.google.com/a/answer/7223765
- Gmail Help, delegates: https://support.google.com/mail/answer/138350
- Gmail API, delegates.list: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/list
- Gmail API, manage delegates guide: https://developers.google.com/workspace/gmail/api/guides/delegate_settings
- Gmail API, messages.list: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
- Gmail API, sendAs: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.sendAs
- Community, delegate token sees own mailbox only: https://discuss.google.dev/t/can-gmail-api-access-a-delegated-user-s-mailbox-using-the-main-account-s-access-token/323439
- Community, Groups have no API to the mailbox: https://discuss.google.dev/t/reading-emails-in-google-groups/286032
- Third party on the native shared inbox: https://www.keeping.com/content/google-workspace-shared-mailboxes/ , https://www.dragapp.com/blog/google-workspace-shared-mailbox/
- Microsoft, About shared mailboxes: https://learn.microsoft.com/en-us/microsoft-365/admin/email/about-shared-mailboxes
- Microsoft, convert user mailbox to shared: https://learn.microsoft.com/en-us/microsoft-365/admin/email/convert-user-mailbox-to-shared-mailbox
- Graph, mailboxSettings: https://learn.microsoft.com/en-us/graph/api/resources/mailboxsettings
- Graph, get mailboxSettings: https://learn.microsoft.com/en-us/graph/api/user-get-mailboxsettings
- Graph, shared folders: https://learn.microsoft.com/en-us/graph/outlook-share-messages-folders
- Graph, send from another user: https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user
- Exchange, manage user mailboxes: https://learn.microsoft.com/en-us/exchange/recipients-in-exchange-online/manage-user-mailboxes/
