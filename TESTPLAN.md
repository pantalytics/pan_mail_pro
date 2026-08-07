# Testplan pan_mail_pro

Handmatig testplan voor wat CI niet kan bereiken: een echt consent-scherm, een
refresh token dat na een uur nog werkt, en of een provider de mail werkelijk
aflevert. Alles wat wél te automatiseren is, hoort in `tests/` — zie
ARCHITECTURE.md §12 voor wat daar al staat.

**Huidige versie:** 19.0.5.0.1
**Ringen:** lokaal → testinstance → dogfood → klanten

> **Wat CI inmiddels afdekt** (en hier dus niet meer handmatig hoeft):
> 440 tests, de provider- en AI-contracten, de volledige inkomende pijplijn op
> Graph- én Gmail-data, en sinds #18 het **upgradepad vanaf de vorige release**
> — een echte install van de vorige tag, dan `-u` met de migratiescripts en de
> suite eroverheen. Wat hieronder staat is bewust alleen het restant dat een
> echte tenant vereist.

---

## Openstaand — wacht op Rutger

Dit zijn de enige harde blokkades. Ze kunnen niet vanuit een sessie, lokaal noch
in de cloud: Azure Portal en Google Cloud Console zijn handwerk.

- [ ] **Azure redirect-URI** toevoegen aan appregistratie
      `32a681d3-aeb0-4dc8-bf32-b40874ce062a`
      (portal.azure.com → App registrations → Authentication → platform Web):
      `https://mailpro-dev.cloudpepper.site/microsoft_oauth/callback`
      Kan niet via API: de Lokka-koppeling heeft alleen SharePoint-scopes (403).
- [ ] **Google OAuth-client.** Productie heeft géén `x_pan_outlook_pro.google_*`
      parameters — er is in Odoo nooit een Google-client geconfigureerd. Bestaat
      er al een client in Cloud Console uit de Gmail-ontwikkelfase? Voeg daar
      `https://mailpro-dev.cloudpepper.site/google_oauth/callback` toe en vul
      client ID + secret in op de settings-pagina. Zo niet: nieuwe client.
- [ ] **IMAP/SMTP-credentials** voor een Soverin-testadres (of ander adres met
      IMAP+SMTP) in Bitwarden zetten.

Beide callback-paden zijn geverifieerd tegen `controllers/main.py`, dus de URI's
hierboven kunnen letterlijk worden overgenomen.

---

## Fase A — Lokaal (Docker)

> **Besluit 30-07:** de functionele tests draaien we **niet** lokaal maar op de
> CloudPepper-testinstance (Fase A′). A1/A2 zijn wel lokaal uitgevoerd en
> blijven geldig als regressiebewijs. De lokale Docker-omgeving is gestopt.

### A1. Omgeving en module-upgrade
- [x] `docker-compose up -d` in `.local/`, Odoo bereikbaar op :8069 (30-07)
- [x] `test_db` had al `pan_mail_pro` 19.0.3.0.0 — rename-pad lokaal doorlopen (30-07)
- [x] Logs schoon: alleen een opstart-race (db booting) en een
      Postgres-collation-warning (30-07)

### A2. Unit tests lokaal
- [x] `--test-enable --test-tags=pan_mail_pro`: 0 failed, 0 errors van 148 tests
      tegen de ge-upgradede database (30-07)
- [ ] Opnieuw draaien op 19.0.5.0.1 — het zijn er inmiddels 440, en Helpdesk
      (Enterprise) is alleen hier bereikbaar. Zie A″ voor wat dat specifiek dekt.

### A3. Alias-routing naar Helpdesk (alleen lokaal mogelijk)

Dit kan **nergens anders**. Odoo's `helpdesk` zit alleen in Enterprise, en de
code noemt `helpdesk.team` en `helpdesk.ticket` letterlijk — een
derdepartij-`helpdesk_community` helpt dus niet.

- [ ] Mailbox met `x_route_to_team` en een `x_alias_id` naar een `helpdesk.team`
- [ ] Mail van een onbekende afzender maakt een ticket via `message_new()`
- [ ] De afzender krijgt de Helpdesk-ontvangstbevestiging, en **geen** kopie van
      zijn eigen mail terug (dat is het hele punt van `message_new()`)
- [ ] Reply op dat ticket landt op hetzelfde ticket, niet op een nieuw record

---

## Fase A′ — Functioneel op de CloudPepper-testinstance

Instance: **https://mailpro-dev.cloudpepper.site** — nieuw aangemaakt, niet
bean-forge, zodat demo-data niet in de weg zit. Server `Pantalytics Demo`
(Odoo 19.0 community). Login `admin`, wachtwoord in Bitwarden Secrets Manager
als `MAILPRO_ODOO_ADMIN_PASSWORD` (project `dev`).

