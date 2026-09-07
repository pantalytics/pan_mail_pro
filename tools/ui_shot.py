#!/usr/bin/env python3
"""Screenshot a settings tab of the instance `tools/ui_preview.sh` is running.

    pip install playwright                       # once; the browser is preinstalled
    tools/ui_shot.py out.png                     # Settings → Mail Pro at 1440px
    tools/ui_shot.py out.png --width=2000        # the width a real desk monitor has

A view that renders is not a view that reads. This is how you look at it
without a laptop, and how a "before" and an "after" become comparable.
"""
import argparse
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright is not installed — pip install playwright")

CHROME = os.environ.get('PAN_UI_CHROME', '/opt/pw-browsers/chromium')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--url', default='http://localhost:8069')
    ap.add_argument('--tab', default='pan_mail_pro', help='data-key of the settings tab')
    ap.add_argument('--width', type=int, default=1440)
    args = ap.parse_args()

    with sync_playwright() as p:
        launch = {'executable_path': CHROME} if os.path.exists(CHROME) else {}
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': args.width, 'height': 900})
        page.goto(f'{args.url}/web/login', wait_until='domcontentloaded')
        page.fill('input[name=login]', 'admin')
        page.fill('input[name=password]', 'admin')
        page.click('button[type=submit]')
        page.wait_for_url('**/odoo**', timeout=60000)
        page.goto(f'{args.url}/odoo/settings', wait_until='domcontentloaded')
        page.wait_for_selector(f'a.tab[data-key={args.tab}]', timeout=60000)
        page.click(f'a.tab[data-key={args.tab}]')
        page.wait_for_timeout(1500)
        block = page.query_selector(f'div.app_settings_block[data-key={args.tab}]')
        print(block.inner_text() if block else '(tab not found)')
        page.screenshot(path=args.out, full_page=True)
        print('saved', args.out)
        browser.close()


if __name__ == '__main__':
    main()
