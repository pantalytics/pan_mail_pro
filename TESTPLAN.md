# Testplan pan_mail_pro v19.0.3.2.0 — rename + provider-refactor + IMAP/SMTP

Doel: aantonen dat na de rename (`pan_outlook_pro` → `pan_mail_pro`) en de
provider-refactor zowel Outlook als Gmail end-to-end werken, in drie ringen:
lokaal → dogfood → klanten.

## Fase A — Lokaal (Docker)

> **Besluit 30-07:** de functionele tests (A3/A4) draaien we **niet** lokaal
> maar op een CloudPepper-testinstance — zie Fase A′ hieronder. A1/A2 zijn
> wel lokaal uitgevoerd en blijven geldig als regressiebewijs. De lokale
> Docker-omgeving is gestopt; de Microsoft-config die al in de lokale
> `test_db` was gezet is daarmee irrelevant geworden (kan blijven staan).

### A1. Omgeving en module-upgrade
- [x] `docker-compose up -d` in `.local/`, Odoo bereikbaar op :8069 (30-07)
- [x] Modulestatus: `test_db` had al `pan_mail_pro` 19.0.3.0.0 geïnstalleerd —
      het rename-pad was lokaal al doorlopen (30-07)
- [x] Logs schoon: alleen een onschuldige opstart-race (db booting) en een
      Postgres-collation-warning (30-07)

### A2. Unit tests lokaal
- [x] `--test-enable --test-tags=pan_mail_pro`: 0 failed, 0 errors van
      148 tests tegen de ge-upgradede database (30-07)

### A3. Outlook functioneel (eerst — was al productie, dus regressiecheck)
- [x] Microsoft-OAuth-config in lokale `test_db` gezet (30-07): client ID,
      tenant ID, auth/token-URL's en het versleutelde client secret gekopieerd
      uit de Pantalytics-productiedatabase. Kon lossless: de Fernet-sleutel
      (`x_pan_outlook_pro.encryption_key`) staat zelf ook in
      `ir_config_parameter`, dus sleutel + ciphertext samen kopiëren werkt
      cross-database. NB: productie-secret staat nu in de lokale dev-db.
- [ ] **WACHT OP RUTGER:** redirect-URI
      `http://localhost:8069/microsoft_oauth/callback` toevoegen aan de
      Azure-appregistratie `32a681d3-aeb0-4dc8-bf32-b40874ce062a`
      (portal.azure.com → App registrations → Authentication → platform Web).
      Kan niet via API: Lokka-koppeling heeft alleen SharePoint-scopes (403).
- [ ] OAuth-flow Microsoft doorlopen, `pan.mail.account` connected
- [ ] Versturen vanaf personal mailbox; mail komt aan, Message-ID opgeslagen
- [ ] Versturen vanaf shared mailbox (SendAs met eigen token)
- [ ] Inkomende mail gesynct door fetcher; `message_new()`-pad, geen
      dubbele notificaties
- [ ] Reply van buitenaf landt in dezelfde chatter-thread (conversationId)
- [ ] Mailbox zonder werkende credentials → mail cancelled, niet via SMTP gelekt

### A4. Gmail functioneel

> **Wat hiervan al geautomatiseerd is** (`tests/test_incoming_sync_gmail.py`,
> draait in CI zonder Google-credentials): inkomende sync end-to-end door
> `_process_mailbox` op Gmail-data, INBOX/SENT-labelmapping, threading van een
> reply op dezelfde thread, de X-Odoo-loopguard en dedup over twee sync-runs.
> `tests/test_google_provider.py` dekt daarnaast al de client zelf, inclusief
> `access_type=offline` + `prompt=consent` in de autorisatie-URL en het
> behouden van het refresh token bij verversing.
>
> **Wat hieronder overblijft is precies wat een echte tenant vereist:** het
> consent-scherm, een echt refresh token dat na een uur nog werkt, en of
> Google de mail daadwerkelijk aflevert. De rest is regressie die CI bewaakt.

- [ ] **WACHT OP RUTGER:** productie heeft géén `x_pan_outlook_pro.google_*`
      parameters — er is in Odoo nooit een Google-client geconfigureerd.
      Bestaat er al een OAuth-client in Google Cloud Console (uit de
      Gmail-ontwikkelfase)? Voeg daar
      `http://localhost:8069/google_oauth/callback` als redirect-URI aan toe
      en vul client ID + secret in op de settings-pagina. Zo niet: nieuwe
      client aanmaken.
- [ ] OAuth-flow Google doorlopen (met `access_type=offline` + `prompt=consent`;
      refresh token daadwerkelijk opgeslagen)
- [ ] Versturen vanaf Gmail-account (RFC822 MIME, Message-ID door ons gezet)
- [ ] Inkomende mail gesynct (INBOX-label)
- [ ] Reply landt in dezelfde thread (threadId)
- [ ] Shared/Workspace-mailbox zonder owner: credentials-check via
      `_has_working_credentials()` werkt

### A5. IMAP/SMTP functioneel (Soverin)
- [ ] Email-account aanmaken (Instellingen → Technisch → E-mail → E-mailaccounts),
      provider *IMAP / SMTP*, adres op `soverin.net` → servers worden voorgevuld
- [ ] **Test Connection**: IMAP én SMTP allebei groen; verkeerd wachtwoord geeft
      een foutmelding die zegt wélke helft faalt
