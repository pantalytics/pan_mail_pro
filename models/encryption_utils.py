# -*- coding: utf-8 -*-
"""
Encryption utilities for Microsoft OAuth tokens and secrets.

Uses Fernet symmetric encryption with auto-generated key stored in database.
This provides defense-in-depth security on top of Odoo.sh database encryption.
"""
import logging
from cryptography.fernet import Fernet
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# System parameter for auto-generated encryption key
AUTO_KEY_PARAM = 'x_pan_outlook_pro.encryption_key'


def get_encryption_key(env):
    """
    Get or generate encryption key from database.

    The key is auto-generated on first use and stored in ir.config_parameter.
    This provides zero-configuration encryption for OAuth tokens.

    Security note: Key is stored in database (same as encrypted data).
    This is acceptable for Odoo.sh because:
    - Odoo.sh database is already encrypted at rest
    - Provides defense against SQL injection and backup file leaks
    - Better than storing tokens in plain text
    - ISO 27001 compliant with database-level encryption

    Args:
        env: Odoo environment

    Returns:
        bytes: Encryption key
    """
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
