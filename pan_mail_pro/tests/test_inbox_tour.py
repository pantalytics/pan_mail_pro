# -*- coding: utf-8 -*-
"""
The Inbox's tour (#393): Odoo's own `web_tour`, four sentences, once per user.

The walk itself is `tools/ui_check.py`'s (`inbox_tour`): a tour is a browser
thing, and whether it starts, moves on and stops is only true in one. What is
asserted here is what the browser leans on and cannot see: the row that makes
"once per user" possible, that a plain user may read it, and that the screen,
the registry and the row all say the same name.
"""
import os
import re

from odoo.tests import TransactionCase, new_test_user, tagged

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOUR = 'pan_mail_pro_inbox_tour'

# The four sentences, as the tour says them. Checked against the code they
# describe: `pan.mail.routing.log.link_to` moves the whole conversation and
# repoints its thread link; unmatched mail lands on the sender's contact
# (`pan_mail_fetcher`), which is the "On a contact only" filter and not a
# folder; the search view searches sender, subject, contact and body, never
# the record, and within the open folder.
SENTENCES = [
    "Every conversation sits on an Odoo record. Your reply lands in its chatter.",
    "Wrong record? Link it to another one. The rest of the conversation follows.",
    "Mail that matched no record lands on the sender's contact. "
    "Find it under <b>On a contact only</b>.",
    "Search this folder by sender, subject or a word in the mail.",
]


def read(*parts):
    with open(os.path.join(MODULE, *parts), encoding='utf-8') as handle:
        return handle.read()


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestInboxTour(TransactionCase):

    def setUp(self):
        super().setUp()
        self.tour = self.env.ref('pan_mail_pro.inbox_tour')
        self.js = read('static', 'src', 'js', 'conversation_view', 'inbox_tour.js')

    def steps(self):
        """The step objects of the registry entry, as source."""
        return re.findall(r'\{\s*(?://[^\n]*\n\s*)*isActive:.*?\n        \}', self.js, re.S)

    def seen(self, user):
        """The question the Inbox asks before it offers the tour, as that user."""
        return self.env['web_tour.tour'].with_user(user).search_count([
            ('name', '=', TOUR), ('user_consumed_ids', 'in', [user.id])])

    def test_the_row_is_odoo_s_own_tour(self):
        """A tour row, last in Onboarding, starting on the Inbox, no fanfare."""
        self.assertEqual(self.tour.name, TOUR)
        self.assertFalse(self.tour.custom, "a custom tour is one somebody recorded")
        later = self.env['web_tour.tour'].search(
            [('custom', '=', False), ('sequence', '>', self.tour.sequence)])
        self.assertFalse(later, "the Inbox tour cuts into Odoo's own onboarding order")
        self.assertFalse(self.tour.rainbow_man_message)
        xmlid = self.tour.url.rsplit('action-', 1)[1]
        self.assertEqual(self.env.ref(xmlid).tag, 'pan_mail_conversation_view')

    def test_once_per_user(self):
        """Consumed by one person is seen by that person and nobody else."""
        anna = new_test_user(self.env, login='tour_anna', groups='base.group_user')
        bert = new_test_user(self.env, login='tour_bert', groups='base.group_user')
        self.assertFalse(self.seen(anna))
        self.env['web_tour.tour'].with_user(anna).consume(TOUR)
        self.assertTrue(self.seen(anna))
        self.assertFalse(self.seen(bert), "one person's tour was recorded for another")

    def test_the_names_agree(self):
        """The registry, the row and the question the Inbox asks: one name."""
        self.assertIn(f'export const INBOX_TOUR = "{TOUR}";', self.js)
        self.assertIn('registry.category("web_tour.tours").add(INBOX_TOUR', self.js)
        self.assertIn('["name", "=", INBOX_TOUR]', self.js)
        manifest = read('__manifest__.py')
        self.assertIn("'web_tour'", manifest)
        self.assertIn('conversation_view/inbox_tour.js', manifest)

    def test_the_sentences(self):
        """Four, as checked against the code, in plain words."""
        for sentence in SENTENCES:
            self.assertIn(sentence, self.js)
        self.assertNotIn('—', self.js)

    def test_every_step_moves_on_a_click(self):
        """19.0's interactive tour skips a step that has nothing to run.

        So every step says `run: "click"`, or the reader would never see it.
        """
        steps = self.steps()
        self.assertEqual(len(steps), 10, "the steps changed: update this count with them")
        for step in steps:
            self.assertIn('run: "click"', step)

    def test_no_steps_off_the_inbox(self):
        """A tour `tour_service` starts before the Inbox is on screen waits.

        It resumes the running tour on every page load for somebody with
        Onboarding on, before any action is mounted. Every step's `isActive`
        fails there, and a tour that skips every step counts as done: the
        first browser run found it consumed and never shown. No steps makes
        `tour_service` leave it current; the Inbox starts it once mounted.
        """
        body = self.js.split('function steps() {', 1)[1]
        self.assertTrue(body.lstrip().startswith(
            'if (!document.querySelector(".o_mailpro_inbox")) {\n        return [];'))
        offer = self.js.split('async function offer()', 1)[1]
        self.assertIn('tourState.getCurrentTour() === INBOX_TOUR', offer)

    def test_linking_is_only_taught_to_who_may_link(self):
        """`link_to` is for mailbox managers, and so is the step about it."""
        taught = [step for step in self.steps() if 'content: link,' in step]
        self.assertEqual(len(taught), 2, "a monitor and a phone each teach linking once")
        for step in taught:
            self.assertIn('CAN_LINK', step)
        view = read('static', 'src', 'js', 'conversation_view', 'conversation_view.js')
        self.assertIn('user.hasGroup("pan_mail_pro.group_mail_mailbox_manager")', view)
        self.assertIn('o_mailpro_can_link: this.state.canLink', view)
        self.assertIn("has_group('pan_mail_pro.group_mail_mailbox_manager')",
                      read('models', 'pan_mail_routing_log.py'))

    def test_the_question_mark(self):
        """In the top bar, beside the search, on a phone too."""
        template = read('static', 'src', 'xml', 'conversation_view.xml')
        bar = template.split('o_mailpro_search_zone', 1)[1].split('o_mailpro_topbar_end', 1)[0]
        self.assertIn('o_mailpro_tour_start', bar)
        self.assertIn('this.tour.start()', bar)
