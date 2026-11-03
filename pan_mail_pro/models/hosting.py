# -*- coding: utf-8 -*-
"""
Where this Odoo runs, read from inside it (pantalytics/odoo-mcp-pro-admin#598).

The platform keeps one `hosting` per Odoo instance and trusts three sources:
what the paired app reports, what it can see from outside when the instance
is added, and what the user corrects. The first is the only one that knows,
because it is the only one standing on the machine. So the heartbeat carries
`hosting()`, one of three words, and nothing else about the host: no host
name, no path, no stage name.

- **`sh`**: Odoo.sh sets `ODOO_STAGE` (`production`, `staging` or `dev`) in
  the environment of every branch. Measured 2026-11-03 in the shell of an
  Odoo.sh production branch: `ODOO_STAGE=production`, `ODOO_VERSION=19.0`.
- **`cloudpepper`**: no environment variable of its own has been seen.
  Its config file is at `/var/odoo/<name>/odoo.conf` (mailpro-dev's log), so
  the path of the config file in use is the sign.
- **`self_hosted`**: everything else, including a laptop and a Docker image.

Nothing is probed: no request to the host, no file read. The platform takes no
other value (a 400), so `hosting()` can return no other.

The same variable says whether an Odoo.sh database is a copy: a `staging` or
`dev` branch is one, whatever the neutralization left set
(`host_says_copy`, read by `neutralization.database_is_copy`).
"""
import os
import re

from odoo import tools

HOSTING_VALUES = ('sh', 'cloudpepper', 'self_hosted')

# The Odoo.sh stages that run on a copy of production's database.
COPY_STAGES = ('staging', 'dev')

CLOUDPEPPER_CONFIG = re.compile(r'^/var/odoo/[^/]+/odoo\.conf$')


def odoo_sh_stage(environ=None):
    """`ODOO_STAGE` lowercased, or None when this is not Odoo.sh."""
    environ = os.environ if environ is None else environ
    return (environ.get('ODOO_STAGE') or '').strip().lower() or None


def hosting(environ=None, config_path=None):
    """`sh`, `cloudpepper` or `self_hosted`. Never anything else.

    `environ` and `config_path` default to this process's own; the tests pass
    them in."""
    if odoo_sh_stage(environ):
        return 'sh'
    if config_path is None:
        config_path = tools.config.get('config') or ''
    if CLOUDPEPPER_CONFIG.match(str(config_path)):
        return 'cloudpepper'
    return 'self_hosted'


def host_says_copy(environ=None):
    """True on an Odoo.sh staging or dev branch."""
    return odoo_sh_stage(environ) in COPY_STAGES
