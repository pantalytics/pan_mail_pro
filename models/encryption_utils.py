# -*- coding: utf-8 -*-
"""
Encryption utilities for Microsoft OAuth tokens and secrets.

Uses Fernet symmetric encryption with auto-generated key stored in database.
This provides defense-in-depth security on top of Odoo.sh database encryption.
"""
import logging
import os
from cryptography.fernet import Fernet
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# System parameter for auto-generated encryption key.
# The name still says outlook: renaming it would orphan every stored token,
# because the parameter *is* the key. It is deliberately left alone.
AUTO_KEY_PARAM = 'x_pan_outlook_pro.encryption_key'

# Optional deployment-level override, read before the database.
ENV_KEY_VAR = 'PAN_MAIL_ENCRYPTION_KEY'


def get_encryption_key(env):
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

    Args:
        env: Odoo environment

    Returns:
        bytes: Encryption key
    """
    env_key = os.environ.get(ENV_KEY_VAR)
    if env_key:
        return env_key.encode('utf-8')

    IrConfigParameter = env['ir.config_parameter'].sudo()
    key = IrConfigParameter.get_param(AUTO_KEY_PARAM)

    if not key:
        # Generate new key on first use
        key = Fernet.generate_key().decode('utf-8')
        IrConfigParameter.set_param(AUTO_KEY_PARAM, key)
        _logger.info(
            "[Encryption] Generated new encryption key for Microsoft OAuth tokens. "
            "Tokens will be stored encrypted in database."
        )

    return key.encode('utf-8')


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

    Raises:
        UserError: If decryption fails (wrong key or corrupted data)
    """
    if not encrypted_text:
        return False

    try:
        key = get_encryption_key(env)
        fernet = Fernet(key)
        decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception as e:
        _logger.error(f"[Encryption] Failed to decrypt value: {e}")
        raise UserError(
            "Failed to decrypt sensitive data. The encryption key may have changed or data is corrupted. "
            "Please reconnect your Microsoft account."
        )
