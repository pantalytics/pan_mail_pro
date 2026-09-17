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

    def fail(self, message):
        self.failures.append(message)

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

        # Pantalytics Account: on a database nobody has linked, one way in and
        # nothing of the pending or connected states leaking onto the screen.
        connect = [b for b in block.query_selector_all('button')
                   if b.is_visible() and b.inner_text().strip() == 'Connect to Pantalytics']
        if len(connect) != 1:
            self.fail(f'Pantalytics Account shows {len(connect)} Connect buttons, expected 1')
        for leaked in ('Check Approval', 'Disconnect'):
            if any(b.is_visible() and b.inner_text().strip() == leaked
                   for b in block.query_selector_all('button')):
                self.fail(f'an unlinked database shows "{leaked}"')

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

    # The five states that earn a line in the rail. More than five and the
    # rail is a filter panel; fewer and people ask where their mail went.
    # "Sent" is not one of them: it was the same query as "Waiting on
    # customer", and the provider's own Sent folder already exists.
    FOLDERS = ('Inbox', 'Needs reply', 'Waiting on customer',
               'On a contact only', 'Linked to nothing')

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
            page.wait_for_selector('.o_mailpro_conversation', timeout=30000)
        except Exception:
            self.fail('the Inbox did not render at all')
            return
        page.wait_for_timeout(2500)
        self.error_free('Inbox')

        rail = [el.inner_text().split('\n')[0].strip()
                for el in page.query_selector_all('.o_mailpro_folder')]
        if rail != list(self.FOLDERS):
            self.fail(f'the folder rail reads {rail}, expected {list(self.FOLDERS)}')

        # The mailbox sits in the rail above its own folders, the way it does
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
            self.fail(f'{len(names)} mailboxes in the rail, expected the seeded 2')
        else:
            active = [el.inner_text().strip()
                      for el in page.query_selector_all('.o_mailpro_mailbox_active')]
            if active != names[:1]:
                self.fail(f'the rail opens on {active}, expected {names[:1]}')
            if open_mailbox(1) != names[1:2]:
                self.fail('clicking a mailbox did not open it')
            # Back to the one the seeded mail is in, so everything below reads
            # the filled screen.
            open_mailbox(0)

        # Everything clickable is a real button, so a keyboard can reach it.
        for selector, what in (('.o_mailpro_mailbox', 'mailbox'),
                               ('.o_mailpro_folder', 'folder'),
                               ('.o_mailpro_item', 'conversation')):
            divs = [el for el in page.query_selector_all(selector)
                    if el.evaluate('el => el.tagName') != 'BUTTON']
            if divs:
                self.fail(f'{len(divs)} {what} rows are not buttons')

        items = page.query_selector_all('.o_mailpro_item')
        if not items:
            self.fail('the conversation list is empty with seeded mail on a lead')
        else:
            selected = page.query_selector_all('.o_mailpro_item_active')
            if len(selected) != 1:
                self.fail(f'{len(selected)} conversations look selected, expected 1')

        messages = page.query_selector_all('.o_mailpro_message')
        if not messages:
            self.fail('the thread pane shows no messages')
        elif len(messages) > 1:
            # A thread is a stack, the way every mail client draws one: the
            # message you came for is open and the history above it is one
            # line each. Nine open bodies is a page you have to scroll to find
            # the end of, and the end is the part anybody reads first.
            opened = page.query_selector_all('.o_mailpro_message_open')
            if len(opened) != 1:
                self.fail(f'{len(opened)} messages are open, expected 1')
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

        # The fourth pane is the product. If the form view cannot mount, the
        # pane falls back and this is the only place that would notice.
        if page.query_selector('.o_mailpro_record .o_form_view') is None:
            self.fail('the record pane did not mount the record form')

        # ...and the form's own statusbar buttons stay out of it. A filled
        # "Convert to Opportunity" in the fourth pane is a louder button than
        # Reply, on a screen whose one job is replying. The chatter's own
        # composer stays: it is how you log an internal note, and it is the
        # control people already know from every other Odoo screen.
        loud = [b for b in page.query_selector_all(
            '.o_mailpro_record .o_form_statusbar button') if b.is_visible()]
        if loud:
            self.fail('the record pane shows %d form buttons beside Reply'
                      % len(loud))

        # The screen's one primary action. A reader-only inbox is half a
        # product, and this is the click that proves it is not one.
        reply = page.query_selector('.o_mailpro_thread_head button.btn-primary')
        if not reply:
            self.fail('the thread has no Reply button')
        else:
            reply.click()
            try:
                page.wait_for_selector('.modal .o_form_view', timeout=15000)
            except Exception:
                self.fail('Reply opened no composer')
            else:
                # The chatter fills "To" from the record; the composer on its
                # own fills nothing, and a reply to nobody is the one bug a
                # green suite cannot see. The seeded thread has a customer,
                # so their tag has to be there before anyone types.
                page.wait_for_timeout(600)
                if not page.query_selector('.modal [name="partner_ids"] .o_tag'):
                    self.fail('Reply opened a composer with nobody in To')
                # Discard rather than Escape: Escape leaves the composer open
                # on a draft, and the screenshot below is what a reviewer
                # looks at.
                discard = page.query_selector('.modal button:has-text("Discard")')
                if discard:
                    discard.click()
                else:
                    page.keyboard.press('Escape')
                page.wait_for_selector('.modal', state='detached', timeout=15000)
                page.wait_for_timeout(600)

        self.shot('inbox.png')

        # A wide monitor is where the complaint arrives from, and a narrow one
        # is where the fourth pane is meant to step aside rather than squeeze.
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.wait_for_timeout(600)
        record = page.query_selector('.o_mailpro_record')
        if record and record.is_visible():
            self.fail('the record pane still takes space at 1280px')
        self.shot('inbox-narrow.png')
        page.set_viewport_size({'width': WIDE, 'height': 1100})
        page.wait_for_timeout(400)

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
        checks.base = args.url
        checks.db = args.db
        checks.call = rpc_for(args.url, args.db)
        checks.settings()
        checks.menus()
        checks.conversation_view()
        checks.provider_form()
        checks.connect_banner()
        browser.close()

    if checks.failures:
        print('UI check failed:')
        for failure in checks.failures:
            print(f'  - {failure}')
        sys.exit(1)
    print('UI check passed.')


if __name__ == '__main__':
    main()
