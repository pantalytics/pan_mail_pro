# Testplan pan_mail_pro

Handmatig testplan voor wat CI niet kan bereiken: een echt consent-scherm, een
refresh token dat na een uur nog werkt, en of een provider de mail werkelijk
aflevert. Alles wat wél te automatiseren is, hoort in `tests/` — zie
ARCHITECTURE.md §12 voor wat daar al staat.

**Huidige versie:** 19.0.26.0.0
**Ringen:** lokaal → testinstance → dogfood → klanten

> **IMAP/SMTP is sinds 19.0.6.5.5 geautomatiseerd.** `tools/ci_odoo.sh` start
> een GreenMail-container -- een echte IMAP4- en SMTP-server -- en
> `tests/test_imap_live.py` stuurt daar een mail doorheen: inloggen, versturen,
> ophalen, de kopie in Verzonden. Fase A'3 hieronder is daarmee grotendeels
> afgedekt; wat overblijft is aflevering aan de buitenwereld, want GreenMail
> stuurt niets door.
>
> **Wat CI inmiddels afdekt** (en hier dus niet meer handmatig hoeft):
> de tests, het providercontract, de volledige inkomende pijplijn op
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
- [ ] **Google OAuth-client.** Productie heeft géén `pan_mail_pro.google_*`
      parameters — er is in Odoo nooit een Google-client geconfigureerd. Bestaat
      er al een client in Cloud Console uit de Gmail-ontwikkelfase? Voeg daar
      `https://mailpro-dev.cloudpepper.site/google_oauth/callback` toe en vul
      client ID + secret in op de settings-pagina. Zo niet: nieuwe client.
- [ ] **IMAP/SMTP-credentials** voor een Soverin-testadres (of ander adres met
      IMAP+SMTP) in Bitwarden zetten. Sinds GreenMail alleen nog nodig voor de
      ene vraag die een echte hoster moet beantwoorden: komt de mail werkelijk
      aan bij een externe ontvanger. Het protocolwerk staat in CI.

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
- [ ] Opnieuw draaien op 19.0.26.0.0 — Helpdesk (Enterprise) is alleen hier
      bereikbaar. Zie A3 voor wat dat specifiek dekt; de alias-routing zelf
      (`alias_defaults`, geen follower, geen kopie naar de afzender) draait in
      CI op een `crm.lead` (`tests/test_incoming_sync.py`).

### A3. Alias-routing naar Helpdesk (alleen lokaal mogelijk)

Dit kan **nergens anders**. Odoo's `helpdesk` zit alleen in Enterprise, en de
code noemt `helpdesk.team` en `helpdesk.ticket` letterlijk — een
derdepartij-`helpdesk_community` helpt dus niet.

- [ ] Mailbox met `route_to_team` en een `alias_id` naar een `helpdesk.team`
- [ ] Mail van een onbekende afzender maakt een ticket via `message_new()`
- [ ] De afzender krijgt de Helpdesk-ontvangstbevestiging, en **geen** kopie van
      zijn eigen mail terug (dat is het hele punt van `message_new()`)
- [ ] Reply op dat ticket landt op hetzelfde ticket, niet op een nieuw record

---

## Fase A′ — Functioneel op de CloudPepper-testinstance

Instance: **https://mailpro-dev.cloudpepper.site** — nieuw aangemaakt, niet
bean-forge, zodat demo-data niet in de weg zit. Server `Pantalytics Demo`
(Odoo 19.0 community). Login `admin`, wachtwoord in Bitwarden Secrets Manager
als `CLOUDPEPPER_MAILPRO_DEV_ADMIN_PASSWORD` (project `prod`).

- [x] Testinstance aangemaakt (30-07) — `mailpro-dev`, 1 worker
- [x] `pan_mail_pro` gedeployed via git addons attach op branch `19.0` (30-07) —
      webhook aan, dus een merge naar `19.0` is binnen ~1 minuut live. Log
      schoon bij eerste start.
