# -*- coding: utf-8 -*-
# Dark mode for the UI check, on the community image CI runs.
#
# Odoo's dark mode is Enterprise: community already loads `web.assets_web_dark`
# for a `color_scheme=dark` cookie, but never answers "dark" and has no dark
# variables to compile it with. This addon supplies both, the way web_enterprise
# does, so `tools/ui_check.py` can look at the Inbox in dark mode. The colours
# are a stand-in for Enterprise's, not a copy: the check is about Mail Pro
# following the scheme, not about Odoo's exact greys.
#
# Installed by tools/ui_preview.sh only. Never shipped, never a dependency.
{
    'name': 'Mail Pro CI: dark mode',
    'version': '19.0.1.0.0',
    'depends': ['web'],
    'author': 'Pantalytics',
    'license': 'LGPL-3',
    'installable': True,
    'assets': {
        'web.assets_web_dark': [
            ('before', 'web/static/src/scss/primary_variables.scss',
             'pan_mail_pro_ci_dark/static/src/scss/primary_variables.dark.scss'),
        ],
    },
}