- [ ] Mailbox met provider *IMAP / SMTP* aanmaken op hetzelfde adres;
      shared mailbox vraagt géén owner
- [ ] Versturen vanaf de IMAP-mailbox; mail komt aan én staat in de Sent-map van
      de mailbox zelf (APPEND) — controleer in Roundcube/eigen mailclient
- [ ] Inkomende mail gesynct (INBOX), oudste eerst, cursor loopt door
- [ ] Reply van buitenaf landt in dezelfde chatter-thread (References-root)
- [ ] Eigen verzonden mail wordt niet opnieuw geïmporteerd (X-Odoo-loop guard)
- [ ] Credentials leeghalen → mailbox `error`, mail cancelled, niet via SMTP gelekt
- [ ] Server met afwijkende Sent-map (bijv. `INBOX.Verzonden`): override op het
      account werkt

## Fase A′ — Functioneel testen op CloudPepper-testinstance

Vervangt A3/A4 hierboven; de checklists daar blijven de inhoudelijke
testgevallen, alleen de omgeving verandert.

De instance is **https://mailpro-dev.cloudpepper.site** — nieuw aangemaakt, niet
bean-forge, zodat de demo's niet in de weg zitten en er verder niets in de
database staat. Server `Pantalytics Demo` (Odoo 19.0 community). Login `admin`,
wachtwoord in Bitwarden Secrets Manager als `MAILPRO_ODOO_ADMIN_PASSWORD`
(project `dev`).

- [x] Testinstance kiezen of aanmaken via CloudPepper (30-07) — nieuwe instance
      `mailpro-dev`, 1 worker (die server heeft 4 GB en draait al vier andere
      instances)
- [x] `pan_mail_pro` 19.0.3.0.0 op de instance deployen (git addons attach,
      branch `19.0`) (30-07) — webhook + auto-upgrade aan, dus een merge naar
      `19.0` is binnen ~1 minuut live. Log schoon bij eerste start.
- [ ] Microsoft-config invullen op de settings-pagina (client ID, tenant ID,
      client secret van de bestaande Azure-app `32a681d3-...`)
- [ ] Redirect-URI `https://mailpro-dev.cloudpepper.site/microsoft_oauth/callback`
      toevoegen aan de Azure-appregistratie (handmatig, portal.azure.com)
- [ ] A3-checklist (Outlook) doorlopen op de instance
- [ ] Google OAuth-client regelen (Cloud Console) met redirect-URI
      `https://mailpro-dev.cloudpepper.site/google_oauth/callback`
- [ ] A4-checklist (Gmail) doorlopen op de instance

Beide callback-paden zijn geverifieerd tegen `controllers/main.py` (regel 19 en
130), dus de URI's hierboven kunnen letterlijk in Azure en Google.

Voordelen t.o.v. lokaal: echte https-URL (geen localhost-uitzonderingen in
Azure/Google nodig), bereikbaar vanuit cloud-sessies, en de omgeving lijkt
op wat klanten draaien.

Wat hier **niet** kan: de unit tests (`--test-enable` blijft lokaal en CI) en
alles wat Helpdesk raakt. Odoo's `helpdesk` zit alleen in Enterprise, dus op deze
community-server is de alias-routing onbereikbaar: `x_route_to_team`, de
`x_alias_id`-koppeling naar `helpdesk.team` en het aanmaken van een ticket via
`message_new()`. Die testgevallen blijven lokaal tegen Enterprise-source; een
derdepartij-`helpdesk_community` helpt niet, want de code noemt `helpdesk.team`
en `helpdesk.ticket` letterlijk.

## Fase B — Dogfood (Pantalytics-database)

- [ ] Deploy via CloudPepper naar de Pantalytics-instance
- [ ] Rename-migratie op een bestaande database verifiëren (backup vooraf!)
- [ ] Outlook + Gmail accounts van het team opnieuw verbinden waar nodig
- [ ] 24–48u laten draaien: tokenverversing (vooral Google), cron-gedrag,
      geen mail-verlies
- [ ] Bekende zwakke plek checken: Gmail-serviceaccount dat ná mailbox-aanmaak
      wordt geautoriseerd hertriggert `x_incoming_enabled` niet — handmatig
      controleren

## Fase C — Klanten

- [ ] Juffermans: backup, rename-migratie, module-upgrade, smoke test
- [ ] Overige klantendatabases idem
- [ ] Nazorg: logs eerste dagen monitoren op `[Graph API]` / `[Incoming Mail]`

## Context voor vervolg-sessies

- A1/A2 draaiden tegen de **lokale** Docker op Rutgers laptop (inmiddels
  gestopt). Alle vervolgstappen (A′, B, C) kunnen vanuit een cloud-sessie:
  de testinstance, Pantalytics-Odoo en de klantendatabases zijn bereikbaar
  via de CloudPepper- en Odoo MCP Pro-koppelingen.
- Wat een sessie (lokaal én cloud) niet kan: Azure Portal en Google Cloud
  Console aanpassen — redirect-URI's en secrets zijn handwerk voor Rutger.
- Config-parameters heten bewust nog `x_pan_outlook_pro.*` (geen
  datamigratie nodig bij de rename).

## Besluitregels

- Outlook (A3) moet groen zijn vóór we Gmail (A4) beoordelen — bij een
  Gmail-probleem willen we weten of het aan de client ligt of aan de
  gedeelde laag.
- Elke fase pas in als de vorige groen is; bij twijfel terug naar Docker.
