# -*- coding: utf-8 -*-
"""
Attachment-handling tests across attachment sizes and inline images.

Verifies the Graph API client picks the right upload strategy:
- < 3MB → POST /messages/{id}/attachments (direct)
- ≥ 3MB → POST /createUploadSession + PUT chunks
- Inline images (cid:) → bundled with isInline=true
"""
import base64
from unittest.mock import patch

from odoo.tests import tagged

from .common import OutlookProTestCase
from odoo.addons.pan_outlook_pro.models.providers.microsoft import graph_client as graph_mod


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestAttachments(OutlookProTestCase):

    def _make_mail_with_attachments(self, attachment_ids=None, body_html='<p>Body</p>'):
        return self.env['mail.mail'].create({
            'subject': 'Test',
            'body_html': body_html,
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
            'x_microsoft_mailbox_id': self.shared_mailbox.id,
            'attachment_ids': [(6, 0, attachment_ids or [])],
        })

    def test_no_attachments_no_endpoint_call(self):
        mail = self._make_mail_with_attachments()
        with self.mock_graph() as calls:
            mail.send(raise_exception=True)
        self.assertEqual(calls['attach'], [])
        self.assertEqual(calls['upload_session'], [])

    def test_small_attachment_uses_direct_endpoint(self):
        att = self.make_attachment(name='small.pdf', size_bytes=1024)
        mail = self._make_mail_with_attachments([att.id])
        with self.mock_graph() as calls:
            mail.send(raise_exception=True)
        self.assertEqual(len(calls['attach']), 1)
        self.assertEqual(calls['attach'][0]['name'], 'small.pdf')
        self.assertEqual(calls['upload_session'], [])

    def test_large_attachment_uses_upload_session(self):
        # Use a small threshold via patch to keep the test fast — we don't
        # want to actually allocate a 3MB+ buffer.
        att = self.make_attachment(name='big.pdf', size_bytes=2048)
        mail = self._make_mail_with_attachments([att.id])
        with patch.object(graph_mod, 'DIRECT_ATTACHMENT_LIMIT', 1024):
            with self.mock_graph() as calls:
                mail.send(raise_exception=True)
        self.assertEqual(calls['attach'], [],
                         "large attachment must NOT use direct endpoint")
        self.assertEqual(len(calls['upload_session']), 1,
                         "large attachment must trigger createUploadSession")
        self.assertGreaterEqual(len(calls['upload_chunks']), 1,
                                "must PUT at least one chunk")

    def test_mixed_size_attachments(self):
        small = self.make_attachment(name='s.pdf', size_bytes=512)
        large = self.make_attachment(name='l.pdf', size_bytes=4096)
        mail = self._make_mail_with_attachments([small.id, large.id])
        with patch.object(graph_mod, 'DIRECT_ATTACHMENT_LIMIT', 1024):
            with self.mock_graph() as calls:
                mail.send(raise_exception=True)
        names_direct = [a['name'] for a in calls['attach']]
        self.assertEqual(names_direct, ['s.pdf'])
        self.assertEqual(len(calls['upload_session']), 1)
        self.assertEqual(calls['upload_session'][0]['payload']['AttachmentItem']['name'], 'l.pdf')

    def test_inline_image_marked_as_inline(self):
        """Body containing a /web/image/ URL should produce an inline
        attachment with isInline=true. We simulate by attaching an image
        and referencing it directly via cid:."""
        # Build an attachment that the Graph client will recognize as inline.
        png_bytes = (
            b'\x89PNG\r\n\x1a\n'
            b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
            b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff'
            b'\xff?\x03\x00\x06\x05\x02\x03~\x97\x9d\x9c\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        image = self.env['ir.attachment'].create({
            'name': 'logo.png',
            'datas': base64.b64encode(png_bytes),
            'mimetype': 'image/png',
            'res_model': 'mail.mail',
        })
        body = f'<p>Hi <img src="/web/image/{image.id}"/></p>'
        mail = self._make_mail_with_attachments([], body_html=body)
        # Add the image to attachment_ids manually so _prepare_inline_images can
        # match it (some Odoo flows do this automatically; we want determinism).
        mail.attachment_ids = [(6, 0, [image.id])]

        with self.mock_graph() as calls:
            mail.send(raise_exception=True)

        # Direct path because PNG is tiny
        self.assertEqual(len(calls['attach']), 1)
        self.assertTrue(
            calls['attach'][0].get('isInline'),
            f"inline image must have isInline=true, got: {calls['attach'][0]}",
        )
        # And the body must reference cid: instead of /web/image/
        self.assertIn('cid:', calls['draft']['body']['content'])
        self.assertNotIn('/web/image/', calls['draft']['body']['content'])
