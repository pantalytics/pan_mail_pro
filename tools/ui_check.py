#!/usr/bin/env python3
"""What the test suite cannot see: whether the pages actually read.

Run against the instance `tools/ui_preview.sh` boots, seeded as that script
seeds it. Every assertion here is a bug this module has actually shipped:

  * the setup checklist ran the full width of the window, so on a wide screen
    the arrow sat a screen away from the step it belongs to
  * the provider line read `outlook` where its own form says "Microsoft 365"
  * a mailbox that had stopped drew two status dots at once
  * every menu the module declares opens; a broken view is a traceback, not
    a red test
  * a brand new provider opened carrying IMAP's explanation, because "has no
    OAuth" and "nothing chosen yet" were one condition
  * the connect banner is drawn by patching Odoo's own webclient template, so
    a wrong xpath breaks every screen and nothing server-side can see it
  * the same banner was registered in `WebClient.components`, which Enterprise
    has already copied by then -- every Enterprise screen went white

    tools/ui_check.py                     # assert, and write screenshots
    tools/ui_check.py --out=ui-screenshots

Exit code 1 with the failures listed is the whole interface; CI reads that.
"""
import argparse
import ast
import datetime
import os
import sys
import xmlrpc.client

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit('playwright is not installed — pip install playwright')

CHROME = os.environ.get('PAN_UI_CHROME', '/opt/pw-browsers/chromium')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The width a real desk monitor has. The layout bug that started all this was
# invisible at 1440 and obvious here.
WIDE = 2000
# A step is a name, an answer and the way to change it. Wider than this and it
# stops being one line the eye can cross.
MAX_STEP_WIDTH = 800

# Selection *values* — none of these may ever be what a user reads.
PROVIDER_CODES = ('outlook', 'gmail', 'imap')


def manifest_version():
    manifest = ast.literal_eval(open(f'{REPO}/__manifest__.py').read())
    return manifest['version']


