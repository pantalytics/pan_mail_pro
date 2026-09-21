# -*- coding: utf-8 -*-
"""
The Inbox has four panes and each one has exactly one name.

ARCHITECTURE.md section 1 fixes them -- mailbox_list, conversation_list,
conversation, odoo_record -- in the code key, the CSS class, the label and the
prose alike. Each is named after what is in it, which `rail` never was and
`record` only half was.

This is the drift the file exists to stop coming back: "thread" means the mail
thread the matcher keys on and never a pane, and "rail" means nothing here at
all any more.

A naming rule nobody can run is a naming rule that lasts one release, so it
is asserted here rather than left to review.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PANES = ['mailbox_list', 'conversation_list', 'conversation', 'odoo_record']

# The screen itself. Its root class is not one of the panes: a pane called
# after the whole screen is how the two collided in the first place.
ROOT_CLASS = 'o_mailpro_inbox'

# Words that named a pane and no longer do. `recordThread` survives the
# `rail` sweep because it is Odoo's own Thread store model for the record's
# chatter, and none of these strings appear in it.
RETIRED = ['o_mailpro_thread', 'thread pane', 'showThread', 'readThread',
           'rail', 'o_mailpro_list', 'o_mailpro_record']


def read(*parts):
    with open(os.path.join(MODULE, *parts), encoding='utf-8') as handle:
        return handle.read()


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestInboxPanes(TransactionCase):
    """One word per pane, in all four places that name one."""

    def setUp(self):
        super().setUp()
        self.panes_js = read('static', 'src', 'js', 'conversation_view', 'use_panes.js')
        self.template = read('static', 'src', 'xml', 'conversation_view.xml')
        self.scss = read('static', 'src', 'scss', 'conversation_view.scss')
        self.architecture = read('ARCHITECTURE.md')

    def test_use_panes_knows_the_four_names(self):
        """The three lists a pane name has to be on to exist at all."""
        for const in ('ICONS', 'PANES'):
            block = re.search(r'const %s = \{(.*?)\n\};' % const,
                              self.panes_js, re.S)
            self.assertTrue(block, 'use_panes.js no longer declares %s' % const)
            found = re.findall(r'\n    ([a-z_]+):', block.group(1))
            expected = PANES if const == 'ICONS' else [
                name for name in PANES if name != 'conversation']
            self.assertEqual(found, expected,
                             '%s does not list the panes, in order' % const)
        folds = re.search(r'const COLLAPSIBLE = \[([^\]]*)\]', self.panes_js)
        self.assertTrue(folds, 'use_panes.js no longer declares COLLAPSIBLE')
        self.assertEqual(
            re.findall(r'"([a-z_]+)"', folds.group(1)),
            [name for name in PANES if name != 'conversation'],
            'the conversation is the one pane that never folds')

    def test_every_pane_has_an_icon_and_a_label(self):
        for name in PANES:
            self.assertRegex(
                self.panes_js, r'\n    %s: "fa-' % name,
                '%s has no icon to come back as when folded' % name)
            self.assertRegex(
                self.panes_js, r'\n        %s: _t\(' % name,
                '%s has no label' % name)

    def test_the_template_draws_a_pane_per_name(self):
        self.assertIn(ROOT_CLASS, self.template, 'the screen lost its root class')
        for name in PANES:
            self.assertIn('o_mailpro_%s"' % name, self.template,
                          'no pane element carries o_mailpro_%s' % name)

    def test_architecture_documents_the_names(self):
        for name in PANES:
            self.assertIn('`o_mailpro_%s`' % name, self.architecture,
                          '%s is not in the pane table in ARCHITECTURE.md' % name)
        self.assertIn('`%s`' % ROOT_CLASS, self.architecture)

    def test_the_browser_check_names_real_panes(self):
        """The class the top bar builds is `o_mailpro_pane_toggle_<pane>`.

        Nothing errors when a selector names a pane that no longer exists:
        `query_selector` just answers None and the check reports the button
        as missing. That is exactly how the rename reached 19.0 red.
        """
        ui_check = read('tools', 'ui_check.py')
        for name in set(re.findall(r'o_mailpro_pane_toggle_([a-z_]+)', ui_check)):
            self.assertIn(name, PANES,
                          'ui_check.py looks for the toggle of "%s", '
                          'which is not a pane' % name)

    def test_the_old_names_are_gone(self):
        """A rename that leaves no check behind is a rename that comes back."""
        # `LEGACY` is the one line allowed to spell the old keys: it reads a
        # layout stored under them and never writes one back.
        panes_js = re.sub(r'^const LEGACY = .*$', '', self.panes_js, flags=re.M)
        haystack = '\n'.join([
            panes_js, self.template, self.scss,
            read('static', 'src', 'js', 'conversation_view', 'conversation_view.js'),
            read('static', 'src', 'js', 'conversation_view', 'use_composer.js'),
            read('models', 'pan_mail_conversation.py'),
        ])
        for word in RETIRED:
            # Whole words: `rail` must not match "trailing".
            self.assertNotRegex(haystack, r'\b%s\b' % word,
                                '"%s" is a retired pane name' % word)
