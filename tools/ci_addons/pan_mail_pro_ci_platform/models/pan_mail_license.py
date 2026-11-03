import json

from odoo import models

# The approval page's address, set by the UI check for as long as it needs
# the stand-in. Unset, every call goes where it always goes.
STUB_PARAM = 'pan_mail_pro_ci.platform_stub'
# What the last pairing start said, for the check to read back: on a copy it
# says `staging` (pan_mail_pro#384), which no browser route can see.
START_PARAM = 'pan_mail_pro_ci.platform_stub_start'
CODE = 'UICK-0380'


class PanMailLicense(models.Model):
    _inherit = 'pan.mail.license'

    def _post(self, path, payload, key=None):
        page = self.env['ir.config_parameter'].sudo().get_param(STUB_PARAM)
        if not page:
            return super()._post(path, payload, key=key)
        if path == '/api/v1/link/start':
            self.env['ir.config_parameter'].sudo().set_param(
                START_PARAM, json.dumps(payload, sort_keys=True))
            return 200, {
                'status': 'started', 'user_code': CODE,
                'device_token': 'ui-check-device', 'verify_url': page,
                'verify_url_complete': f'{page}?code={CODE}', 'expires_at': '',
            }
        # The poll and the heartbeat: the check stands their outcome in on
        # the row, and a real call from CI would only be refused.
        return 503, {}