class Checks:
    def __init__(self, page, out, browser=None):
        self.page = page
        self.out = out
        self.browser = browser
        self.failures = []
        # Uncaught JS, which is how every failure of a mounted Odoo view
        # arrives: nothing on the Python side sees it and the server log is
        # empty. Printed with the failures, so the next person reads the
        # error instead of guessing from a Playwright timeout.
        self.js_errors = []

    def fail(self, message):
        # Printed as it happens, not only in the summary: a Playwright click
        # that times out takes the process down with it, and the failures
        # collected before that are the ones that say why.
        print(f'  - {message}', flush=True)
        self.failures.append(message)

    def dialog_in_the_way(self):
        """The text of an Odoo error dialog over the screen, and close it.

        A view that fails to mount leaves the pane looking fine with a modal
        on top of it, and the next click times out thirty seconds later
        somewhere unrelated. Read it where it happened, then get it out of
        the way so the checks after this one still run.
        """
        modal = self.page.query_selector('.modal.o_technical_modal, .o_dialog_container .modal')
        if not modal or not modal.is_visible():
            return ''
        # Odoo's error dialog says "Oops!" and keeps the stack behind a link.
        # The stack is the whole message: without it this reads as "something
        # went wrong somewhere in the web client".
        details = modal.query_selector('a:has-text("technical details"), '
                                       'button:has-text("technical details")')
        if details:
            details.click()
            self.page.wait_for_timeout(300)
        text = ' '.join(modal.inner_text().split())[:1200]
        for selector in ('button:has-text("Close")', 'button:has-text("Ok")', '.btn-close'):
            button = modal.query_selector(selector)
            if button:
                button.click()
                break
        self.page.wait_for_timeout(400)
        return text

    def shot(self, name):
        if self.out:
            self.page.screenshot(path=os.path.join(self.out, name), full_page=True)

    # -- Settings → Mail Pro --------------------------------------------------

    def settings(self):
        page = self.page
        page.goto(f'{self.base}/odoo/settings', wait_until='domcontentloaded')
        page.wait_for_selector('a.tab[data-key=pan_mail_pro]', timeout=60000)
        page.click('a.tab[data-key=pan_mail_pro]')
        page.wait_for_selector('.o_mailpro_step', timeout=30000)
        page.wait_for_timeout(800)
        self.shot('settings-mail-pro.png')

        steps = page.query_selector_all('.o_mailpro_step')
        if len(steps) != 3:
            self.fail(f'the checklist has {len(steps)} steps, expected 3')

        for index, step in enumerate(steps, start=1):
            width = step.bounding_box()['width']
            if width > MAX_STEP_WIDTH:
                self.fail(
                    f'step {index} is {width:.0f}px wide at a {WIDE}px window '
                    f'(max {MAX_STEP_WIDTH}) — the arrow ends up a screen away '
                    f'from its step')

            dots = [d for d in step.query_selector_all('.o_mailpro_dot') if d.is_visible()]
            if len(dots) != 1:
                self.fail(f'step {index} shows {len(dots)} status dots, expected 1')

        block = page.query_selector('div.app_settings_block[data-key=pan_mail_pro]')
        text = block.inner_text()
        for code in PROVIDER_CODES:
            # A code on its own line, not a substring of a real address.
            if any(line.strip() == code for line in text.splitlines()):
                self.fail(f'the page shows the selection code "{code}" instead of its label')

        version = manifest_version()
        if version not in text:
            self.fail(f'About does not show the version {version}')
        if 'Pantalytics B.V.' not in text:
            self.fail('About does not carry the copyright line')

        # Pantalytics Account, connected: one way out and nothing of the
        # not-connected or pending states leaking onto the screen.
        for leaked in ('Connect to Pantalytics', 'Check Approval'):
            if any(b.is_visible() and b.inner_text().strip() == leaked
                   for b in block.query_selector_all('button')):
                self.fail(f'a connected database shows "{leaked}"')
        if not any(b.is_visible() and b.inner_text().strip() == 'Disconnect'
                   for b in block.query_selector_all('button')):
            self.fail('a connected database offers no way to disconnect')

    def settings_not_connected(self):
        """Without a Pantalytics account the page is one button and nothing else.

        Every step below it configures a product that will not run, and a
        checklist you cannot finish reads as the thing that is broken. The
        instance is seeded connected, so this disconnects it, looks, and
        connects it back for the checks that come after.
        """
        page = self.page
        link = self.call('pan.mail.license', 'search', [])
        self.call('pan.mail.license', 'unlink', link)
        try:
            page.goto(f'{self.base}/odoo/settings', wait_until='domcontentloaded')
            page.wait_for_selector('a.tab[data-key=pan_mail_pro]', timeout=60000)
            page.click('a.tab[data-key=pan_mail_pro]')
            page.wait_for_timeout(1200)
            self.shot('settings-not-connected.png')

            block = page.query_selector('div.app_settings_block[data-key=pan_mail_pro]')
            connect = [b for b in block.query_selector_all('button')
                       if b.is_visible() and b.inner_text().strip() == 'Connect to Pantalytics']
            if len(connect) != 1:
                self.fail(f'an unlinked database shows {len(connect)} Connect '
                          f'buttons, expected 1')
            steps = [s for s in page.query_selector_all('.o_mailpro_step') if s.is_visible()]
            if steps:
                self.fail(f'an unlinked database shows {len(steps)} setup steps, '
                          f'expected none')
            text = block.inner_text()
            # About (the version and the licence line) stays: a support mail
            # and the documentation link are wanted before connecting too.
            for leaked in ('1. Email Provider', '2. Internal Domains', 'Connect Mailbox'):
                if leaked in text:
                    self.fail(f'an unlinked database still shows "{leaked}"')
            for wanted in ('Elastic License', manifest_version(), 'Documentation'):
                if wanted not in text:
                    self.fail(f'an unlinked database no longer shows "{wanted}"')
            self.error_free('Settings without a Pantalytics account')
        finally:
            self.call('pan.mail.license', 'create', {
                'status': 'active',
                'valid_until': (datetime.datetime.now(datetime.UTC)
                                + datetime.timedelta(days=14)
                                ).strftime('%Y-%m-%d %H:%M:%S')})

    # -- Every menu this module adds -----------------------------------------

    def menus(self):
        """Open every menu the module declares and prove it renders.

        The menus are read from the server rather than clicked out of the
        navbar: they moved from an app of their own to Settings → Technical in
        19.0.7.0.0, and a check that walks the navbar only ever tests where
        they happen to live today. Half of them are behind developer mode,
        which a browser walk would have to switch on first; their action URL
        opens regardless.
        """
        for name, action_id in module_menu_actions(self.call):
            self.page.goto(f'{self.base}/odoo/action-{action_id}',
                           wait_until='domcontentloaded')
            self.page.wait_for_timeout(2000)
            self.error_free(name)
            self.shot(f'view-{slug(name)}.png')

    # -- The Inbox ------------------------------------------------------------

    # The mailbox list is the shape the mail client next to this one has: a mailbox
    # and its folders. Our own states are not folders and do not go here --
    # a mailbox list of invented names reads as a filter panel wearing a mailbox list's
    # clothes, which is what people notice first and trust least.
    FOLDERS = ('Inbox', 'Sent')

    # Those states, in the filter menu at the top right of the list they
    # filter -- where the mail client next to this one puts its own.
    FILTERS = ('Unread', 'On a contact only', 'Linked to nothing')

    def conversation_view(self):
        """The Inbox renders four panes with real mail in them.

        A screen that renders is not a screen that reads, which is why this
        asserts the panes are actually filled: the seed puts three messages on
        one lead, so an empty list here means the read layer returned nothing
        and the screen would ship as a convincing empty shell.
        """
        action = dict(module_menu_actions(self.call)).get('Inbox')
        if not action:
            self.fail('there is no Inbox menu')
            return

        page = self.page
        page.goto(f'{self.base}/odoo/action-{action}', wait_until='domcontentloaded')
        try:
            page.wait_for_selector('.o_mailpro_inbox', timeout=30000)
        except Exception:
            self.fail('the Inbox did not render at all')
            return
        page.wait_for_timeout(2500)
        self.error_free('Inbox')

        folders = [el.inner_text().split('\n')[0].strip()
                for el in page.query_selector_all('.o_mailpro_folder')]
        if folders != list(self.FOLDERS):
            self.fail(f'the folder list reads {folders}, expected {list(self.FOLDERS)}')

        # The bar reads the way Outlook's does: New Email on the left, the
        # search in the middle, and the filter at the top right of the list.
        # Their order on screen is the assertion -- three controls in the
        # right places is the whole point of the layout.
        if not page.query_selector('.o_mailpro_new'):
            self.fail('the Inbox has no New Email button')
        if not page.query_selector('.o_mailpro_topbar #o_mailpro_search'):
            self.fail('the search is not in the top bar')
        if not page.query_selector('.o_mailpro_conversation_list_head .o_mailpro_filter_toggle'):
            self.fail('the filter is not at the top of the conversation list')

        # The filter menu opens once, over the list, not once per mailbox.
        page.click('.o_mailpro_filter_toggle')
        page.wait_for_timeout(800)
        pills = [el.inner_text().strip()
                 for el in page.query_selector_all(
                     '.o_mailpro_filter_menu .o_mailpro_filter_item '
                     '.o_mailpro_filter_label')]
        if pills != list(self.FILTERS):
            self.fail(f'the filter menu reads {pills}, expected {list(self.FILTERS)}')
        else:
            # An item narrows the list and a second click gives it back, which
            # is the whole promise of a filter over a folder. The menu stays
            # open while you do it, the way Odoo's own filter menu does.
            items = page.query_selector_all(
                '.o_mailpro_filter_menu .o_mailpro_filter_item')
            items[0].click()
            page.wait_for_timeout(1500)
            if not page.query_selector('.o_mailpro_filter_menu .selected'):
                self.fail('clicking a filter did not mark it as the one in use')
            page.query_selector_all(
                '.o_mailpro_filter_menu .o_mailpro_filter_item')[0].click()
            page.wait_for_timeout(1500)
            if page.query_selector('.o_mailpro_filter_menu .selected'):
                self.fail('clicking the filter again did not clear it')
            self.error_free('Inbox filter menu')
        self.shot('inbox-filter-menu.png')
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)

        # Typing is the search: no Enter, no button, the list follows.
        page.fill('#o_mailpro_search', 'zzzznothingmatchesthis')
        page.wait_for_timeout(2500)
        if page.query_selector_all('.o_mailpro_item'):
            self.fail('typing in the search did not narrow the conversation list')
        page.fill('#o_mailpro_search', '')
        page.wait_for_timeout(2500)
        if not page.query_selector_all('.o_mailpro_item'):
            self.fail('clearing the search did not give the conversations back')
        self.error_free('Inbox search')

        # New Email asks which record to write on before it opens anything:
        # a mail this module sends with nothing behind it is the state the
        # filter menu one line up exists to find. It is the same two-step
        # dialog linking uses, which is the point -- one thing to learn.
        page.click('.o_mailpro_new')
        try:
            page.wait_for_selector('.o_mailpro_link_dialog', timeout=15000)
        except Exception:
            self.fail('New Email opened no record picker')
            return
        rows = page.query_selector_all('.o_mailpro_link_dialog .o_mailpro_link_row')
        if not rows:
            self.fail('New Email offers nothing to write the mail on')
        else:
            self.shot('inbox-new-email.png')
            rows[0].click()          # step one: the kind of record
            page.wait_for_timeout(1500)
            records = page.query_selector_all(
                '.o_mailpro_link_dialog .o_mailpro_link_row')
            if not records:
                self.fail('New Email step two offers no records')
                return
            records[0].click()       # step two: the record itself
            # The composer opens in the conversation pane, the same one a
            # reply uses. A regression here is New Email having gone back to
            # being a popup over the Inbox.
            try:
                page.wait_for_selector('.o_mailpro_composer .o_form_view',
                                       timeout=15000)
            except Exception:
                self.fail('picking a record did not open the composer in the pane')
                return
            if page.query_selector('.modal .o_mail_composer_form'):
                self.fail('New Email opened the composer in a dialog')
            # Step two picked a contact, and a contact is its own recipient:
            # the tag has to be in "To" before anyone types, the same
            # assertion Reply makes further down.
            if not page.query_selector('.o_mailpro_composer [name="partner_ids"] .o_tag'):
                self.fail('New Email opened a composer with nobody in To')
            head = page.query_selector('.o_mailpro_conversation_head .o_mailpro_conversation_title')
            if not head or head.inner_text().strip() != 'New email':
                self.fail('the pane head does not say a new email is being written')
            if not page.query_selector(
                    '.o_mailpro_conversation_head button:has-text("Send")'):
                self.fail('the New Email composer has no Send button')
            self.shot('inbox-new-email-composer.png')
            # Discard rather than Escape: Escape leaves the draft open, and
            # an open composer takes every check after this one down with it.
            discard = page.query_selector(
                '.o_mailpro_conversation_head button:has-text("Discard")')
            if not discard:
                self.fail('an open New Email cannot be discarded')
            else:
                discard.click()
                page.wait_for_timeout(1000)
            if page.query_selector('.o_mailpro_composer'):
                self.fail('the New Email composer stayed open after Discard')
                return
        self.error_free('Inbox New Email')

        # The mailbox sits in the mailbox list above its own folders, the way it does
        # in the mail client next to this one. The seed makes two, so this is
        # also the only place that proves switching mailbox works at all.
        def mailbox_names():
            return [el.inner_text().strip()
                    for el in page.query_selector_all('.o_mailpro_mailbox')]

        def open_mailbox(index):
            page.query_selector_all('.o_mailpro_mailbox')[index].click()
            page.wait_for_timeout(1500)
            open_now = page.query_selector_all('.o_mailpro_mailbox_active')
            return [el.inner_text().strip() for el in open_now]

        names = mailbox_names()
        if len(names) < 2:
            self.fail(f'{len(names)} mailboxes in the mailbox list, expected the seeded 2')
        else:
            active = [el.inner_text().strip()
                      for el in page.query_selector_all('.o_mailpro_mailbox_active')]
            if active != names[:1]:
                self.fail(f'the mailbox list opens on {active}, expected {names[:1]}')
            if open_mailbox(1) != names[1:2]:
                self.fail('clicking a mailbox did not open it')
            # Back to the one the seeded mail is in, so everything below reads
            # the filled screen.
            open_mailbox(0)

        # A mailbox folds its folders away and unfolds them again, the way an
        # account does in Outlook. Asserted on the folder rows, not on the
        # caret: the caret pointing the right way proves nothing about what
        # is on screen.
        def folder_rows():
            return len(page.query_selector_all('.o_mailpro_folder'))

        def fold(index):
            page.query_selector_all('.o_mailpro_mailbox_toggle')[index].click()
            page.wait_for_timeout(1200)
            return folder_rows()

        if len(names) >= 2:
            before = folder_rows()
            if fold(0) != before - len(self.FOLDERS):
                self.fail('folding a mailbox left its folders on screen')
            if fold(0) != before:
                self.fail('unfolding a mailbox did not bring its folders back')

        # Everything clickable is a real button, so a keyboard can reach it.
        for selector, what in (('.o_mailpro_mailbox', 'mailbox'),
                               ('.o_mailpro_mailbox_toggle', 'mailbox caret'),
                               ('.o_mailpro_folder', 'folder'),
                               ('.o_mailpro_filter', 'filter'),
                               ('.o_mailpro_item', 'conversation')):
            divs = [el for el in page.query_selector_all(selector)
                    if el.evaluate('el => el.tagName') != 'BUTTON']
            if divs:
                self.fail(f'{len(divs)} {what} rows are not buttons')

        items = page.query_selector_all('.o_mailpro_item')
        if not items:
            self.fail('the conversation list is empty with seeded mail on a lead')
        else:
            # Every line carries a face: the contact's photo, Odoo's letter
            # circle when the partner has none, initials when the sender is
            # nobody in the database. A line without one reads as broken.
            faceless = [el for el in items
                        if not el.query_selector('.o_mailpro_avatar')]
            if faceless:
                self.fail(f'{len(faceless)} conversation rows have no avatar')
            selected = page.query_selector_all('.o_mailpro_item_active')
            if len(selected) != 1:
                self.fail(f'{len(selected)} conversations look selected, expected 1')

        messages = page.query_selector_all('.o_mailpro_message')
        if not messages:
            self.fail('the conversation pane shows no messages')
        elif len(messages) > 1:
            # A conversation is a stack, newest at the top: the message you came
            # for is open and the history under it is one line each. Nine
            # open bodies is a page you have to scroll, and the newest
            # message is the part anybody reads first.
            opened = page.query_selector_all('.o_mailpro_message_open')
            if len(opened) != 1:
                self.fail(f'{len(opened)} messages are open, expected 1')
            elif not messages[0].evaluate(
                    'el => el.classList.contains("o_mailpro_message_open")'):
                self.fail('the open message is not the top one')
            closed = page.query_selector(
                '.o_mailpro_message:not(.o_mailpro_message_open)'
                ' .o_mailpro_message_head')
            if not closed:
                self.fail('a collapsed message has no header to click open')
            else:
                closed.click()
                page.wait_for_timeout(400)
                if len(page.query_selector_all('.o_mailpro_message_open')) != 2:
                    self.fail('clicking a collapsed message did not open it')
                else:
                    # Back to the shape the screenshot below is meant to show.
                    closed.click()
                    page.wait_for_timeout(400)

        # Who was on the open mail, the way Outlook shows it: To and Cc on
        # one line under the sender, and the whole block -- From with its
        # address, To, Cc, Date -- behind a click on that line. Folded by
        # default; a header four lines tall on every open mail is the thing
        # the fold exists to avoid.
        meta = page.query_selector('.o_mailpro_message_open .o_mailpro_meta_toggle')
        if meta is None:
            self.fail('the open message has no To/Cc line')
        elif page.query_selector('.o_mailpro_message_open .o_mailpro_meta_full'):
            self.fail('the header details are open before anybody asked')
        else:
            meta.click()
            page.wait_for_timeout(300)
            full = page.query_selector('.o_mailpro_message_open .o_mailpro_meta_full')
            if full is None:
                self.fail('clicking the To/Cc line did not open the header')
            else:
                # text_content, not inner_text: the labels are set in
                # capitals by CSS, and inner_text returns what is drawn.
                labels = [dt.text_content().strip() for dt in full.query_selector_all('dt')]
                for word in ('From', 'To', 'Date'):
                    if word not in labels:
                        self.fail(f'the open header has no {word} line')
                self.shot('inbox-message-details.png')
                meta.click()
                page.wait_for_timeout(300)

        # The fourth pane is the product. If the form view cannot mount, the
        # pane falls back and this is the only place that would notice.
        if page.query_selector('.o_mailpro_odoo_record .o_form_view') is None:
            self.fail('the record pane did not mount the record form')

        # ...and the form's own statusbar buttons stay out of it. A filled
        # "Convert to Opportunity" in the fourth pane is a louder button than
        # Reply, on a screen whose one job is replying.
        loud = [b for b in page.query_selector_all(
            '.o_mailpro_odoo_record .o_form_statusbar button') if b.is_visible()]
        if loud:
            self.fail('the record pane shows %d form buttons beside Reply'
                      % len(loud))

        # The chatter goes with them. Two composers a divider apart is the
        # thing the tab strip replaced, and the one that loses is the one
        # that cannot thread a reply. Visibility rather than presence: it is
        # hidden with CSS, so it still mounts -- what must not happen is that
        # somebody sees it or tabs into it.
        chatter = [el for el in page.query_selector_all(
            '.o_mailpro_odoo_record .o-mail-Form-chatter, '
            '.o_mailpro_odoo_record .o-mail-Chatter') if el.is_visible()]
        if chatter:
            self.fail('the record pane still shows a chatter')

        # The four readings of a conversation, in one strip over pane 3.
        # The label is the first span; the second is the count, and reading
        # the button's text gives "Activities2" the moment there is one.
        tabs = [el.query_selector('span').inner_text().strip()
                for el in page.query_selector_all('.o_mailpro_tab')]
        if tabs != ['Mail', 'Mail + notes', 'Files', 'Activities']:
            self.fail(f'the tab strip reads {tabs}')
        else:
            for name in ('Mail + notes', 'Files', 'Activities', 'Mail'):
                tab = page.query_selector(f'.o_mailpro_tab:has(span:text-is("{name}"))')
                tab.click()
                page.wait_for_timeout(900)
                self.error_free(f'the {name} tab')
                if name == 'Files':
                    self.check_files_tab(page)

            # The Activities tab draws Odoo's own activity card, so a follow-up
            # reads here exactly as it does in the chatter: the type's icon, the
            # deadline's colour and Mark Done. A restyled row of our own would
            # pass every other assertion on this screen.
            page.query_selector('.o_mailpro_tab:has-text("Activities")').click()
            page.wait_for_selector('.o_mailpro_activities', timeout=15000)
            page.wait_for_timeout(900)
            cards = page.query_selector_all('.o_mailpro_activities .o-mail-Activity')
            if len(cards) != 2:
                self.fail(f'the Activities tab drew {len(cards)} activity cards, expected 2')
            elif not page.query_selector(
                    '.o_mailpro_activities .o-mail-Activity-iconContainer.text-bg-danger'):
                self.fail('no overdue activity, so the state colours are not Odoo\'s')
            elif not page.query_selector('.o_mailpro_activities .o-mail-Activity-markDone'):
                self.fail('the activity card carries no Mark Done button')
            self.shot('inbox-activities.png')
            page.query_selector('.o_mailpro_tab:has(span:text-is("Mail"))').click()
            page.wait_for_timeout(600)
            self.check_followers(page)

        # One writing action per tab, and each on the tab that shows what it
        # writes. Both everywhere is how a note written in Mail vanishes on
        # save, and how a reply gets sent from a screen showing no mail.
        if page.query_selector('.o_mailpro_conversation_head button:has-text("Log note")'):
            self.fail('the Mail tab offers Log note')

        # The screen's one primary action. A reader-only inbox is half a
        # product, and this is the click that proves it is not one.
        reply = page.query_selector('.o_mailpro_conversation_head button.btn-primary')
        if not reply:
            self.fail('the conversation has no Reply button')
        else:
            reply.click()
            try:
                # In the pane, not on top of it: a dialog over the Inbox hides
                # the list, the record and the mail being answered. A `.modal`
                # here is the composer having gone back to being a popup.
                page.wait_for_selector('.o_mailpro_composer .o_form_view', timeout=15000)
            except Exception:
                problem = self.dialog_in_the_way()
                self.fail('Reply opened no composer in the conversation pane'
                          + (f': {problem}' if problem else ''))
            else:
                page.wait_for_timeout(600)
                if page.query_selector('.modal .o_form_view'):
                    self.fail('Reply opened the composer in a dialog')
                # The chatter fills "To" from the record; the composer on its
                # own fills nothing, and a reply to nobody is the one bug a
                # green suite cannot see. The seeded conversation has a customer,
                # so their tag has to be there before anyone types.
                if not page.query_selector('.o_mailpro_composer [name="partner_ids"] .o_tag'):
                    self.fail('Reply opened a composer with nobody in To')
                # And it answers the mail, not the record: the subject is the
                # conversation's, so the customer's client files it where they read
                # the question. The record's name here means the reply left
                # as a new conversation.
                subject = page.query_selector('.o_mailpro_composer [name="subject"] input')
                value = subject.input_value() if subject else ''
                # "offerte revisie" is in the mail's subject and not in the
                # lead's name, so the record-name fallback cannot pass this.
                if 'offerte revisie' not in value.lower():
                    self.fail(f'Reply subject is "{value}", not the conversation subject')
                # The arch's footer is cut out of every form that is not in a
                # dialog, so without the inline view there is no way to attach
                # a file to a reply -- and nothing errors, the paperclip is
                # simply not there.
                if not page.query_selector(
                        '.o_mailpro_composer .o_mailpro_composer_tools'):
                    self.fail('the reply has no attachment or template row')
                # The record stays readable beside the reply. That is the
                # whole reason this is a pane and not a dialog.
                if not page.query_selector('.o_mailpro_odoo_record .o_form_view'):
                    self.fail('the record pane went away while replying')
                self.shot('inbox-reply.png')
                # A dialog on top of the reply is this screen's failure mode:
                # the pane renders, something throws behind it, and the next
                # click times out thirty seconds later somewhere unrelated.
                over_the_reply = self.dialog_in_the_way()
                if over_the_reply:
                    self.fail(f'a dialog opened over the reply: {over_the_reply}')
                # Discard rather than Escape: Escape leaves the draft open,
                # and the screenshot below is what a reviewer looks at.
                discard = page.query_selector(
                    '.o_mailpro_conversation_head button:has-text("Discard")')
                if not discard:
                    self.fail('an open reply cannot be discarded')
                else:
                    discard.click()
                    page.wait_for_selector('.o_mailpro_messages', timeout=15000)
                    page.wait_for_timeout(600)
            left_open = self.dialog_in_the_way()
            if left_open:
                self.fail(f'a dialog was left over the Inbox: {left_open}')

        # Log note is the other half of "this screen writes in one pane". It
        # is the same composer with the note subtype, so the thing to prove is
        # that it lands in the pane too and says what it will do: a button
        # reading Send over a note is how somebody mails a customer their own
        # internal margin. It lives in Mail + notes, which is the tab that shows
        # a note once it is written.
        page.click('.o_mailpro_tab:has-text("Mail + notes")')
        page.wait_for_timeout(900)
        if page.query_selector('.o_mailpro_conversation_head button:has-text("Reply")'):
            self.fail('the Mail + notes tab offers Reply')
        note = page.query_selector('.o_mailpro_conversation_head button:has-text("Log note")')
        if not note:
            self.fail('there is no way to log a note')
        else:
            note.click()
            try:
                page.wait_for_selector('.o_mailpro_composer .o_form_view', timeout=15000)
            except Exception:
                self.fail('Log note opened no composer in the conversation pane')
            else:
                page.wait_for_timeout(600)
                over_the_note = self.dialog_in_the_way()
                if over_the_note:
                    self.fail(f'a dialog opened over the note: {over_the_note}')
                if not page.query_selector(
                        '.o_mailpro_conversation_head button:has-text("Log")'):
                    self.fail('the note is sent by a button that says Send')
                discard = page.query_selector(
                    '.o_mailpro_conversation_head button:has-text("Discard")')
                if discard:
                    discard.click()
                    page.wait_for_selector('.o_mailpro_messages', timeout=15000)
                    page.wait_for_timeout(600)

        self.panes()
        self.zoom()

        self.shot('inbox.png')

        # The chip opens the record on top of the Inbox. The breadcrumb then
        # names the screen it came from, and a client action is only named
        # by its component: the action record's name is not read.
        chip = page.query_selector('.o_mailpro_chips .o_mailpro_chip_button')
        if chip:
            # The app's tile, and a loaded one: a broken image is what a
            # wrong module name looks like, and nothing else reports it.
            icon = chip.query_selector('.o_mailpro_chip_icon')
            if not icon:
                self.fail('the Linked-to chip has no app icon')
            elif not icon.evaluate('img => img.complete && img.naturalWidth > 0'):
                self.fail('the Linked-to chip icon did not load')
            chip.click()
            try:
                page.wait_for_selector('.o_form_view .o_breadcrumb', timeout=15000)
                page.wait_for_timeout(400)
                crumbs = page.inner_text('.o_breadcrumb')
                if 'Inbox' not in crumbs:
                    self.fail(f'the breadcrumb above the record reads "{crumbs}", not Inbox')
                page.go_back()
                page.wait_for_selector('.o_mailpro_inbox', timeout=15000)
                page.wait_for_timeout(600)
            except Exception as exc:
                self.fail(f'the Filed-on chip did not open the record: {exc}')

        # A wide monitor is where the complaint arrives from, and a narrow one
        # is where the fourth pane is meant to step aside rather than squeeze.
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.wait_for_timeout(600)
        record = page.query_selector('.o_mailpro_odoo_record')
        if record and record.is_visible():
            self.fail('the record pane still takes space at 1280px')
        # The pane stepped aside; the record did not. Its button is in the
        # top bar with the other two, where it is on screen whatever is
        # folded: nothing floats over the conversation's own header, which
        # already carries a title, a chip row and a tab strip.
        if page.query_selector('.o_mailpro_odoo_record_button'):
            self.fail('at 1280px the conversation head still carries a Record button')
        if page.query_selector('.o_mailpro_split_toggle'):
            self.fail('a fold button still floats on a divider')
        toggle = page.query_selector('.o_mailpro_pane_toggle_odoo_record')
        if not toggle or not toggle.is_visible():
            self.fail('at 1280px there is no button to open the record from')
        else:
            box = toggle.bounding_box()
            if box['width'] < 32 or abs(box['width'] - box['height']) > 2:
                self.fail('the record toggle is %dx%dpx, not a finger-sized square'
                          % (box['width'], box['height']))
            if 'odoo record' not in (toggle.get_attribute('aria-label') or '').lower():
                self.fail('the record button does not say what it opens: %r'
                          % toggle.get_attribute('aria-label'))
            if toggle.get_attribute('aria-pressed') != 'false':
                self.fail('the record is folded but its button reads pressed')
            toggle.click()
            page.wait_for_timeout(600)
            record = page.query_selector('.o_mailpro_odoo_record')
            if not record or not record.is_visible():
                self.fail('tapping the button did not open the record at 1280px')
            elif record.bounding_box()['width'] < 500:
                self.fail('the record opened %dpx wide at 1280px, not the column'
                          % record.bounding_box()['width'])
            if self.visible('.o_mailpro_conversation'):
                self.fail('the conversation stayed open next to the record at 1280px')
            self.shot('inbox-narrow-record.png')
            toggle = page.query_selector('.o_mailpro_pane_toggle_odoo_record')
            if toggle.get_attribute('aria-pressed') != 'true':
                self.fail('the record is open but its button does not read pressed')
            toggle.click()
            page.wait_for_timeout(600)
            if not self.visible('.o_mailpro_conversation'):
                self.fail('tapping the button again did not bring the conversation back')
            if self.visible('.o_mailpro_odoo_record'):
                self.fail('the record stayed open next to the conversation at 1280px')
        # The mailbox list folds the way every pane folds: its own button in the top
        # bar, the menu icon whether it is open or shut, pressed while it is
        # open. There is no second control for it anywhere else.
        fold = page.query_selector('.o_mailpro_pane_toggle_mailbox_list')
        if not fold or not fold.is_visible():
            self.fail('at 1280px the top bar has no button for the mailbox list')
        else:
            if not fold.query_selector('.fa-bars'):
                self.fail('the mailbox list button is not the menu icon')
            if fold.get_attribute('aria-pressed') != 'true':
                self.fail('the mailbox list is open but its button does not read pressed')
            fold.click()
            page.wait_for_timeout(400)
            if self.visible('.o_mailpro_mailbox_list'):
                self.fail('the button did not fold the mailbox list at 1280px')
            fold = page.query_selector('.o_mailpro_pane_toggle_mailbox_list')
            if not fold or not fold.is_visible():
                self.fail('folding the mailbox list took its own button off the screen')
            elif fold.get_attribute('aria-pressed') != 'false':
                self.fail('the mailbox list is folded but its button still reads pressed')
            else:
                self.shot('inbox-narrow-mailboxes-folded.png')
                fold.click()
                page.wait_for_timeout(400)
                if not self.visible('.o_mailpro_mailbox_list'):
                    self.fail('the button did not bring the mailbox list back at 1280px')
        self.shot('inbox-narrow.png')

        self.phone()

        page.set_viewport_size({'width': WIDE, 'height': 1100})
        page.wait_for_timeout(600)
        for selector, name in (('.o_mailpro_mailbox_list', 'the mailbox list'),
                               ('.o_mailpro_conversation_list', 'the conversation list'),
                               ('.o_mailpro_conversation', 'the conversation')):
            if not self.visible(selector):
                self.fail(f'{name} did not come back at {WIDE}px')

    def linking(self):
        """The screen where a match is corrected, and the correction sticking.

        Two things here are worth a browser and nothing else can prove them.
        That the suggestion is offered as one record and not a candidate list,
        and that clicking it actually moves the conversation: a `link_to`
        that works over RPC and leaves the screen showing the old state is a
        bug a green Python suite cannot see.

        "On a contact only" is the filter this works from, not "Linked to
        nothing". A mail the ladder could not place still lands somewhere, and
        the contact is where -- delivered, to a place nobody is looking, which
        is the whole reason the filter exists.
        """
        page = self.page
        page.click('.o_mailpro_filter_toggle')
        page.wait_for_timeout(800)
        pill = page.query_selector(
            '.o_mailpro_filter_item:has(.o_mailpro_filter_label:text-is('
            '"On a contact only"))')
        if not pill:
            self.fail('there is no "On a contact only" filter to link from')
            return
        pill.click()
        page.wait_for_timeout(1500)
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)

        items = page.query_selector_all('.o_mailpro_item')
        if len(items) < 2:
            self.fail(f'{len(items)} contact-only conversations, expected the seeded 2')
            return
        items[0].click()
        page.wait_for_timeout(1500)

        # One suggestion. A list here would be the triage queue coming back in
        # another shape, asking the reader to do the matching we could not.
        suggestions = page.query_selector_all('.o_mailpro_suggestion')
        if len(suggestions) != 1:
            self.fail(f'{len(suggestions)} suggestions on screen, expected exactly 1')
            return
        text = suggestions[0].inner_text()
        if 'Asafdichtingen' not in text:
            self.fail(f'the suggestion reads "{text}" and does not name the record')
        # The confidence is a number for the routing log, not for this screen.
        if '%' in text or '0.6' in text:
            self.fail(f'the suggestion shows a score: "{text}"')

        # The picker: two steps, each a search box over a list, and nothing
        # on screen until somebody asks for it. Worth a browser because both
        # steps are an RPC per keystroke and a Python test sees neither the
        # dialog nor the step it leaves behind.
        if page.query_selector('.o_mailpro_link_dialog'):
            self.fail('the link picker is open on a screen nobody asked')
        opener = page.query_selector('.o_mailpro_relink_toggle')
        if not opener:
            self.fail('a linked conversation offers no way to change where it went')
            return
        opener.click()
        page.wait_for_timeout(800)
        dialog = page.query_selector('.o_mailpro_link_dialog')
        if not dialog:
            self.fail('the link picker did not open')
            return
        if not dialog.query_selector('.o_mailpro_link_search'):
            self.fail('step one of the picker has no search box')
        models = dialog.query_selector_all('.o_mailpro_link_row')
        if not models:
            self.fail('step one of the picker opened with nothing in it')
            return
        self.shot('inbox-link-models.png')

        # Step two: the records of the model just picked, seeded from the
        # correspondent. Seeded, so the list must not be empty before anybody
        # has typed -- an empty second step is the bug this replaced.
        models[0].click()
        page.wait_for_timeout(1200)
        if not dialog.query_selector('.o_mailpro_link_search'):
            self.fail('step two of the picker has no search box')
        if not dialog.query_selector('.o_mailpro_link_back'):
            self.fail('step two of the picker cannot go back to the models')
        if not dialog.query_selector_all('.o_mailpro_link_row'):
            self.fail('step two opened empty instead of on the correspondent')
        self.shot('inbox-link-records.png')

        # Typing searches rather than filtering what is drawn: a term that
        # matches nothing has to reach the server and come back empty.
        page.fill('.o_mailpro_link_search', 'zzzzgeenmatch')
        page.wait_for_timeout(1500)
        if dialog.query_selector_all('.o_mailpro_link_row'):
            self.fail('the record search answers rows for a term nothing matches')
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)
        if page.query_selector('.o_mailpro_link_dialog'):
            self.fail('the link picker does not close')
        self.error_free('opening the link picker')

        self.shot('inbox-unlinked.png')

        accept = page.query_selector('.o_mailpro_suggestion button')
        if not accept:
            self.fail('the suggestion has no button to accept it')
            return
        accept.click()
        page.wait_for_timeout(2500)
        self.error_free('linking a conversation')

        # It left the folder it was in, which is the only proof from here that
        # the move reached the database rather than the screen.
        remaining = page.query_selector_all('.o_mailpro_item')
        if len(remaining) != 1:
            self.fail(f'{len(remaining)} conversations left on a contact after '
                      f'linking one, expected 1')

    def visible(self, selector):
        el = self.page.query_selector(selector)
        return bool(el and el.is_visible())

    def phone(self):
        """One pane at a time, the way a phone reads mail.

        The list, then the conversation with a way back, then the record over
        it with a way back; and the mailbox list as a drawer that closes on the
        folder you picked. Asserted on what is on screen, because every pane
        is still in the same template and the difference is entirely which
        of them the width lets through.
        """
        page = self.page
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(600)

        # Shrinking the window mid-conversation keeps the conversation; the
        # check starts from the list either way.
        back = page.query_selector('.o_mailpro_back')
        if back:
            back.click()
            page.wait_for_timeout(400)
        if not self.visible('.o_mailpro_conversation_list'):
            self.fail('a phone does not open on the conversation list')
            return
        if self.visible('.o_mailpro_conversation'):
            self.fail('the conversation sits next to the list on a phone')
        list_width = page.query_selector('.o_mailpro_conversation_list').bounding_box()['width']
        if list_width < 370:
            self.fail('the list takes %dpx of a 390px phone' % list_width)

        # The mailbox list is a drawer: absent until asked for, over the list while
        # open, gone again once a folder is picked.
        if self.visible('.o_mailpro_mailbox_list'):
            self.fail('the mailbox list takes space on a phone before it is asked for')
        if page.query_selector('.o_mailpro_pane_toggle_conversation_list') or \
                page.query_selector('.o_mailpro_pane_toggle_odoo_record'):
            self.fail('a phone shows toggles for panes that take turns anyway')
        button = page.query_selector('.o_mailpro_pane_toggle_mailbox_list')
        if not button or not button.is_visible():
            self.fail('a phone has no button for the mailbox list')
            return
        button.click()
        page.wait_for_timeout(400)
        if not self.visible('.o_mailpro_mailbox_list'):
            self.fail('the mailbox list button opened nothing on a phone')
        else:
            drawer = page.query_selector('.o_mailpro_mailbox_list').bounding_box()
            if drawer['width'] > 340:
                self.fail('the mailbox list drawer covers the whole phone')
            self.shot('inbox-phone-mailboxes.png')
            folder = page.query_selector('.o_mailpro_folder')
            if folder:
                folder.click()
                page.wait_for_timeout(800)
            if self.visible('.o_mailpro_mailbox_list'):
                self.fail('picking a folder left the mailbox list drawer open')

        item = page.query_selector('.o_mailpro_item')
        if not item:
            self.fail('no conversation to open on the phone')
            return
        item.click()
        page.wait_for_timeout(1200)
        if not self.visible('.o_mailpro_conversation'):
            self.fail('opening a conversation on a phone showed nothing')
            return
        if self.visible('.o_mailpro_conversation_list'):
            self.fail('the list stayed on screen under the conversation on a phone')
        self.shot('inbox-phone.png')

        # The record, over the conversation, and the way back from it.
        button = page.query_selector('.o_mailpro_odoo_record_button')
        if not button:
            self.fail('the phone conversation head offers no way to the record')
        else:
            button.click()
            page.wait_for_timeout(800)
            record = page.query_selector('.o_mailpro_odoo_record')
            if not record or not record.is_visible():
                self.fail('the Record button showed no record on a phone')
            else:
                share = record.bounding_box()['width'] / 390
                if share < 0.9:
                    self.fail('the phone record takes %d%% of the screen' % (share * 100))
                self.shot('inbox-phone-record.png')
                page.query_selector('.o_mailpro_odoo_record_zoom').click()
                page.wait_for_timeout(500)
                if not self.visible('.o_mailpro_conversation'):
                    self.fail('Back to the Inbox did not bring the conversation back on a phone')

        # Writing takes the whole phone: the composer is as wide as the
        # screen, the conversation's chips are gone from above it, and the
        # back arrow with them -- Discard is the way out of a draft.
        # Reply lives on the Mail tab, and an earlier check may have left
        # the strip elsewhere; a check that skips itself on a stale tab
        # proves nothing.
        mail_tab = page.query_selector('.o_mailpro_tab:has(span:text-is("Mail"))')
        if mail_tab:
            mail_tab.click()
            page.wait_for_timeout(600)
        reply = page.query_selector('.o_mailpro_conversation_head button:has-text("Reply")')
        if not reply:
            self.fail('the phone conversation has no Reply button on the Mail tab')
        else:
            reply.click()
            try:
                page.wait_for_selector('.o_mailpro_composer .o_form_view', timeout=15000)
                page.wait_for_timeout(600)
                composer = page.query_selector('.o_mailpro_composer')
                if composer.bounding_box()['width'] < 370:
                    self.fail('the phone composer takes %dpx of a 390px screen'
                              % composer.bounding_box()['width'])
                if self.visible('.o_mailpro_chips'):
                    self.fail('the Linked-to chips stay above the phone composer')
                if self.visible('.o_mailpro_back'):
                    self.fail('the back arrow offers to drop the draft on a phone')
                self.shot('inbox-phone-compose.png')
                page.click('.o_mailpro_conversation_head button:has-text("Discard")')
                page.wait_for_timeout(500)
            except Exception as exc:
                self.fail(f'Reply on a phone opened no composer: {exc}')

        back = page.query_selector('.o_mailpro_back')
        if not back:
            self.fail('the phone conversation has no way back to the list')
            return
        back.click()
        page.wait_for_timeout(400)
        if not self.visible('.o_mailpro_conversation_list'):
            self.fail('Back did not bring the list back on a phone')

    def panes(self):
        """The dividers move, the side panes fold, and the browser remembers.

        The remembering is the half that cannot be seen in a screenshot and is
        the half people notice: a width you have to set again every morning is
        worse than one you were never offered. So this one reloads the page
        and looks again.
        """
        page = self.page

        divider = page.query_selector('.o_mailpro_split_conversation_list')
        pane = page.query_selector('.o_mailpro_conversation_list')
        if not divider or not pane:
            self.fail('the conversation list has no divider to drag')
            return

        before = pane.bounding_box()['width']
        box = divider.bounding_box()
        # 200px down the strip, well clear of anything sticky at the top of
        # the panes it sits between.
        grab = (box['x'] + box['width'] / 2, box['y'] + 200)
        page.mouse.move(*grab)
        page.mouse.down()
        page.mouse.move(grab[0] + 90, grab[1], steps=8)
        page.mouse.up()
        page.wait_for_timeout(400)

        widened = page.query_selector('.o_mailpro_conversation_list').bounding_box()['width']
        if widened - before < 40:
            self.fail('dragging the divider 90px moved the list %dpx'
                      % (widened - before))

        fold = page.query_selector('.o_mailpro_pane_toggle_odoo_record')
        if not fold:
            self.fail('the record pane cannot be folded away')
            return
        fold.click()
        page.wait_for_timeout(400)
        record = page.query_selector('.o_mailpro_odoo_record')
        if record and record.is_visible():
            self.fail('the record pane did not fold away')
        self.shot('inbox-folded.png')

        # Both of those are a preference, not a gesture: they survive the
        # reload or they were never worth storing.
        page.reload(wait_until='domcontentloaded')
        try:
            page.wait_for_selector('.o_mailpro_item', timeout=30000)
        except Exception:
            self.fail('the Inbox did not come back after a reload')
            return
        page.wait_for_timeout(1200)

        record = page.query_selector('.o_mailpro_odoo_record')
        if record and record.is_visible():
            self.fail('the folded record pane came back on reload')
        kept = page.query_selector('.o_mailpro_conversation_list').bounding_box()['width']
        if abs(kept - widened) > 8:
            self.fail('the list width was %dpx before the reload and %dpx after'
                      % (widened, kept))

        # The same button, in the same place, is the way back: a control that
        # moves when the thing it controls folds is a control you hunt for.
        toggle = page.query_selector('.o_mailpro_pane_toggle_odoo_record')
        if not toggle or not toggle.is_visible():
            self.fail('the folded record pane left no button to bring it back')
        else:
            if toggle.get_attribute('aria-pressed') != 'false':
                self.fail('the record is folded but its button still reads pressed')
            toggle.click()
        divider = page.query_selector('.o_mailpro_split_conversation_list')
        if divider:
            divider.dblclick(position={'x': 2, 'y': 200})
        page.wait_for_timeout(500)
        if not self.visible('.o_mailpro_odoo_record'):
            self.fail('the record pane did not come back when unfolded')

        # The list folds too, the same way, and the conversation takes the
        # room it leaves.
        thread_before = page.query_selector('.o_mailpro_conversation').bounding_box()['width']
        fold = page.query_selector('.o_mailpro_pane_toggle_conversation_list')
        if not fold:
            self.fail('the conversation list cannot be folded away')
            return
        fold.click()
        page.wait_for_timeout(400)
        if self.visible('.o_mailpro_conversation_list'):
            self.fail('the conversation list did not fold away')
        thread_after = page.query_selector('.o_mailpro_conversation').bounding_box()['width']
        if thread_after - thread_before < 100:
            self.fail('folding the list gave the conversation %dpx, not the list\'s width'
                      % (thread_after - thread_before))
        self.shot('inbox-list-folded.png')

        # Both left panes folded. This is the shape the buttons used to be
        # read in: two of them floating over the conversation's header,
        # beside its title, its chip row and its tab strip -- a second menu
        # bar on top of the screen's first. Nothing floats there now, the
        # conversation starts at its own pane's edge, and both buttons are
        # where they always are.
        page.query_selector('.o_mailpro_pane_toggle_mailbox_list').click()
        page.wait_for_timeout(400)
        if self.visible('.o_mailpro_mailbox_list'):
            self.fail('the mailbox list did not fold away')
        if page.query_selector('.o_mailpro_split_toggle'):
            self.fail('a fold button still floats over a pane')
        conversation = page.query_selector('.o_mailpro_conversation')
        title = page.query_selector('.o_mailpro_conversation_title')
        if conversation and title and \
                title.bounding_box()['x'] - conversation.bounding_box()['x'] > 40:
            self.fail('the conversation title still starts %dpx into its pane'
                      % (title.bounding_box()['x'] - conversation.bounding_box()['x']))
        mailbox_list_btn = page.query_selector('.o_mailpro_pane_toggle_mailbox_list')
        conversation_list_btn = page.query_selector('.o_mailpro_pane_toggle_conversation_list')
        new_btn = page.query_selector('.o_mailpro_new')
        if not mailbox_list_btn or not conversation_list_btn or not new_btn:
            self.fail('folding the mailbox list and the conversation list together lost a button')
        elif mailbox_list_btn.bounding_box()['x'] > new_btn.bounding_box()['x']:
            self.fail('the pane toggles sit to the right of New Email')
        else:
            self.shot('inbox-two-folded.png')
            mailbox_list_btn.click()
            page.wait_for_timeout(400)
            if not self.visible('.o_mailpro_mailbox_list'):
                self.fail('the menu button did not bring the mailbox list back')
            if not conversation_list_btn.query_selector('.fa-list-ul'):
                self.fail('the list button is not the list icon')
            conversation_list_btn.click()
            page.wait_for_timeout(400)
            if not self.visible('.o_mailpro_conversation_list'):
                self.fail('the button did not bring the list back')

    def zoom(self):
        """The record on the whole screen, and the way back out of it.

        Zoom is the one pane state that is not stored, so this one asserts the
        opposite of what `panes` asserts: a reload lands on the Inbox, not on
        the record somebody was reading yesterday.
        """
        page = self.page

        button = page.query_selector('.o_mailpro_odoo_record_zoom')
        if not button:
            # The reload above lands on the list; pick a conversation so the
            # fourth pane has a record to zoom.
            item = page.query_selector('.o_mailpro_item')
            if item:
                item.click()
                page.wait_for_timeout(1200)
            button = page.query_selector('.o_mailpro_odoo_record_zoom')
        if not button:
            self.fail('the record pane has no control to take the screen')
            return

        # One way to make the record bigger. Leaving for the record's own
        # screen is offered once you are on the whole screen, not beside it.
        if page.query_selector('.o_mailpro_odoo_record_open'):
            self.fail('Open sits next to Expand in the record pane')

        # It is an icon, the same quiet round one the pane toggles wear --
        # the label it used to carry is the tooltip now.
        if not button.query_selector('.fa-expand'):
            self.fail('Expand is not the expand arrows')
        if button.inner_text().strip():
            self.fail('the Expand button still carries a text label')
        if not button.get_attribute('aria-label'):
            self.fail('the icon-only Expand button has no accessible name')

        button.click()
        page.wait_for_timeout(500)

        for selector, name in (('.o_mailpro_conversation_list', 'the conversation list'),
                               ('.o_mailpro_conversation', 'the conversation'),
                               ('.o_mailpro_mailbox_list', 'the mailbox list'),
                               ('.o_mailpro_split_conversation_list', 'a divider')):
            pane = page.query_selector(selector)
            if pane and pane.is_visible():
                self.fail(f'{name} is still on screen while the record is zoomed')

        open_link = page.query_selector('.o_mailpro_odoo_record_open')
        if not open_link:
            self.fail('the zoomed record has no way to its own screen')
        elif not open_link.query_selector('.fa-external-link'):
            self.fail('the way to the record\'s own screen wears no external arrow')

        if not page.query_selector('.o_mailpro_odoo_record_zoom .fa-compress'):
            self.fail('the zoomed record offers no way back to the pane')

        record = page.query_selector('.o_mailpro_odoo_record')
        panes = page.query_selector('.o_mailpro_panes')
        if not record or not record.is_visible():
            self.fail('the record pane went away when it was zoomed')
            return
        share = record.bounding_box()['width'] / panes.bounding_box()['width']
        if share < 0.9:
            self.fail('the zoomed record takes %d%% of the screen' % (share * 100))
        self.shot('inbox-zoom.png')

        page.query_selector('.o_mailpro_odoo_record_zoom').click()
        page.wait_for_timeout(500)
        for selector, name in (('.o_mailpro_conversation_list', 'the conversation list'),
                               ('.o_mailpro_conversation', 'the conversation')):
            pane = page.query_selector(selector)
            if not pane or not pane.is_visible():
                self.fail(f'{name} did not come back when the zoom was closed')

        # A reading mode, not a preference: the reload lands on the Inbox.
        page.query_selector('.o_mailpro_odoo_record_zoom').click()
        page.wait_for_timeout(400)
        page.reload(wait_until='domcontentloaded')
        try:
            page.wait_for_selector('.o_mailpro_item', timeout=30000)
        except Exception:
            self.fail('the Inbox did not come back after a reload')
            return
        page.wait_for_timeout(1200)
        pane = page.query_selector('.o_mailpro_conversation_list')
        if not pane or not pane.is_visible():
            self.fail('the reload came back zoomed on the record')

    # -- The provider form ----------------------------------------------------

    # What each provider's registration asks for, under the name its own
    # console uses -- Azure's three fields are labelled the way Azure labels
    # them, Google's the way Google does. Microsoft is the only one with a
    # tenant; IMAP has no registration at all, and says so.
    MICROSOFT_FIELDS = ('Application (client) ID', 'Client Secret Value',
                        'Directory (tenant) ID')
    GOOGLE_FIELDS = ('Client ID', 'Client Secret')
    # These are substring checks, and Google's two labels are both prefixes of
    # nothing on the Microsoft form except "Client Secret", which is a prefix
    # of "Client Secret Value". So a form is proved to be Google's by the label
    # that cannot appear on Microsoft's.
    GOOGLE_ONLY = ('Client ID',)
    PROVIDER_FIELDS = {
        'outlook': {'shows': MICROSOFT_FIELDS + ('Callback URL',),
                    'hides': GOOGLE_ONLY + ('has no application registration',)},
        'gmail': {'shows': GOOGLE_FIELDS + ('Callback URL',),
                  'hides': MICROSOFT_FIELDS + ('has no application registration',)},
        'imap': {'shows': ('has no application registration',),
                 'hides': MICROSOFT_FIELDS + GOOGLE_ONLY + ('Callback URL',)},
    }

    def provider_form(self):
        """Each provider asks for its own credentials, and only for those.

        There is one provider row, so the three shapes are walked by switching
        it — which is also how an admin moves to another provider now. An
        empty new record used to open carrying IMAP's explanation, because
        "has no OAuth" and "nothing chosen yet" were the same condition.
        """
        action = dict((name, aid) for name, aid in module_menu_actions(self.call)).get('Providers')
        if not action:
            self.fail('there is no Providers menu')
            return
        rows = self.call('pan.mail.provider', 'search_read', [], fields=['provider'])
        if len(rows) != 1:
            self.fail(f'there are {len(rows)} provider rows, expected exactly 1')
            return
        row_id, was = rows[0]['id'], rows[0]['provider']

        try:
            for code, expected in self.PROVIDER_FIELDS.items():
                self.call('pan.mail.provider', 'write', [row_id], {'provider': code})
                text = self.form_text(f'{self.base}/odoo/action-{action}/{row_id}')
                self.shot(f'provider-{code}.png')
                for shown in expected['shows']:
                    if shown not in text:
                        self.fail(f'the {code} form does not show "{shown}"')
                for hidden in expected['hides']:
                    if hidden in text:
                        self.fail(f'the {code} form shows "{hidden}", which is not its')
        finally:
            self.call('pan.mail.provider', 'write', [row_id], {'provider': was})

        text = self.form_text(f'{self.base}/odoo/action-{action}/new')
        self.shot('provider-new.png')
        for hidden in (('has no application registration',)
                       + self.MICROSOFT_FIELDS + self.GOOGLE_ONLY):
            if hidden in text:
                self.fail(f'a new provider, with nothing chosen yet, shows "{hidden}"')

    # -- The connect banner ---------------------------------------------------

    def connect_banner(self):
        """It reaches the people who have to act, and nobody else.

        Both halves are the check. The banner lives inside `web.WebClient`
        rather than in an action, so a wrong xpath takes the whole client down
        and no server-side check can see it -- and one shown to somebody who is
        already connected is a bar on every screen that teaches people to stop
        reading bars. The seed connects admin, so admin must not see it; a
        colleague who has not signed in must.
        """
        self.page.goto(f'{self.base}/odoo/settings', wait_until='domcontentloaded')
        self.page.wait_for_selector('.o_main_navbar', timeout=60000)
        self.page.wait_for_timeout(1200)
        self.error_free('the webclient')
        if self.page.query_selector('.o_mailpro_connect_banner'):
            self.fail('the connect banner is shown to a user who is already connected')

        login = 'ui-unconnected@example.com'
        found = self.call('res.users', 'search', [('login', '=', login)])
        if not found:
            self.call('res.users', 'create', {
                'name': 'Not Connected Yet', 'login': login, 'password': login,
                'group_ids': [(6, 0, self.call(
                    'ir.model.data', 'check_object_reference', 'base', 'group_user')[1:])],
            })

        page = self.browser.new_context(
            viewport={'width': WIDE, 'height': 1100}).new_page()
        try:
            page.goto(f'{self.base}/web/login', wait_until='domcontentloaded')
            page.fill('input[name=login]', login)
            page.fill('input[name=password]', login)
            page.click('button[type=submit]')
            page.wait_for_selector('.o_main_navbar', timeout=60000)
            page.wait_for_timeout(1500)
            banner = page.query_selector('.o_mailpro_connect_banner')
            if not banner:
                self.fail('a user who has not connected a mailbox is never asked to')
                return
            if self.out:
                page.screenshot(path=os.path.join(self.out, 'connect-banner.png'))
            box = banner.bounding_box()
            navbar = page.query_selector('.o_main_navbar').bounding_box()
            if box['y'] < navbar['y'] + navbar['height'] - 1:
                self.fail('the connect banner covers the navbar instead of sitting under it')
            content = page.query_selector('.o_action_manager, .o_content').bounding_box()
            if content['y'] < box['y'] + box['height'] - 1:
                self.fail('the connect banner floats over the page instead of pushing it down')
            if not banner.query_selector('a[href="/mail_pro/connect"]'):
                self.fail('the connect banner has no way into the consent screen')
            self.banner_survives_enterprise(page)

            banner.query_selector('.o_mailpro_connect_close').click()
            page.wait_for_timeout(400)
            if page.query_selector('.o_mailpro_connect_banner'):
                self.fail('dismissing the connect banner does not hide it')
        finally:
            page.context.close()

    def banner_survives_enterprise(self, page):
        """The banner must not be reachable only through `WebClient.components`.

        Enterprise mounts `WebClientEnterprise`, whose class body copies
        `WebClient.components` at definition time -- before this module is
        loaded. A component this module adds to that dict is therefore absent
        from the class that is actually mounted, Owl cannot resolve the tag,
        and the webclient does not mount at all: a white screen on every
        Enterprise database, with nothing in the server log (#85).

        CI runs the community image, so the banner above is drawn either way.
        What tells the two apart is where it came from, and that is readable
        here: the banner rendered *and* nothing of ours is in that snapshot.
        """
        keys = page.evaluate(
            "() => { const m = odoo.loader.modules.get('@web/webclient/webclient');"
            " return m ? Object.keys(m.WebClient.components) : null; }")
        if keys is None:
            self.fail('cannot read WebClient.components — the check below proves nothing')
        elif any(key.startswith('MailPro') for key in keys):
            self.fail('the connect banner is registered in WebClient.components, '
                      'which Enterprise has already snapshotted — it will white-screen there')

    def form_text(self, url):
        self.page.goto(url, wait_until='domcontentloaded')
        self.page.wait_for_selector('.o_form_view', timeout=30000)
        self.page.wait_for_timeout(1200)
        return self.page.inner_text('.o_form_view')

    def check_followers(self, page):
        """Who Odoo notifies about the record, from the end of the tab strip.

        The button is ours; the list it opens is the chatter's own, so Follow
        and Unfollow have to move the count on the button the way they move
        the chatter's. The seeded record may already count the admin among
        its followers (whoever creates a record follows it), so the check
        starts from whichever of the two the list offers and ends where it
        began.
        """
        button = page.query_selector('.o_mailpro_followers')
        if not button or not button.is_visible():
            self.fail('the tab strip offers no Followers button')
            return
        if 'follow' not in button.inner_text().lower():
            self.fail('the followers button does not say what it opens: %r'
                      % button.inner_text())

        def count():
            el = page.query_selector('.o_mailpro_followers .o_mailpro_tab_count')
            return int(el.inner_text().strip()) if el else 0

        def open_list():
            page.query_selector('.o_mailpro_followers').click()
            page.wait_for_timeout(600)
            menu = page.query_selector('.o-mail-Followers-dropdown')
            return menu if menu and menu.is_visible() else None

        before = count()
        menu = open_list()
        if not menu:
            self.fail('the Followers button opened no list')
            return
        if not page.query_selector('.o-mail-Followers-dropdown a:has-text("Add Followers")'):
            self.fail('the follower list offers no Add Followers to an admin')
        following = bool(menu.query_selector('.o-mail-FollowerList-unfollowBtn'))
        first = '.o-mail-FollowerList-unfollowBtn' if following else '.o-mail-FollowerList-followBtn'
        second = '.o-mail-FollowerList-followBtn' if following else '.o-mail-FollowerList-unfollowBtn'
        step = -1 if following else 1
        self.shot('inbox-followers.png')
        menu.query_selector(first).click()
        page.wait_for_timeout(900)
        if count() != before + step:
            self.fail('%s left the followers count at %d, expected %d'
                      % ('Unfollow' if following else 'Follow', count(), before + step))
        label = page.query_selector('.o_mailpro_followers').inner_text().lower()
        if ('following' in label) == following:
            self.fail('the button still reads %r after %s'
                      % (label, 'Unfollow' if following else 'Follow'))
        # Leave the record the way it was found.
        menu = open_list()
        if not menu or not menu.query_selector(second):
            self.fail('the follower list offers no way back after %s'
                      % ('Unfollow' if following else 'Follow'))
            page.keyboard.press('Escape')
            return
        menu.query_selector(second).click()
        page.wait_for_timeout(900)
        if count() != before:
            self.fail('the followers count is %d after a round trip, expected %d'
                      % (count(), before))

    def check_files_tab(self, page):
        """The Files tab is Odoo's own attachment list, or it is a copy.

        The card, the viewer behind a click and the uploader are what people
        already know from the chatter; a row of links of our own looked fine
        and could do none of the three. So the assertion is on Odoo's own
        class names -- the moment this screen grows a file list of its own,
        this check is what says so.
        """
        card = page.query_selector('.o_mailpro_files .o-mail-AttachmentContainer')
        if not card:
            self.fail("the Files tab draws no attachment card")
            return
        self.shot('inbox-files.png')
        card.click()
        try:
            page.wait_for_selector('.o-FileViewer', timeout=10000)
        except Exception:
            self.fail('a file in the Files tab opens no viewer')
        else:
            close = page.query_selector('.o-FileViewer [aria-label="Close"]')
            if close:
                close.click()
            else:
                page.keyboard.press('Escape')
            page.wait_for_timeout(400)
        # Attaching a file is half of what the chatter's file list is for.
        if not page.query_selector('.o_mailpro_files input[type="file"]'):
            self.fail('the Files tab has no way to attach a file')

    def error_free(self, where):
        dialog = self.page.query_selector('.o_error_dialog, .o_dialog_error')
        if dialog:
            self.fail(f'{where} opened an error dialog: {dialog.inner_text()[:200]}')
        if not self.page.query_selector('.o_content'):
            self.fail(f'{where} rendered no view')


