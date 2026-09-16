# Survey: email in Odoo (September 2026)

Two questions sent to the Odoo MCP Pro list on 15 September 2026 from
`rutger@pantalytics.com`:

1. Do you (or your client) send emails to customers from inside Odoo (like
   quotes, invoices, replies from the chatter)? Or does that mostly happen in
   Outlook or Gmail?
2. And if you do: how well does that work? Is that a problem for you, have you
   looked at alternatives, or is it fine as it is?

**28 replies** by 16 September 2026 09:53 UTC. All of them are below, verbatim,
signatures and quoted mail stripped. This is customer correspondence: keep it
in this repo, do not publish it to the knowledge base.

## Where mail is sent from

| Bucket | Count | Who |
|---|---|---|
| Inside Odoo | 18 | Firestorm, Ice-Mountain, Round Solutions, Jobyfull, Moka Origins, Sceba, Finishing Tech, Marea Outdoor, Bizolve, GestionFyC, UnitBit, Scewo (x2), Deepti (via Brevo), IT Optimizer, 3D Playmakers, Devooght, Hatton Locks |
| Mixed (transactional from Odoo, conversation outside) | 1 | Sail Specials |
| Outside Odoo | 8 | Ryan Whittaker, Measurit, Mevo-America, Luvre, Poel Group, Pressure Control Solutions, JB Herman, ElementX |
| Not applicable | 1 | Sana Swiss (still on Odoo 17, wants the app after migrating) |

Two thirds already send from Odoo. Almost nobody says it is fine as it is.

## What is broken, by count

| # | Theme | Said by |
|---|---|---|
| 5 | **Setup is the wall.** Catchall, bounce, dedicated address, Microsoft backend config "very difficult for non-IT specialist" | Firestorm, Sceba, Round Solutions, Deepti, Hatton Locks |
| 4 | **No CC/BCC, and the recipient cannot see who is on the thread** | Jobyfull, PCS, GestionFyC, Scewo |
| 3 | **Followers.** Auto-added, wrong default recipients, invisible to the customer | Finishing Tech, PCS, Scewo |
| 3 | **No conversation thread.** Each chatter reply arrives standalone, no quoted history, so support conversations do not work | Ice-Mountain, PCS, Sail Specials |
| 3 | **Mail is scattered.** No central inbox in Odoo, and what is sent from Odoo never shows up in Outlook | PCS, Firestorm, Round Solutions |
| 3 | **Formatting.** Composer output is poor, HTML leaks into the text, signatures are better in Outlook | PCS, IT Optimizer, Moka Origins |
| 3 | **From address.** Mail arrives from `notification@...` or the company, not the person, and replying to it fails | Scewo, Hatton Locks, Sceba |
| 2 | **Deliverability.** Customers did not receive the mail, or it lands in spam | Ryan Whittaker, 3D Playmakers |
| 1 | Composer UX: switching template destroys the written draft; no view of earlier mails while composing | Jobyfull |
| 1 | Send through the MCP connector using the standard UI and email wizard | Moka Origins |

Everything in the top three rows is what Mail Pro already aims at. The "two
thirds already send from Odoo, and the complaint is never delivery but
conversation" reading is the one to check before the next roadmap call.

## The replies

### Sent from inside Odoo

**Sebastian Thalhammer, Firestorm Digital** (`sebastian@firestorm-digital.com`, 16 Sep)
> First it took A LOT of try and error to make emails work in odoo in and of
> itself. I had to learn first how the catchall system and everything works and
> I mistook the concept of what I was used to in HubSpot.
>
> Now I am using odoo for every system email (invoices, helpdesk, billing,
> etc.) - I have tinkered around with alias emails for projects but nothing
> really useful.
>
> A gap exists when I want to use M365 and Odoo. More often than not I find
> myself going back and forth between the two systems.
>
> Ideally I want to have it so that I can send emails from my personal account
> to clients and then attach it to speciifc things in odoo - ideally it would
> do that automatically.
>
> but now I have scattered emails in outlook and odoo.
>
> I try to use odoo as much as I can but sometimes I need to use M365 instead
> (e.g. video messages, delayed sending) and I would like to see the sent and
> received emails in my outlook as well. so far that's not happening.

**Louis Dekegeleer, Ice-Mountain** (`louis@ice-mountain.com`, 16 Sep)
> We do use chatter to send 'emails' through Odoo.
>
> One of the issues our salespeople encounter is that when they send a response
> to a customer through the chatter; the customers don't receive the full
> conversation thread (like it is the case in outlook or email). Instead each
> reply arrives at the customer as a new singular email.

**Hüseyin Özyaman, Round Solutions** (`Hueseyin.Oezyaman@roundsolutions.com`, 16 Sep)
> 1. We use for sending the Chater of Odoo, but would prefer to use Outlook if
> it was easy to configure and use.
>
> 2. As said before we would prefer to use Outlook for conversation with
> customer, but it does not work like we would find it useful and configuration
> (in Outlook 365 backend) is very difficult for non-IT specialist. But every
> conversation should be also in the Chater of Odoo (traceability for all
> Users).