- [ ] Bijwerken naar 19.0.26.0.0. Een push herstart, maar upgradet niet
      (issue #134): na een merge met versiebump Apps → Mail Pro → Upgrade, en
      daarna in `ir_module_module` controleren dat de nieuwe versie er staat.

Voordelen t.o.v. lokaal: echte https-URL (geen localhost-uitzonderingen in
Azure/Google), bereikbaar vanuit cloud-sessies, en de omgeving lijkt op wat
klanten draaien. Wat hier niet kan: unit tests en alles wat Helpdesk raakt (A3).

### A′0. Het echte consent-scherm (OAuth)

CI mockt elke HTTP-call naar Microsoft en Google; het consent-scherm, de
redirect-URI en wat de callback daarna opslaat zijn alleen hier te zien. De
dev-instance heeft een publieke https-URL met dezelfde vorm als productie, wat
`localhost:8069` nooit heeft.

- [ ] Instellingen → Mail Pro, stap 2: provider kiezen, client ID / secret
      (en bij Microsoft de tenant) invullen, opslaan. Het verificatiedialoog
      zegt of de registratie klopt, en biedt daarna het inloggen aan
- [ ] De **Callback URL** op het providerformulier letterlijk kopiëren naar
      Azure / Google Cloud Console. Een afwijkende URI faalt pas op het
      consent-scherm, nooit in Odoo
- [ ] Als admin inloggen via Mijn Profiel → Mail Pro → **Connect mailbox**:
      consent-scherm, terug in Odoo, "Connected as" toont het adres waarmee
      is ingelogd (niet per se het Odoo-adres)
- [ ] Instellingen → Technisch → E-mail → Mail Pro → Email accounts: één
      account per adres per provider, `connected` aan
- [ ] Consent weigeren of annuleren op het providerscherm: terug in Odoo op
      de pagina "Connection Failed" met een leesbare reden, geen half
      account. Een callback die wél een code had maar daarna faalt (token
      ruil, `/me` zonder adres) staat als `oauth.callback_failed` in Errors
- [ ] Terwijl je verbonden bent als A nog eens consent geven als B: geweigerd
      met de melding dat je als A verbonden bent, de rij blijft A. Na
      **Disconnect** wordt B aangenomen, de persoonlijke mailbox van A
      gearchiveerd en is hij niet meer de standaardmailbox

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

> Geautomatiseerd in `tests/test_imap_live.py` tegen GreenMail: inloggen op
> beide helften, versturen, ophalen, de UID-referentie heen en terug, de kopie
> in Verzonden, en een send die blijft slagen als de Verzonden-map ontbreekt.
> Wat hieronder aangevinkt moet worden is alleen nog wat een echte hoster
> anders doet: aflevering naar buiten, TLS, en een afwijkende mapnaam.

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

### Eén sync-ladder per mailbox (19.0.8.0.0)

Het mailboxformulier vraagt één ding: hoeveel van deze mailbox leest Odoo
terug (`sync_level`, tab Sync settings). Vier treden, elke trede houdt strikt
meer dan de vorige. Replies op mail die Odoo al heeft komen op élke trede
binnen; dat is geen instelling.

- [ ] **Replies, in Odoo only** (standaard): een reply van een klant op een
      mail uit Odoo landt op het record; een nieuwe mail van dezelfde klant
      komt niet binnen; de Sent-map wordt niet gelezen
- [ ] **Replies, in Odoo and your mail app**: een antwoord dat de eigenaar in
      Outlook/Gmail op een Odoo-thread typt, verschijnt op het record; een
      nieuwe mail die hij vanuit zijn mailclient start blijft buiten
- [ ] **Replies and new email, existing contacts only**: een nieuwe mail van
      een bestaand contact komt binnen (contact-chatter, of team-alias als
      "Route new conversations to a team" aanstaat); van een onbekende
      afzender niet, en in het log staat één regel met de mailbox, de
      Message-ID en de reden
- [ ] **Replies and new email, everyone**: dezelfde mail van de onbekende
      afzender komt alsnog binnen en de afzender wordt een contact. De
      waarschuwing over nieuwsbrieven en privémail staat onder de keuze
- [ ] Op een **bestaande** database na de upgrade: de oude keuze is op de
      juiste trede gezet (`migrations/19.0.8.0.0/`, `tests/test_sync_level_migration.py`)
- [ ] Mijn Profiel → Mail Pro: een gewone gebruiker ziet dezelfde ladder voor
      zijn eigen persoonlijke mailbox en kan hem opslaan zonder Mailbox
      Manager te zijn; de melding bij "everyone" staat ook daar
- [ ] Verbinden/loskoppelen via Mijn Profiel → Mail Pro. De knoppen heten
      "Connect mailbox" / "Disconnect" en zijn niet per provider.

### Interne domeinen zijn een slot (19.0.3.4.0)
- [ ] Op een database zonder interne domeinen: een mailbox op sync zetten moet
      **weigeren** met uitleg
- [ ] Domeinen invullen (Odoo stelt ze voor), daarna kan het wel
- [ ] Lijst achteraf leeghalen → een lopende sync stopt en zet de mailbox op error
- [ ] Mail van een intern domein wordt nooit gesynct. Er is geen schakelaar
      meer om dat uit te zetten (19.0.6.4.0, ARCHITECTURE.md §9.12)

### Zichtbaarheid (19.0.4.0.0) — nooit functioneel getest
- [ ] **Mail Routing** krijgt een rij per afgeleverde mail, met regel en
      confidence. Controleer de vier uitkomsten: `threaded`, `created`,
      `fallback`, `sent_item`
- [ ] `needs_review` staat aan bij `fallback` en bij `created` mét kandidaten —
      en **niet** bij een gewoon gethreade mail of een sent item
- [ ] **Link Coverage** geeft plausibele aantallen over 30/90/365 dagen

### Triage en AI zijn weg (19.0.7.0.0)
- [ ] Na de upgrade bestaat de tegel **Communication** op het beginscherm niet
      meer; All Communication, Link Coverage, Internal Domains en Mail Routing
      staan onder Settings → Technical → Email → Mail Pro
- [ ] Een mail van een onbekende afzender op een mailbox op trede "Replies
      and new email, existing contacts only" wordt geweigerd. In het log
      staat één regel met de mailbox, de Message-ID en `unknown_contact`; er
      wordt niets opgeslagen
- [ ] Zet de mailbox op "Replies and new email, everyone" en sync opnieuw:
      dezelfde mail komt alsnog binnen. Dit is de vervanger van de
      triage-wachtrij
- [ ] `pan_mail_item` bestaat niet meer als tabel, en er zijn geen
      AI-cronjobs of AI-modellen over

### Originele maildatum (19.0.5.0.1, issue #1)
- [ ] Zet **Import From** op een datum ver terug en laat historische mail
      binnenkomen. In de chatter moet elke mail de datum dragen waarop hij
      **verstuurd** is, niet de dag van de import. Dit was de bug: alles kwam op
      één middag te staan.
- [ ] Al eerder geïmporteerde mail blijft de oude (foute) datum houden — de fix
      werkt alleen vooruit. Bepaal per klant of een herimport de moeite is.

### Eigen mailbox live lezen en "Add to Odoo" (19.0.16.0.0)

CI test dit tegen gemockte providers (`tests/test_live_mailbox.py`); wat
alleen een echte mailbox laat zien is of de lijst klopt met wat Outlook/Gmail
toont, en of er werkelijk niets wordt opgeslagen.

- [ ] Inbox openen als eigenaar van een **persoonlijke** mailbox: onder die
      mailbox staat de map **All email**, en onder geen andere. Een gedeelde
      mailbox heeft hem niet, ook niet voor een Mailbox Manager
- [ ] De lijst is de echte inbox van de provider, nieuwste eerst, met het
      filter "in Odoo / not in Odoo". Een mail die wel in Odoo staat opent de
      bestaande conversatie
- [ ] Een mail die niet in Odoo staat opent alleen-lezen met **Add to Odoo**
      eronder; geen Reply. Na de klik staat hij op het juiste record (of op
      het contact) en verdwijnt hij uit "not in Odoo"
- [ ] Een mail van een intern domein of van een geblokkeerd contact: Add to
      Odoo importeert wel bij intern domein (de knop is de bewuste
      uitzondering), **nooit** bij een geblokkeerd contact
- [ ] `mail_message` groeit niet door alleen bladeren: tel de rijen voor en na
      een minuut lezen zonder Add to Odoo

### Leesstatus heen en terug (19.0.15.4.0, ARCHITECTURE.md §9.18)

De provider is eigenaar van gelezen/ongelezen; Odoo spiegelt. CI bewijst de
schrijfpaden met een fake; of Outlook en Gmail het ook zo zien kan alleen hier.

- [ ] Markeer in Outlook (of Gmail) een gesyncte mail ongelezen, open de
      Inbox in Odoo: de conversatie heeft de stip en is vet (verversing bij
      openen, hooguit één provider-call per mailbox per minuut)
- [ ] Open de conversatie in Odoo: in Outlook/Gmail is de mail nu gelezen
- [ ] **Mark unread** in Odoo (knop in de conversatie of rijmenu ⋮): in
      Outlook/Gmail is alleen de nieuwste inkomende mail ongelezen, niet de
      hele conversatie
- [ ] Odoo's eigen bel: een @-vermelding op het record verdwijnt bij het
      lezen in de Inbox; Mark unread brengt hem **niet** terug
- [ ] Provider tijdelijk onbereikbaar (token intrekken): Mark read/unread
      werkt in Odoo, geeft geen fout, en de volgende verversing met werkend
      token zet de provider leidend

### Koppeling met Pantalytics (sinds 19.0.14.1.2, verplicht sinds #126)

De server staat op mcp.pantalytics.com; CI mockt elke call ernaartoe. Connect,
goedkeuring en de dagelijkse heartbeat zijn alleen tegen de echte server te
zien.

- [ ] Nieuwe database: Instellingen → Mail Pro toont alleen stap 1 met
      **Connect to Pantalytics**; de Inbox toont één kaart met één knop en
      geen panes; de sync-cron haalt niets op
- [ ] **Connect to Pantalytics**: nieuw tabblad op Pantalytics met de code al
      in de link, inloggen, controleren dat de pagina déze Odoo noemt,
      goedkeuren, knop terug naar Odoo (`/mail_pro/pantalytics/return`). Stap
      1 zegt nu "Connected by" met het Pantalytics-account, de Odoo-gebruiker
      en het tijdstip
- [ ] Terugknop niet gebruiken maar het tabblad sluiten: in Odoo staat de
      koppeling op "Waiting for approval"; **Check approval** haalt de sleutel
      alsnog op. Te vroeg drukken geeft een melding, geen fout
- [ ] Na verbinden: Inbox en sync werken binnen een minuut, zonder herladen
      van de instellingenpagina
- [ ] Cron **Mail Pro: Pantalytics Heartbeat** handmatig draaien: de
      workspace op Pantalytics toont de database, versies, aantallen en de
      drie setup-antwoorden. Wat er over de lijn gaat is `_heartbeat_body()`,
      niets anders: geen adres, onderwerp of naam
- [ ] Pantalytics onbereikbaar maken (hosts-file): de heartbeat faalt met
      één rij in Errors (`license.heartbeat_failed`), de sync blijft werken op
      de gecachte entitlement; na 14 dagen zonder antwoord niet meer
- [ ] **Disconnect** op stap 1: sync stopt, Inbox toont weer de kaart,
      uitgaande mail blijft werken (nooit gegijzeld)
- [ ] Op een geneutraliseerde kopie (backup terugzetten): geen Connect-knop,
      geen heartbeat, Sync Now zegt waarom

### Het Errors-scherm (19.0.23.0.0)

Instellingen → Technisch → E-mail → Mail Pro → **Errors**: elke fout die de
module vangt, dertig dagen, gegroepeerd op code, met de traceback op de rij.
CI bewijst dat elke code in de lijst staat en dat een rij een rollback
overleeft; of het scherm leest, niet.

- [ ] Een sync laten falen (token intrekken): binnen een minuut een rij
      `incoming.mailbox_failed` met mailbox, provider en traceback; de
      mailbox zelf staat op error met dezelfde reden
- [ ] Een mail laten falen (mailbox zonder credentials als afzender): rij
      `outgoing.no_route` of `outgoing.send_failed`, en in Odoo's E-mails
      dezelfde reden in `failure_reason`
- [ ] Standaardweergave is gegroepeerd op code; filters Errors / Warnings,
      Incoming / Outgoing / Inbox en Today werken; een rij openen toont
      "What happened" met de traceback
- [ ] Een throttle (`incoming.throttled`) is een waarschuwing, geen fout: de
      rij is grijs en de mailbox-badge wordt niet rood
- [ ] Na een nieuwe soort fout gaat binnen een minuut een heartbeat uit met
      alleen de code en een aantal; op Pantalytics verschijnt een
      `heartbeat_error`-event. Rijen ouder dan dertig dagen zijn weg na de
      opruimcron

### Geverifieerde setup (19.0.28.0.0, scopes opt-in sinds 19.0.28.1.0)

De module vraagt de provider nu zelf of een sign-in een mailbox kan lezen en
onthoudt per (mailbox, sign-in) hoe elke verzending afliep
(`pan.mail.mailbox.access`). Het ontwerp staat in
`docs/plans/verified-setup.md`; de antwoorden die CI heeft nagespeeld in
`docs/research/provider-probes.md`, met per cel of die ooit op een echte
tenant is gezien. Dat laatste is wat dit plan bewijst.

**Geen Azure-wijziging nodig.** De leesprobe en de verzenduitkomsten gebruiken
niets dat een bestaande grant mist. Deel 2 is het enige dat `MailboxSettings.Read`
en `User.ReadBasic.All` vraagt, en dat is opt-in per database: alleen op onze
eigen tenant doen, nooit als eis voor een klant.

**Deel 1: standaardpad, op odoo.pantalytics.com (gedaan 2026-10-07 tot en
met de eerste vier).**

- [x] Na de upgrade leest elke mailbox healthy of met de zin die de tabel
      geeft, nooit "Reconnect": soort is *Unknown* en dat kleurt niets
- [x] Mailbox → **Check mailbox**: rij voor de eigen sign-in met *can read:
      yes*; `access_checked_date` gezet; geen rij in Errors
- [x] Testmail vanaf notifications@: aangekomen; rij *can send: yes*; stap 4
      van de checklist groen (Settings → Mail Pro)
- [x] Disconnect, dan Connect mailbox onder My Preferences → Mail Pro: het
      consent-scherm is ongewijzigd, geen *needs admin approval*
- [ ] **De Emovr-vorm.** Maak een shared mailbox voor een adres waar jouw
      sign-in geen Full Access op heeft (bijv. `daniel@pantalytics.com`
      met jou als owner). Check mailbox: badge *error*, zin "rutger@… cannot
      read daniel@…. An administrator grants Full Access…"; rij in Errors
      onder `access.read_denied`. Dit is de check die bij Emovr zes maanden
      ontbrak
- [ ] Verstuur toch een mail vanaf die mailbox: `failure_reason` draagt
      dezelfde zin, de rij zegt *can send: no*, Errors krijgt
      `access.send_denied`. Geen omleiding naar een ander adres
- [ ] Geef in het Exchange admin center Full Access (nog geen Send As),
      wacht een paar minuten, Check mailbox: *can read: yes*, badge weg.
      Verstuur: `ErrorSendAsDenied`, zin noemt Send As. Geef Send As,
      verstuur: *can send: yes*, healthy. De tabel onthoudt de `yes` en een
      latere check overschrijft die niet met `unknown`
- [ ] Een adres dat niet bestaat als mailbox (een distributielijst of een
      typefout): Check mailbox zegt "There is no mailbox at …"; badge *error*;
      de mailboxes-regel op Settings toont de alert
- [ ] Het uurlijkse pad: wacht een uur, `access_checked_date` is bijgewerkt
      zonder dat iemand op Check mailbox drukte; de sync zelf is niet
      vertraagd
- [ ] Gmail (mailpro-dev of een Workspace-account): Check mailbox op de eigen
      mailbox leest *yes* via het profiel; een shared Gmail-adres zonder
      send-as geeft "X has no send-as address for Y"
- [ ] IMAP (Soverin): Check mailbox doet `SELECT INBOX` en `MAIL FROM`/`RSET`;
      een afzender die de server weigert geeft de 5xx-regel van de server

**Deel 2: opt-in, alleen op de Pantalytics-tenant.** Dit vult de cellen
*unverified* in `provider-probes.md` en is de enige reden om onze eigen
Azure-registratie aan te raken.

- [ ] Azure: `MailboxSettings.Read` en `User.ReadBasic.All` (delegated)
      toevoegen aan de app-registratie, admin consent opnieuw geven
- [ ] Odoo: Settings → Technical → System Parameters,
      `pan_mail_pro.graph_inspect_scopes` = `True`
- [ ] Reconnect één sign-in. Op de account (Settings → Technical → Email →
      Mail Pro → Accounts) staan beide scopes in *granted scopes*; `tid` en
      de principal name zijn gevuld
- [ ] Check mailbox: Kind wordt *User* op een persoonlijk adres, *Shared* op
      `notifications@` (als dat in Exchange echt een shared mailbox is). Noteer
      het antwoord van `userPurpose` in `provider-probes.md` en zet de
      cel op *observed*
- [ ] De Emovr-vorm met soort bekend: user-adres als shared type met een
      owner die als een ander adres is aangemeld: badge *warning*, zin
      "… is a user account. Connect it as its own sign-in, or grant …"
- [ ] Een alias (proxy address op een bestaande mailbox) als mailbox: Kind
      *Alias*, badge *error*, zin "… is an alias on another mailbox". Noteer
      de exacte 404-code van de directory-lookup in `provider-probes.md`
- [ ] Een room of equipment mailbox: Kind *Resource*, *can send: no*
- [ ] Een sign-in die níét is gereconnect: Check mailbox slaat rung 3 en 4
      stil over, soort blijft *Unknown*, geen zin, geen rij in Errors
- [ ] Parameter weer op `False` of verwijderen: nieuwe connects vragen de
      scopes niet meer; bestaande grants houden ze en de rungen blijven
      werken voor die accounts

**Deel 3: Emovr.** Na de upgrade van odoo-customer-emovr: Check mailbox op
`info@emovr.nl` met Robert als owner geeft precies de zin uit deel 1
(`robert@stalero.nl cannot read info@emovr.nl`), en Daniëlle, aangemeld als
`info@` zelf, leest *yes*. De fix is één van de twee uitwegen uit de zin, en
de badge bewijst welke is gekozen.

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

- [ ] Eerste klantendatabase: backup, module-upgrade, smoke test
- [ ] Overige klantendatabases idem
- [ ] Nazorg: logs eerste dagen monitoren op `[Outgoing Mail]` / `[Incoming Mail]`
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
- Sinds 19.0.6.0.0 heten config-parameters `pan_mail_pro.*`, het mailboxmodel
  `pan.mail.mailbox` en de velden provider-neutraal; `migrations/19.0.6.0.0/`
  hernoemt bestaande databases. Een klant die van vóór 19.0.6.0.0 komt:
  na de upgrade controleren dat Settings → Mail Pro de Azure-gegevens nog
  toont (de sleutel-rename) en dat een reply op een oude thread nog threadt.

## Besluitregels

- Microsoft (A′1) moet groen zijn vóór we Gmail (A′2) beoordelen — bij een
  Gmail-probleem willen we weten of het aan de client ligt of aan de gedeelde laag.
- Elke fase pas in als de vorige groen is; bij twijfel terug naar Docker.
- Een testgeval dat hier twee keer handmatig is gelopen en stabiel bleek, hoort
  in `tests/` — niet in dit bestand.
