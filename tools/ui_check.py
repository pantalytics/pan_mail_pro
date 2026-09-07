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
    def __init__(self, page, out):
        self.page = page
        self.out = out
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

    # -- The provider form ----------------------------------------------------

    # What each provider's registration asks for. Microsoft is the only one
    # with a tenant; IMAP has no registration at all, and says so.
    PROVIDER_FIELDS = {
        'outlook': {'shows': ('Client ID', 'Client Secret', 'Tenant ID', 'Callback URL'),
                    'hides': ('has no application registration',)},
        'gmail': {'shows': ('Client ID', 'Client Secret', 'Callback URL'),
                  'hides': ('Tenant ID', 'has no application registration')},
        'imap': {'shows': ('has no application registration',),
                 'hides': ('Client ID', 'Client Secret', 'Tenant ID', 'Callback URL')},
    }

    def provider_form(self):
        """Each provider asks for its own credentials, and only for those.

        An empty new record used to open carrying IMAP's explanation, because
        "has no OAuth" and "nothing chosen yet" were the same condition.
        """
        action = dict((name, aid) for name, aid in module_menu_actions(self.call)).get('Providers')
        if not action:
            self.fail('there is no Providers menu')
            return
        rows = self.call('pan.mail.provider', 'search_read', [], fields=['provider'])

        for row in rows:
            expected = self.PROVIDER_FIELDS.get(row['provider'])
            if not expected:
                continue
            text = self.form_text(f'{self.base}/odoo/action-{action}/{row["id"]}')
            self.shot(f'provider-{row["provider"]}.png')
            for shown in expected['shows']:
                if shown not in text:
                    self.fail(f'the {row["provider"]} form does not show "{shown}"')
            for hidden in expected['hides']:
                if hidden in text:
                    self.fail(f'the {row["provider"]} form shows "{hidden}", which is not its')

        text = self.form_text(f'{self.base}/odoo/action-{action}/new')
        self.shot('provider-new.png')
        for hidden in ('has no application registration', 'Client ID', 'Tenant ID'):
            if hidden in text:
                self.fail(f'a new provider, with nothing chosen yet, shows "{hidden}"')

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

        checks = Checks(page, args.out)
        checks.base = args.url
        checks.db = args.db
        checks.call = rpc_for(args.url, args.db)
        checks.settings()
        checks.menus()
        checks.provider_form()
        browser.close()

    if checks.failures:
        print('UI check failed:')
        for failure in checks.failures:
            print(f'  - {failure}')
        sys.exit(1)
    print('UI check passed.')


if __name__ == '__main__':
    main()