**Milad, Jobyfull** (`ar2ne.com@gmail.com`, 16 Sep) -- Odoo department lead developer
> Most of our clients and Jobyfull as well use email inside odoo.
>
> These are our most requested custom development for emails:
>
> 1- Adding CC and BCC option inside odoo.
>
> 2- Customers really like to see the history of all the emails sent to their
> customers while they are sending an email. I know they are displayed on the
> chat box but there are many other messages there and finding emails can be
> time consuming. Also most of the times user sends the emails using expand
> button on chat to pick a template and on that page, previous emails are not
> visible.
>
> 3- Imagine you want to write an email to customer and start writing it on
> chat box then you remember you need to send this on using another email
> template. You push the expand button but when template is changed, the
> message disappears and template data loads there instead. User should copy
> the written message, change the template, and past it in place.
>
> These are our customer feedback over the years regarding emails and I wrote a
> few module for each of these based on every customers needs. It would be
> really good if Odoo had these.
>
> Also I never use AI for my emails I like to read every email and answer them
> completely on my own.

**Mark Larson, Moka Origins** (`mark@mokaorigins.com`, 15 Sep)
> 1. Yes, we commonly send emails from inside Odoo. It would be great if the
> MCP connector could facilitate this using standard UI and the email wizard
> (currently not able to send invoice email template this way, for example)
>
> 2. It works fine manually. I have some occasional formatting issues with
> images, but there are workarounds. My bigger issue is around activity
> management. For example, I email out of the contacts app (not the marketing
> app), and you currently can't bulk edit contact activities from the contacts
> app.

**Emeric, Sceba** (`emeric@sceba.fr`, 15 Sep)
> 1. Yes, all the time.
> 2. It's not really efficient. In order to work, you have to dedicate an email
> address to odoo, it's not really efficient. It's complicated (bounce,
> catchalls...)

**Tony Greenland, Finishing Tech** (`tony@finishingtech.com`, 15 Sep)
> 1. YES
> 2. IS OK, but we have an issue with which contact emails are defaulted. We
> have spend time and dev time changing how followers are added and how the
> default 'send' buttons work.

**Stefano, Marea Outdoor** (`stefano@mareaoutdoor.com`, 15 Sep)
> Yes we do. We send emails to our prospects from inside Odoo. It works very
> well.

**Mirko, Bizolve** (`mirko@bizolve.com`, 15 Sep)
> 1.: they are send through the module or chatter
> 2.: It does work, but it does require a short explanation

**Juan José Garrido, GestionFyC** (`jjgarrido@gestionfyc.cl`, 15 Sep) -- Odoo partner
> We do send emails to customers from inside Odoo, and so do my clients. We, as
> Odoo partner, encourage the email system from within Odoo to be used, as this
> helps to keep track of what the teams are doing.
>
> At the beginning it is a bit confusing to tell the team to send emails from
> Odoo, mostly because everyone comes from Outlook or Gmail and they have their
> way to work, so the confusion, personally, mostly comes from changing the way
> to work rather than the Odoo email interface. However, I miss a better email
> interface within Odoo, especially to make it easier to CC other contacts.

**Andrea Caspani, UnitBit** (`andrea@unitbit.it`, 15 Sep)
> 1. most of all reply from chatter in the tickets. We're starting next month
> to impl
> 2. it works fine, we linked our google workspace Oauth as a outgoing server
> for the emails

**Philipp Lämmler, Scewo** (`p.laemmler@scewo.ch`, 15 Sep) -- Head of Software
> 1. Yes, as a company, we send emails to customers directly from inside Odoo.
> It is simple for us to use and ensures everyone can follow the conversation.
>
> 2. It is definitely a problem for us, and we haven't found a solution yet.
> Customers often experience issues with the emails they receive:
>   - Copying the notification email address (like notification@odoo...)
>     directly does not work for them.
>   - Customers cannot always see who else is a follower on the thread. As a
>     result, they worry other team members aren't receiving the updates, which
>     sometimes leads to them not replying at all.

**Dominik Aue, Scewo** (`d.aue@scewo.ch`, 15 Sep)
> 1. Yes, we send emails from inside Odoo
> 2. It is not perfect, sometimes creates (technical) issues. But it mostly
> works.

**Deepti Jeram** (`vasocial21@gmail.com`, 15 Sep)
> They do send emails via Odoo, we had to connect Brevo to get some sort of
> workable prototype/work around. Because we use Microsoft Eco-system, its a
> bit complicated to connect emails directly to Odoo.
>
> Since we have connected it via Brevo, everything flows smoothly.

**Koene Kisjes, IT Optimizer** (`info@it-optimizer.nl`, 15 Sep) -- Odoo Learning Partner
> 1. Mostly from the odoo chatter
> 2. Most of the time, it would put HTML syntaxes into the text, and then I
> have to reply to it and tell them that you have to make it plain text.

**3D Playmakers** (`3dplaymakers@gmail.com`, 15 Sep)
> I exclusively use odoo, to send those emails. They get stuck in spam a lot
> but that's about it.

**Cedric Devooght, Devooght** (`cedric@devoplast.com`, 15 Sep)
> We've integrated outlook with odoo and send emails inside odoo

