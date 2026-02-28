# addons/stock_3pl_core/tests/test_credential_store.py
"""Pure-Python unit tests for the Fernet credential store.

These tests do NOT require a live Odoo runtime.  ir.config_parameter is
mocked with a simple dict-backed MagicMock.  The cryptography package is
required; the tests are skipped if it is not installed.
"""
import base64
import sys
import types
import pathlib
import importlib.util
import unittest
from unittest.mock import MagicMock, patch

# Skip the entire module if cryptography is not available.
cryptography = pytest_skip = None
try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    import pytest
    pytestmark = pytest.mark.skip(reason='cryptography package not installed')
    Fernet = None

# ---------------------------------------------------------------------------
# Load credential_store.py from disk so the test is independent of any
# Odoo import machinery.
# ---------------------------------------------------------------------------
_UTILS_DIR = (
    pathlib.Path(__file__).parent.parent / 'utils' / 'credential_store.py'
)


def _load_module():
    full_name = '_test_credential_store'
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(full_name, _UTILS_DIR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_cs = _load_module()
encrypt_credential = _cs.encrypt_credential
decrypt_credential = _cs.decrypt_credential
_get_or_create_key = _cs._get_or_create_key
_PREFIX = _cs._PREFIX
_PARAM_KEY = _cs._PARAM_KEY


# ---------------------------------------------------------------------------
# Helper — build a minimal env mock backed by an in-memory dict store
# ---------------------------------------------------------------------------

def _make_env(initial_params=None):
    """Return a MagicMock env where ir.config_parameter is dict-backed."""
    store = dict(initial_params or {})

    param_obj = MagicMock()
    param_obj.get_param.side_effect = lambda key, default=False: store.get(key, default)
    param_obj.set_param.side_effect = lambda key, value: store.update({key: value})

    # env['ir.config_parameter'].sudo() → param_obj
    param_obj_sudo = MagicMock()
    param_obj_sudo.get_param.side_effect = param_obj.get_param
    param_obj_sudo.set_param.side_effect = param_obj.set_param
    param_obj.sudo.return_value = param_obj_sudo

    env = MagicMock()
    env.__getitem__.side_effect = lambda key: param_obj if key == 'ir.config_parameter' else MagicMock()

    # Expose the underlying store for assertions
    env._store = store
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetOrCreateKey(unittest.TestCase):

    def test_get_or_create_key_creates_if_absent(self):
        """A new Fernet key is generated and stored when none exists."""
        env = _make_env()
        key = _get_or_create_key(env)
        # Must be a valid Fernet key (32 url-safe base64-encoded bytes → 44 chars)
        self.assertIsInstance(key, bytes)
        decoded = base64.urlsafe_b64decode(key)
        self.assertEqual(len(decoded), 32)
        # Must have been persisted in the store
        self.assertIn(_PARAM_KEY, env._store)

    def test_get_or_create_key_reuses_existing(self):
        """If a key is already in ir.config_parameter, it is returned as-is."""
        existing_key = Fernet.generate_key()
        env = _make_env({_PARAM_KEY: existing_key.decode()})
        key = _get_or_create_key(env)
        self.assertEqual(key, existing_key)

    def test_get_or_create_key_stable_across_calls(self):
        """Calling _get_or_create_key twice with the same env returns the same key."""
        env = _make_env()
        key1 = _get_or_create_key(env)
        key2 = _get_or_create_key(env)
        self.assertEqual(key1, key2)


class TestEncryptCredential(unittest.TestCase):

    def test_encrypt_returns_enc_prefixed_string(self):
        """encrypt_credential returns a string prefixed with 'enc:'."""
        env = _make_env()
        result = encrypt_credential(env, 'my_secret_password')
        self.assertTrue(result.startswith(_PREFIX), f'Expected enc: prefix, got: {result}')

    def test_encrypt_result_is_valid_fernet_token(self):
        """The part after 'enc:' is a valid Fernet token decryptable with the same key."""
        env = _make_env()
        result = encrypt_credential(env, 'hello_world')
        token_part = result[len(_PREFIX):]
        key = _get_or_create_key(env)
        f = Fernet(key)
        plaintext = f.decrypt(token_part.encode()).decode()
        self.assertEqual(plaintext, 'hello_world')

    def test_encrypt_idempotent_if_already_encrypted(self):
        """A value already starting with 'enc:' is returned unchanged."""
        env = _make_env()
        first = encrypt_credential(env, 'original_secret')
        second = encrypt_credential(env, first)
        self.assertEqual(first, second)

    def test_encrypt_empty_passthrough(self):
        """Empty string and None are returned unchanged without encryption."""
        env = _make_env()
        self.assertEqual(encrypt_credential(env, ''), '')
        self.assertIsNone(encrypt_credential(env, None))

    def test_encrypt_falsy_string_passthrough(self):
        """Falsy values (empty bytes-equivalent) are passed through."""
        env = _make_env()
        self.assertFalse(encrypt_credential(env, ''))


class TestDecryptCredential(unittest.TestCase):

    def test_decrypt_recovers_plaintext(self):
        """decrypt_credential returns the original plaintext after encrypt_credential."""
        env = _make_env()
        plaintext = 'super_secret_value_123'
        ciphertext = encrypt_credential(env, plaintext)
        recovered = decrypt_credential(env, ciphertext)
        self.assertEqual(recovered, plaintext)

    def test_decrypt_passthrough_for_plaintext_legacy(self):
        """A string without the 'enc:' prefix is returned as-is (legacy plaintext)."""
        env = _make_env()
        legacy = 'old_plaintext_password'
        result = decrypt_credential(env, legacy)
        self.assertEqual(result, legacy)

    def test_decrypt_empty_passthrough(self):
        """Empty string and None are returned unchanged without attempting decryption."""
        env = _make_env()
        self.assertEqual(decrypt_credential(env, ''), '')
        self.assertIsNone(decrypt_credential(env, None))

    def test_decrypt_bad_token_returns_raw_value_without_raising(self):
        """A corrupted 'enc:' value logs an error but does NOT raise — returns raw."""
        env = _make_env()
        corrupt = _PREFIX + 'this_is_not_a_valid_fernet_token=='
        # Must not raise
        result = decrypt_credential(env, corrupt)
        # Returns the raw (corrupt) value
        self.assertEqual(result, corrupt)

    def test_decrypt_with_wrong_key_returns_raw_without_raising(self):
        """Decrypting with a mismatched key returns the raw value without raising."""
        env_a = _make_env()
        env_b = _make_env()   # different env → different key generated

        ciphertext = encrypt_credential(env_a, 'secret_for_env_a')
        # Attempt to decrypt with env_b's key (wrong key)
        result = decrypt_credential(env_b, ciphertext)
        # Should not raise; raw value is returned
        self.assertEqual(result, ciphertext)

    def test_roundtrip_preserves_special_characters(self):
        """Special characters, unicode, and symbols survive the encrypt/decrypt cycle."""
        env = _make_env()
        special = 'p@ssw0rd!#$%^&*()_+-=[]{}|;:,.<>?/~`\u00e9\u4e2d\u6587'
        ciphertext = encrypt_credential(env, special)
        recovered = decrypt_credential(env, ciphertext)
        self.assertEqual(recovered, special)

    def test_each_encrypt_produces_unique_ciphertext(self):
        """Fernet uses random IV so encrypting the same value twice is non-deterministic."""
        env = _make_env()
        c1 = encrypt_credential(env, 'same_value')
        c2 = encrypt_credential(env, 'same_value')
        # Both must decrypt correctly
        self.assertEqual(decrypt_credential(env, c1), 'same_value')
        self.assertEqual(decrypt_credential(env, c2), 'same_value')
        # But the ciphertexts themselves differ (probabilistic — almost certain)
        self.assertNotEqual(c1, c2)


if __name__ == '__main__':
    unittest.main()
