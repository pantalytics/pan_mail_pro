# -*- coding: utf-8 -*-
# The Pantalytics platform's pairing start, for the UI check.
#
# The Inbox's gate connects by itself (pan_mail_pro#380): Odoo's server asks
# the platform for a pairing, and the browser opens the approval page it names.
# The browser half is stood in at the network by `tools/ui_check.py`
# (`page.route`); the server half is a call from inside the Odoo container,
# which no browser route can reach. This addon answers it while the check
# says so (`pan_mail_pro_ci.platform_stub`), and lets nothing else leave.
#
# Installed by tools/ui_preview.sh only. Never shipped, never a dependency.
{
    'name': 'Mail Pro CI: platform stand-in',
    'version': '19.0.1.0.0',
    'depends': ['pan_mail_pro'],
    'author': 'Pantalytics',
    'license': 'LGPL-3',
    'installable': True,
}