- [x] Testinstance aangemaakt (30-07) — `mailpro-dev`, 1 worker
- [x] `pan_mail_pro` gedeployed via git addons attach op branch `19.0` (30-07) —
      webhook + auto-upgrade aan, dus een merge naar `19.0` is binnen ~1 minuut
      live. Log schoon bij eerste start.
- [ ] Bijwerken naar 19.0.5.0.1

Voordelen t.o.v. lokaal: echte https-URL (geen localhost-uitzonderingen in
Azure/Google), bereikbaar vanuit cloud-sessies, en de omgeving lijkt op wat
klanten draaien. Wat hier niet kan: unit tests en alles wat Helpdesk raakt (A3).

### A′1. Microsoft 365 — regressie, was al productie
- [ ] Config invullen (client ID, tenant ID, secret van de bestaande Azure-app)
- [ ] OAuth-flow doorlopen, `pan.mail.account` connected
- [ ] Versturen vanaf personal mailbox; mail komt aan, Message-ID opgeslagen
- [ ] Versturen vanaf shared mailbox (SendAs met eigen token)
- [ ] Inkomende mail gesynct; geen dubbele notificaties
- [ ] Reply van buitenaf landt in dezelfde thread (References, dan conversationId)
- [ ] Mailbox zonder werkende credentials → mail faalt, niet via SMTP gelekt

### A′2. Google Workspace
- [ ] OAuth-flow doorlopen (`access_type=offline` + `prompt=consent`; refresh
      token daadwerkelijk opgeslagen)
- [ ] Versturen vanaf Gmail-account (RFC822 MIME, Message-ID door ons gezet)
- [ ] Inkomende mail gesynct (INBOX-label)
- [ ] Reply landt in dezelfde thread (threadId)
- [ ] Shared/Workspace-adres zónder owner: credentials-check werkt
- [ ] **Na een uur nog een mail versturen** — dit is de enige test die bewijst
      dat het refresh token echt werkt; CI kan dit per definitie niet

### A′3. IMAP/SMTP (Soverin)
- [ ] Account aanmaken (Instellingen → Technisch → E-mail → E-mailaccounts),
      provider *IMAP / SMTP*, adres op `soverin.net` → servers voorgevuld
- [ ] **Test Connection**: IMAP én SMTP groen; verkeerd wachtwoord zegt wélke
      helft faalt
- [ ] Mailbox met provider *IMAP / SMTP* op hetzelfde adres; shared vraagt geen owner
- [ ] Versturen; mail komt aan én staat in de Sent-map van de mailbox zelf
      (APPEND) — controleer in Roundcube of eigen mailclient
- [ ] Inkomende mail gesynct (INBOX), oudste eerst, cursor loopt door
- [ ] Reply landt in dezelfde thread (References-root)
- [ ] Eigen verzonden mail niet opnieuw geïmporteerd (X-Odoo-loop guard)
- [ ] Credentials leeghalen → mailbox `error`, mail faalt, niet via SMTP gelekt
- [ ] Server met afwijkende Sent-map (bijv. `INBOX.Verzonden`): override werkt

---

## Fase A″ — Regressie op wat 19.0.4.0.0 en 19.0.5.0.0 veranderden

Opzettelijke gedragsveranderingen. Ze moeten precies doen wat er staat.

### Geen stille omleiding meer (19.0.5.0.0)
- [ ] Zet bij een testgebruiker de standaardmailbox op een mailbox zonder
      werkende credentials en verstuur naar een externe klant. Verwacht: een
      foutmelding die zegt wat er mis is, én de mail in Instellingen → Technisch
      → E-mail → E-mails met status *Uitzondering* en dezelfde reden. Verwacht
      **niet** dat hij alsnog vanaf `notifications@` vertrekt.
- [ ] Eén foute mail blokkeert de rest niet: verstuur in dezelfde actie een
      onrouteerbare en een goede mail. De goede moet verstuurd zijn.
- [ ] De mailwachtrij loopt door: laat een onrouteerbare mail staan en
      controleer dat de cron de mails erachter alsnog verstuurt.

### Eén sync-instelling per mailbox (19.0.5.0.0)
- [ ] Het mailboxformulier toont "Inkomende mail" met drie keuzes in plaats van
      vier schakelaars. Controleer op een **bestaande** database dat de keuze
      bewaard is (`x_sync_mode` is niet gemigreerd, alleen de afgeleide velden
      zijn weg).
- [ ] Verbinden/loskoppelen via Mijn Profiel → Mail Pro, per provider die de
      klant gebruikt. De knoppen heten "Connect Mailbox" / "Disconnect" en zijn
      niet meer per provider.

