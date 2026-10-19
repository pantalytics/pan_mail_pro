# -*- coding: utf-8 -*-
"""
Every colour Mail Pro paints has a light and a dark side.

Odoo compiles each backend stylesheet twice, once per colour scheme, with
`$o-webclient-color-scheme` set to `dark` in the second pass. A palette token
picks its side from that; a literal does not, and in dark mode it is white
panes around a dark Odoo record and #001d21 text on #001d21. So outside the
palette lines themselves, no stylesheet names a colour.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCSS = os.path.join(MODULE, 'static', 'src', 'scss')

# A hex colour, or rgb()/rgba() with a number where the colour goes.
LITERAL = re.compile(r'#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d')

# Colours that mean the same thing in both schemes: the green "done" dot and
# a neutral grey tint.
ALLOWED = ('#1f9d63', 'rgba(120, 120, 120')


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestDarkMode(TransactionCase):

    def test_no_colour_outside_the_palette(self):
        found = []
        for name in sorted(os.listdir(SCSS)):
            with open(os.path.join(SCSS, name), encoding='utf-8') as handle:
                for number, line in enumerate(handle, 1):
                    code = line.split('//')[0]
                    if 'if($mailpro-dark' in code or any(ok in code for ok in ALLOWED):
                        continue
                    if LITERAL.search(code):
                        found.append('%s:%d: %s' % (name, number, line.strip()))
        self.assertFalse(found, 'A colour with no dark side:\n' + '\n'.join(found))

