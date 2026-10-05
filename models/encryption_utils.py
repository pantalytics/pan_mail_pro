# -*- coding: utf-8 -*-
"""
Encryption utilities for OAuth tokens, mailbox passwords and client secrets.

Uses Fernet symmetric encryption with auto-generated key stored in database.
This provides defense-in-depth security on top of Odoo.sh database encryption.
"""
import logging
import os
from cryptography.fernet import Fernet, InvalidToken
from odoo.exceptions import UserError

from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

# System parameter for the auto-generated encryption key.
AUTO_KEY_PARAM = 'pan_mail_pro.encryption_key'
# Where the key lived before 19.0.6.0.0. The migration renames the parameter;
# this fallback is insurance against code that runs ahead of it (a deploy that
# forgot the version bump), because generating a fresh key there would orphan
# every stored credential at once.
LEGACY_KEY_PARAM = 'x_pan_outlook_pro.encryption_key'

# Optional deployment-level override, read before the database.
ENV_KEY_VAR = 'PAN_MAIL_ENCRYPTION_KEY'


def _stored_key(env):
    """The key in ir.config_parameter, under its current name or the one it
    had before 19.0.6.0.0, which is adopted on the way. None when there is
    none; this never mints one."""
    IrConfigParameter = env['ir.config_parameter'].sudo()
    key = IrConfigParameter.get_param(AUTO_KEY_PARAM)
    if not key:
        key = IrConfigParameter.get_param(LEGACY_KEY_PARAM)
        if key:
            IrConfigParameter.set_param(AUTO_KEY_PARAM, key)
            IrConfigParameter.set_param(LEGACY_KEY_PARAM, False)
            _logger.info("[Encryption] Adopted the encryption key from its pre-19.0.6.0.0 name")
    return key or None


def get_encryption_key(env, generate=True):
    """
    Get the encryption key: environment first, then database.

    When PAN_MAIL_ENCRYPTION_KEY is set it wins and nothing is written to
    ir.config_parameter. That is the only configuration in which a database
    dump does not also contain the key that decrypts its tokens.

    Otherwise the key is generated on first use and stored in the database
    alongside the ciphertext it protects. That is zero-configuration and
    defends against SQL injection and stolen table extracts, but it does NOT
    defend against a stolen backup: whoever holds the dump holds both halves.
    Say so plainly to customers rather than implying otherwise.

    "First use" means the first *encrypt*. A reader that finds no key passes
    `generate=False` and gets None, because minting a key to decrypt with is
    the one thing that can never be right: whatever is stored was encrypted
    under a key that is gone, and the fresh one quietly orphans all of it
    while making the next encrypt look fine.

    Args:
        env: Odoo environment
        generate: mint and store a key when the database holds none

    Returns:
        bytes: Encryption key, or None when there is none and `generate` is off
    """
    env_key = os.environ.get(ENV_KEY_VAR)
    if env_key:
        return env_key.encode('utf-8')

    key = _stored_key(env)
    if not key:
        if not generate:
            return None
        key = Fernet.generate_key().decode('utf-8')
        env['ir.config_parameter'].sudo().set_param(AUTO_KEY_PARAM, key)
        _logger.info(
            "[Encryption] Generated a new encryption key. Credentials are stored "
            "encrypted in the database."
        )

    return key.encode('utf-8')


def _key_source():
    """Where the key in use came from, for the sentence that says it is wrong."""
    if os.environ.get(ENV_KEY_VAR):
        return f"the {ENV_KEY_VAR} environment variable"
    return f"the {AUTO_KEY_PARAM} system parameter"


def encrypt_value(env, plaintext):
    """
    Encrypt a plaintext value using Fernet symmetric encryption.

    Args:
        env: Odoo environment
        plaintext (str): String to encrypt

    Returns:
        str: Encrypted string (base64 encoded), or False if plaintext is empty

    Raises:
        UserError: If encryption fails
    """
    if not plaintext:
        return False

    try:
        key = get_encryption_key(env)
        fernet = Fernet(key)
        encrypted = fernet.encrypt(plaintext.encode('utf-8'))
        return encrypted.decode('utf-8')
    except Exception as e:
        _logger.error(f"[Encryption] Failed to encrypt value: {e}")
        raise UserError("Failed to encrypt sensitive data. Please contact your administrator.")


def decrypt_value(env, encrypted_text):
    """
    Decrypt an encrypted value using Fernet symmetric encryption.

    Args:
        env: Odoo environment
        encrypted_text (str): Encrypted string (base64 encoded)

    Returns:
        str: Decrypted plaintext string, or False if encrypted_text is empty
             or the database is neutralized

    Raises:
        UserError: when there is no key at all, when the key is not a Fernet
            key, or when it is not the key this value was encrypted under.
            Three different sentences, because they are three different
            repairs: set the key back, fix its format, or reconnect.
    """
    if not encrypted_text:
        return False

    # The module's off switch, and the reason it is here rather than at each
    # call site: every credential Mail Pro owns is read through this one
    # function -- OAuth access and refresh tokens, IMAP/SMTP passwords, both
    # providers' client secrets. A neutralized database therefore cannot reach
    # a provider at all, including through call sites nobody has written yet.
    #
    # Empty rather than an exception: "no credentials" is a state every caller
    # already handles, and it leaves the account form readable in staging. The
    # sentence that says *neutralized* is raised earlier, by the callers that
    # know what they were trying to do. See models/neutralization.py.
    if database_is_neutralized(env):
        _logger.info("[Encryption] Database is neutralized - refusing to decrypt")
        return False

    # Never generate here. There is ciphertext, so a key existed; a database
    # with none now has lost it (a parameter deleted by hand, an environment
    # variable that did not follow the move to a new server), and a fresh key
    # would not read a byte of it while making every stored credential look
    # merely corrupt. Say what is missing and where it is expected instead.
    key = get_encryption_key(env, generate=False)
    if key is None:
        _logger.error("[Encryption] No encryption key: nothing stored can be decrypted")
        raise UserError(
            "There is no encryption key to read the stored credentials with. The key "
            f"was lost: set it back in the {ENV_KEY_VAR} environment variable or the "
            f"{AUTO_KEY_PARAM} system parameter, or reconnect every email account."
        )

    try:
        fernet = Fernet(key)
    except (ValueError, TypeError) as error:
        # Fernet refuses anything but 32 url-safe base64 bytes before it
        # looks at the data: this is the key's format, not the data's.
        _logger.error("[Encryption] The encryption key is not a Fernet key: %s", error)
        raise UserError(
            f"The encryption key in {_key_source()} is not a valid Fernet key "
            "(32 url-safe base64-encoded bytes). Fix the key; the stored credentials "
            "are intact."
        ) from error

    try:
        decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken as error:
        _logger.error("[Encryption] Failed to decrypt value: the key does not fit the data")
        raise UserError(
            "Failed to decrypt sensitive data. The encryption key may have changed or "
            "the data is corrupted. Please reconnect the email account."
        ) from error