def rpc_for(url, db):
    """A `call(model, method, *args, **kw)` against the preview database."""
    uid = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common').authenticate(
        db, 'admin', 'admin', {})
    proxy = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

    def call(model, method, *args, **kw):
        return proxy.execute_kw(db, uid, 'admin', model, method, list(args), kw)
    return call


def module_menu_actions(call):
    """(name, action id) for every menu `pan_mail_pro` declares, from the server."""
    declared = call('ir.model.data', 'search_read',
                    [('module', '=', 'pan_mail_pro'), ('model', '=', 'ir.ui.menu')],
                    fields=['res_id'])
    menus = call('ir.ui.menu', 'read', [d['res_id'] for d in declared],
                 fields=['name', 'action'])
    found = []
    for menu in menus:
        # "ir.actions.act_window,232" — a menu without one is a section header.
        if not menu['action']:
            continue
        found.append((menu['name'], menu['action'].split(',')[1]))
    return found


def slug(name):
    return name.lower().replace(' ', '-')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8069')
    ap.add_argument('--out', default='', help='directory for screenshots')
    ap.add_argument('--db', default='ui_db', help='the database tools/ui_preview.sh made')
    args = ap.parse_args()
    if args.out:
        os.makedirs(args.out, exist_ok=True)

    with sync_playwright() as p:
        launch = {'executable_path': CHROME} if os.path.exists(CHROME) else {}
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': WIDE, 'height': 1100})
        page.goto(f'{args.url}/web/login', wait_until='domcontentloaded')
        page.fill('input[name=login]', 'admin')
        page.fill('input[name=password]', 'admin')
        page.click('button[type=submit]')
        page.wait_for_url('**/odoo**', timeout=60000)
        page.wait_for_timeout(1500)

        checks = Checks(page, args.out, browser)
        page.on('pageerror', lambda error:
                checks.js_errors.append(str(error).splitlines()[0][:300]))
        # Odoo's error service catches what Owl throws, so the only trace of
        # it outside the dialog is the console.
        page.on('console', lambda message: message.type == 'error'
                and checks.js_errors.append(message.text.splitlines()[0][:300]))
        checks.base = args.url
        checks.db = args.db
        checks.call = rpc_for(args.url, args.db)
        checks.settings()
        checks.settings_not_connected()
        checks.menus()
        checks.conversation_view()
        checks.linking()
        checks.provider_form()
        checks.connect_banner()
        browser.close()

    if checks.failures:
        print('UI check failed:')
        for failure in checks.failures:
            print(f'  - {failure}')
        for error in checks.js_errors:
            print(f'  js: {error}')
        sys.exit(1)
    print('UI check passed.')


if __name__ == '__main__':
    main()
