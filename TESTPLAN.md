# Testplan pan_mail_pro v19.0.3.0.0 — rename + provider-refactor

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
- [x] Herhaald op 17-08 tegen `odoo:19.0` community (CI-reproductie, verse
      `ci_test`-db): install + upgrade beide groen, 0 failed van 148

### A2b. UI-smoke lokaal (17-08, community-image + Playwright)
- [x] Settings → Mail Pro: beide provider-secties renderen; "Connect Google"
      zonder credentials geeft nette UserError, geen traceback
- [x] Mailbox-formulier: provider-switch naar Gmail werkt; save zonder owner
      correct geblokkeerd door ValidationError
- [x] Composer: setup-warning + "Send From"-dropdown renderen
- [x] OAuth-callbacks met bogus params: redirects, geen 500's
- [x] Gevonden + gefixt in v19.0.3.0.1: `widget="timeago"` bestaat niet in
      Odoo 19 (viel stil terug op datetime); **gebruikers konden Google niet
      koppelen** — `action_connect_google` zat in het model maar in geen
      enkele view; disconnect-knoppen (MS + Google) ontbraken ook; plus
      Microsoft-only teksten in settings/empty-state/menunaam

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

## Fase A′ — Functioneel testen op CloudPepper-testinstance

Vervangt A3/A4 hierboven; de checklists daar blijven de inhoudelijke
testgevallen, alleen de omgeving verandert.

- [ ] Testinstance kiezen of aanmaken via CloudPepper (Odoo 19-server;
      bestaande kandidaat: bean-forge, anders nieuwe instance)
- [ ] `pan_mail_pro` 19.0.3.0.0 op de instance deployen (git addons attach,
      branch `19.0`)
- [ ] Microsoft-config invullen op de settings-pagina (client ID, tenant ID,
      client secret van de bestaande Azure-app `32a681d3-...`)
- [ ] Redirect-URI `https://<instance>.cloudpepper.site/microsoft_oauth/callback`
      toevoegen aan de Azure-appregistratie (handmatig, portal.azure.com)
- [ ] A3-checklist (Outlook) doorlopen op de instance
- [ ] Google OAuth-client regelen (Cloud Console) met redirect-URI
      `https://<instance>.cloudpepper.site/google_oauth/callback`
- [ ] A4-checklist (Gmail) doorlopen op de instance

Voordelen t.o.v. lokaal: echte https-URL (geen localhost-uitzonderingen in
Azure/Google nodig), bereikbaar vanuit cloud-sessies, en de omgeving lijkt
op wat klanten draaien.

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
