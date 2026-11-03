# The Python differences between the Odoo series this module runs on
# (docs/plans/multi-version.md). Every name here is one thing that is spelled
# differently on one series, resolved once at import time against what the
# running Odoo actually has, never against a version number. A model file
# imports the name from here and reads the same on every branch.
import inspect

from odoo import api, models
from odoo.addons.base.models import ir_config_parameter as _icp
from odoo.addons.mail.models.mail_mail import MailMail
from odoo.addons.mail.models.mail_thread import MailThread

# 19 and 20 hand _postprocess_sent_message the delivered addresses as well as
# the partners; 18 only knows the partners.
_POSTPROCESS_TAKES_EMAILS = (
    "success_emails" in inspect.signature(MailMail._postprocess_sent_message).parameters
)


def postprocess_sent_message(mail, success_pids, success_emails, **kwargs):
    """mail.mail._postprocess_sent_message with the arguments this Odoo accepts."""
    if _POSTPROCESS_TAKES_EMAILS:
        return mail._postprocess_sent_message(
            success_pids=success_pids, success_emails=success_emails, **kwargs
        )
    return mail._postprocess_sent_message(success_pids=success_pids, **kwargs)


def notification_status(mail):
    """The mail.notification status that matches a mail.mail state.

    19 and 20 answer it themselves; 18 has no such method, so its table is
    here, copied from 20's _get_notification_status.
    """
    if hasattr(MailMail, "_get_notification_status"):
        return mail._get_notification_status()
    return {
        "outgoing": "ready",
        "sent": "sent",
        "received": "sent",
        "exception": "exception",
        "cancel": "canceled",
    }.get(mail.state, "ready")


# The definition class is IrConfigParameter on 18 and 19, IrConfig_Parameter
# on 20, so the model is found by what it is rather than by its class name.
_CONFIG_PARAMETER_HAS_GET_PARAM = any(
    hasattr(cls, "get_param") for cls in vars(_icp).values() if isinstance(cls, type)
)

if not _CONFIG_PARAMETER_HAS_GET_PARAM:
    # 20 replaced get_param/set_param with typed getters and setters
    # (get_str, set_str, ...). The module reads and writes its parameters
    # through the old pair in 86 places; until those move to the typed
    # calls on every branch, 20 gets the pair back with the 19 semantics:
    # a string or the default, and a falsy value removes the row.
    class IrConfigParameterParams(models.Model):
        _inherit = "ir.config_parameter"

        @api.model
        def get_param(self, key, default=False):
            return self.get_str(key, default=default)

        @api.model
        def set_param(self, key, value):
            if value is False or value is None:
                old = self.get_str(key, default=False)
                self.sudo().search([("key", "=", key)]).unlink()
                return old
            return self.set_str(key, value)


def partner_ids_from_emails(record, emails):
    """The partners for these addresses, created when missing, as ids.

    19 and 20: _partner_find_from_emails_single. 18: _mail_find_partner_from_emails,
    which answers one partner (or an empty one) per address.
    """
    if hasattr(MailThread, "_partner_find_from_emails_single"):
        return record._partner_find_from_emails_single(emails).ids
    found = record._mail_find_partner_from_emails(emails, force_create=True)
    return [partner.id for partner in found if partner]


try:
    # 19 and 20: a domain is an object with AND and OR on it.
    from odoo.fields import Domain
except ImportError:
    # 18 has lists and odoo.osv.expression. The module only builds domains
    # (Domain(list), Domain.AND, Domain.OR) and hands them to search, so a
    # list that knows the two combinators is the whole of what it uses.
    from odoo.osv import expression

    class Domain(list):
        def __init__(self, domain):
            # 19 and 20 refuse anything but a list of conditions with a
            # TypeError, which is what a domain that arrived over RPC relies on.
            if not isinstance(domain, (list, tuple)):
                raise TypeError(f"a domain is a list, not {type(domain).__name__}")
            super().__init__(domain)

        @classmethod
        def AND(cls, domains):
            return cls(expression.AND([list(d) for d in domains]))

        @classmethod
        def OR(cls, domains):
            return cls(expression.OR([list(d) for d in domains]))


try:
    # 19 and 20: a table object declared as a class attribute.
    Constraint = models.Constraint
except AttributeError:
    # 18 has `_sql_constraints = [(name, definition, message)]` on the class
    # and nothing else. The attribute form is kept by appending to that list
    # when the class is built: __set_name__ runs from type.__new__, after the
    # class object exists, and 18 reads `_sql_constraints` off the class
    # __dict__ only when the registry is built, which is later still.
    class Constraint:
        def __init__(self, definition, message=None):
            self.definition = definition
            self.message = message or ""

        def __set_name__(self, owner, name):
            assert name.startswith("_"), "Names of SQL objects in a model must start with _"
            owner._sql_constraints = [
                *owner.__dict__.get("_sql_constraints", []),
                (name[1:], self.definition, self.message),
            ]