**Andre Venter, Hatton Locks** (`andre.venter@hattonlocks.co.za`, 15 Sep)
> It's a great question. I think the answer is "It all works well but specific
> configuration is very important"
>
> I service customers and for various customers I have implemented "e-mail
> solutions" as per below:
>
> | sales@ | CRM lead or opportunity |
> | info@ | Also CRM lead or opportunity |
> | support@ | Helpdesk ticket |
> | projects@ | Project task (Also often more specific senders like specific projects with a specific audience) |
> | jobs@ | Recruitment application |
> | expenses@ | Employee expense |
> | bills@ | Draft vendor bill |
> | documents@ | Document in a designated workspace |
> | invoices@ | Sending Invoices to Customers |
>
> Also mail lists, customised solutions like electronic document delivery for
> customers (In legal firms as one example), or "special" marketing projects
> like Market Reports sent to customers by Odoo on completion of a form (As one
> potential example). All of which can be looked at as either a "send" or
> "receive function" and some limited number to a "Both send and receive"
> function.
>
> What I haven't done is configure odoo to collect mail from individuals'
> mailboxes. I have configured it to send on behalf of and set the reply
> address, and that works well for limited use sales and marketing messaging
> where it's important that when the recipient replies the mail goes directly
> to the salesperson (Or other correct person). Things start to get messy and
> overcomplicated if Names of Individuals are spoofed but e-mail addresses
> aren't.
>
> It's possible to reply directly from chatter with no more specific config,
> but then the recipient gets an e-mail from the company or the sales list
> instead of from an individual, which isn't always desirable.

### Mixed

**Aart Jan Klok, Sail Specials** (`aartjan@sailspecials.nl`, 15 Sep)
> outside Odoo indeed 99% of the time. We work with Missive for all our
> customer communication channels. But an invoice and order confirmation, yes
> we email them from Odoo. We have not real problems there as long as it is not
> a ful conversation with a customer, like customer support, Odoo is not nice
> for that.

### Sent outside Odoo

**Margriet Doolaard, Pressure Control Solutions** (`margriet@pressurecontrolsolutions.com`, 16 Sep)
> Vanuit PCS zouden we graag veel meer mailen vanuit Odoo ipv Outlook. Er zijn
> echter meerdere redenen waarom dat niet goed lukt (ligt mss aan ons):
>
>   - Slecht/lastige opmaak in Odoo
>   - Email handtekeningen zijn beter in Outlook
>   - Odoo biedt geen handige 'centrale inbox', althans wij weten niet hoe;
>     vooral voor klanten die mailconversatie starten of die antwoorden op mail
>     A over onderwerp B is dat onhandig. Mails in Odoo vind je terug bij een
>     specifieke record, niet in algemene sent/received folder.
>   - Geen cc zichtbaarheid bij ontvanger, veroorzaakt veel verwarring
>   - The automatic adding of followers creates complications
>
> Ik vergeet vast nog dingen, hopelijk maken jullie een mooie oplossing!

**Ryan Whittaker** (`ryanwhittaker314@gmail.com`, 16 Sep)
> Thank you for the great service you have produced, it has helped me a lot
> already. In terms of emails. When we set up Odoo, we tried their email
> system, but some clients claimed to not receive our emails so we reverted
> back to sending emails from from gmail and outlook.

**Mark Radford, Measurit** (`mark.radford@measurit.com`, 15 Sep)
> 1 - we use gmail, with Missive app - because we can share email/ team inboxes
> etc.
>
> 2 - integrating ODOO is a current challenge that we want to automate
>
> We generate quotes/sales orders/invoices in PDF and email to customers -with
> supporting documents & links.

**Bill Mark, Mevo-America** (`bmark@mevo-america.com`, 15 Sep)
> Mostly gmail. Trying to figure out a way to seamlessly integrate the two to
> maiximize use of CRM.

**JB Herman** (`jodybherman@gmail.com`, 15 Sep)
> If you serious about Odoo, like us who work on it everyday.
>
> I am busy adding Chatwoot alongside Odoo as Odoo does not have proper CRM
> comms the way business needs.
>
> It would make Odoo the most powerful on the market, the other winning formula
> would be to make the UX better. Would win over alot of laymen.

**Miel Bonduelle, ElementX Travel** (`miel@elementx.travel`, 15 Sep)
> we changed the email servers for newsletters

**Rodrigo Salinas, Luvre** (`rodrigo.salinas@luvrepro.com`, 15 Sep)
> We send mails from Outlook, for the moment we don't use Odoo to send mails,
> but we send WhatsApp from there.

**Lennart van Beuzekom, Poel Group** (`Lennart@poelgroup.nl`, 15 Sep)
> 1. We send it outside ODOO with an intergration on Outlook so with our
> outlook emailadress to the customers.
> 2. That works well right now.

### Not applicable

**Konstantin Satushev, Sana Swiss Services** (`konstantin@sana.swiss`, 15 Sep)
> We are very much interested in your Outlook Pro app. However as we are
> currently still on Odoo 17 and are just starting to analyze the migration to
> Odoo 19 it will take us a while unfortunately until we can start using your
> solution.
