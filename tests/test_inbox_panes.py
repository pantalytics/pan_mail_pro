# -*- coding: utf-8 -*-
"""
The Inbox has four panes and each one has exactly one name.

ARCHITECTURE.md section 1 fixes them -- rail, list, conversation, record --
in the code key, the CSS class, the label and the prose alike. The third one
was `thread` in the code and "the conversation pane" in every sentence about
it until 19.0.13.10.0, which is the drift this file exists to stop coming
back: "thread" means the mail thread the matcher keys on, and nothing else.

A naming rule nobody can run is a naming rule that lasts one release, so it
is asserted here rather than left to review.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PANES = ['rail', 'list', 'conversation', 'record']

# The screen itself. Its root class is not one of the panes: a pane called
# after the whole screen is how the two collided in the first place.
ROOT_CLASS = 'o_mailpro_inbox'

# Named for the mail thread, never for a pane. `recordThread` is Odoo's own
# Thread store model for the record's chatter, which is why it may stay.
RETIRED = ['o_mailpro_thread', 'thread pane', 'showThread', 'readThread']


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

    def test_use_panes_orders_the_four_names(self):
        match = re.search(r'const ORDER = \[([^\]]*)\]', self.panes_js)
        self.assertTrue(match, 'use_panes.js no longer declares ORDER')
        found = re.findall(r'"([a-z]+)"', match.group(1))
        self.assertEqual(found, PANES, 'ORDER is the left-to-right pane list')

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

    def test_the_old_names_are_gone(self):
        """A rename that leaves no check behind is a rename that comes back."""
        haystack = '\n'.join([
            self.panes_js, self.template, self.scss,
            read('static', 'src', 'js', 'conversation_view', 'conversation_view.js'),
            read('static', 'src', 'js', 'conversation_view', 'use_composer.js'),
            read('models', 'pan_mail_conversation.py'),
        ])
        for word in RETIRED:
            self.assertNotIn(word, haystack,
                             '"%s" names pane 3 again; it is `conversation`' % word)