### Interne domeinen zijn een slot (19.0.3.4.0)
- [ ] Op een database zonder interne domeinen: een mailbox op sync zetten moet
      **weigeren** met uitleg
- [ ] Domeinen invullen (Odoo stelt ze voor), daarna kan het wel
- [ ] Lijst achteraf leeghalen → een lopende sync stopt en zet de mailbox op error
- [ ] Mail van een intern domein wordt niet gesynct; met "Sync internal email"
      aan wél

### Zichtbaarheid (19.0.4.0.0) — nooit functioneel getest
- [ ] **Mail Routing** krijgt een rij per afgeleverde mail, met regel en
      confidence. Controleer de vier uitkomsten: `threaded`, `created`,
      `fallback`, `sent_item`
- [ ] `needs_review` staat aan bij `fallback` en bij `created` mét kandidaten —
      en **niet** bij een gewoon gethreade mail of een sent item
- [ ] **Triage**: een mail van een onbekende afzender op een mailbox met
      "hou onbekende afzenders vast" belandt in de wachtrij, mét onderwerp maar
      **zonder** body in de database; de body wordt pas bij openen opgehaald
- [ ] Een geblokkeerd contact (`x_email_sync_blocked`) komt **niet** in de
      triage-wachtrij terecht — ook de metadata niet
- [ ] **Link Coverage** geeft plausibele aantallen over 30/90/365 dagen

### AI staat uit (19.0.4.0.0)
- [ ] Zonder API-key: geen enkele AI-aanroep, geen fouten, module gedraagt zich
      alsof de functie er niet is
- [ ] Met key: een suggestie verschijnt op een triage-item, maar routeert niets
      automatisch (`x_routing_smart` blijft dicht)
- [ ] Uitgaande mail en de inkomende cron worden nooit vertraagd door AI

### Originele maildatum (19.0.5.0.1, issue #1)
- [ ] Zet **Import From** op een datum ver terug en laat historische mail
      binnenkomen. In de chatter moet elke mail de datum dragen waarop hij
      **verstuurd** is, niet de dag van de import. Dit was de bug: alles kwam op
      één middag te staan.
- [ ] Al eerder geïmporteerde mail blijft de oude (foute) datum houden — de fix
      werkt alleen vooruit. Bepaal per klant of een herimport de moeite is.

---

## Fase B — Dogfood (Pantalytics-database)

- [ ] Deploy via CloudPepper naar de Pantalytics-instance
- [ ] Backup vooraf, dan module-upgrade
- [ ] Outlook + Gmail accounts van het team opnieuw verbinden waar nodig
- [ ] 24–48u laten draaien: tokenverversing (vooral Google), cron-gedrag,
      geen mailverlies
- [ ] Een Gmail-serviceaccount dat **ná** mailbox-aanmaak wordt geautoriseerd
      moet binnen een minuut gaan syncen (`_has_working_credentials()` wordt op
      het moment zelf gevraagd; `x_incoming_enabled` bestaat niet meer)
- [ ] Mail Routing na een dag bekijken: hoeveel staat er op `needs_review`, en
      klopt dat? Dit is meteen de eerste echte meting van de matcher

---

## Fase C — Klanten

- [ ] Juffermans: backup, module-upgrade, smoke test
- [ ] Overige klantendatabases idem
- [ ] Nazorg: logs eerste dagen monitoren op `[Graph API]` / `[Incoming Mail]`
- [ ] Per klant beslissen of interne domeinen goed staan — bij een upgrade van
      vóór 19.0.3.4.0 staat de lijst leeg en stopt de sync tot het is ingevuld

---

## Context voor vervolg-sessies

- A1/A2 draaiden tegen de **lokale** Docker op Rutgers laptop (inmiddels
  gestopt). A′, B en C kunnen vanuit een cloud-sessie: de testinstance,
  Pantalytics-Odoo en de klantendatabases zijn bereikbaar via de CloudPepper-
  en Odoo MCP Pro-koppelingen.
- Wat een sessie niet kan: Azure Portal en Google Cloud Console aanpassen.
- Alleen A3 (Helpdesk) vereist Enterprise-source en dus de lokale Docker.
- Config-parameters heten bewust nog `x_pan_outlook_pro.*` — geen datamigratie
  nodig geweest bij de rename. Modelnamen (`microsoft.*`) en velden
  (`x_microsoft_*`) idem; zie ARCHITECTURE.md §1.

## Besluitregels

- Microsoft (A′1) moet groen zijn vóór we Gmail (A′2) beoordelen — bij een
  Gmail-probleem willen we weten of het aan de client ligt of aan de gedeelde laag.
- Elke fase pas in als de vorige groen is; bij twijfel terug naar Docker.
- Een testgeval dat hier twee keer handmatig is gelopen en stabiel bleek, hoort
  in `tests/` — niet in dit bestand.
