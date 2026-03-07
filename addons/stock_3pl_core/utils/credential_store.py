# addons/stock_3pl_core/utils/credential_store.py
"""Fernet-based symmetric encryption for connector credential fields.

Master key is stored in ir.config_parameter under key
'stock_3pl_core.credential_key'. On first use, a new key is generated
and stored. The same key must persist across restarts.

Encrypted values are stored as base64-encoded Fernet tokens prefixed
with 'enc:' to distinguish them from legacy plaintext values.

Usage:
    from odoo.addons.stock_3pl_core.utils.credential_store import encrypt_credential, decrypt_credential

    ciphertext = encrypt_credential(env, plaintext)
    plaintext  = decrypt_credential(env, ciphertext)
"""
import logging
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_PARAM_KEY = 'stock_3pl_core.credential_key'
_PREFIX = 'enc:'


def _get_or_create_key(env) -> bytes:
    """Return the Fernet master key bytes, generating and persisting one if absent.

    The key is stored in ir.config_parameter as a base64 string (the native
    format returned by Fernet.generate_key()). .sudo() is used so that the
    key can be read/written regardless of the current user's access rights.
    """
    from cryptography.fernet import Fernet

    IrParam = env['ir.config_parameter'].sudo()
    stored = IrParam.get_param(_PARAM_KEY)
    if stored:
        return stored.encode()

    new_key = Fernet.generate_key()          # bytes, already base64-encoded
    IrParam.set_param(_PARAM_KEY, new_key.decode())
    _logger.warning('stock_3pl_core: generated new Fernet credential encryption key ...')
    return new_key


def encrypt_credential(env, value: str) -> str:
    """Encrypt *value* and return an 'enc:'-prefixed Fernet token string.

    - If *value* is falsy, it is returned unchanged.
    - If *value* already starts with 'enc:', it is returned unchanged (idempotent).
    - On any Fernet error, the raw value is returned and the error is logged.
    """
    if not value:
        return value
    if value.startswith(_PREFIX):
        return value

    try:
        from cryptography.fernet import Fernet
        key = _get_or_create_key(env)
        f = Fernet(key)
        token = f.encrypt(value.encode('utf-8'))
        return _PREFIX + token.decode()
    except Exception as exc:
        _logger.error('encrypt_credential: Fernet encryption failed: %s', exc)
        raise UserError(
            'Credential encryption failed. Ensure the "cryptography" package is installed '
            'and the encryption key in ir.config_parameter is valid. '
            f'Error: {str(exc)[:200]}'
        )


def decrypt_credential(env, value: str) -> str:
    """Decrypt an 'enc:'-prefixed Fernet token and return the plaintext.

    - If *value* is falsy or does not start with 'enc:', it is returned
      unchanged (handles legacy plaintext stored before encryption was enabled).
    - On any Fernet error (bad key, corrupted token), the raw value is
      returned and the error is logged — never raises to the caller.
    """
    if not value:
        return value
    if not value.startswith(_PREFIX):
        _logger.warning(
            'decrypt_credential: credential is stored as plaintext — '
            're-save the connector to encrypt it.'
        )
        return value

    try:
        from cryptography.fernet import Fernet
        key = _get_or_create_key(env)
        f = Fernet(key)
        token = value[len(_PREFIX):].encode()
        return f.decrypt(token).decode('utf-8')
    except Exception as exc:
        _logger.error(
            'decrypt_credential: failed to decrypt — key may have changed. '
            'Re-enter credentials on the connector. Error: %s', exc
        )
        return ''
