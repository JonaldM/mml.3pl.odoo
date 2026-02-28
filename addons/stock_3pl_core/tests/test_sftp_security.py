# addons/stock_3pl_core/tests/test_sftp_security.py
"""
Pure-Python unit tests for SftpTransport._get_client() host key verification.

These tests use unittest.TestCase + MagicMock and do NOT require a live Odoo
runtime or a real SFTP server.  paramiko is patched via sys.modules so that
the lazy `import paramiko` inside _get_client() resolves to our mock.

Strategy
--------
* SftpTransport is loaded directly from disk via importlib (same pattern as
  test_poll_inbound.py) so that conftest stubs are in place before import.
* `sys.modules['paramiko']` is replaced with a MagicMock for each test via
  unittest.mock.patch.dict, isolating tests from each other.
* The connector is a simple MagicMock with the SFTP fields as attributes.
"""
import importlib.util
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Load sftp.py as a standalone module using importlib
# (conftest.py has already installed odoo stubs so the import succeeds).
# ---------------------------------------------------------------------------
_TRANSPORT_DIR = pathlib.Path(__file__).parent.parent / 'transport'


def _load_sftp_module():
    """Load sftp.py fresh; return the module object."""
    full_name = '_test_standalone_sftp'
    # Remove any cached version so we get a clean load each time the helper
    # is called (though in practice it is only called once at module level).
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(
        full_name, _TRANSPORT_DIR / 'sftp.py'
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_sftp_mod = _load_sftp_module()
SftpTransport = _sftp_mod.SftpTransport
_WarnOnNewHostKeyPolicy = _sftp_mod._WarnOnNewHostKeyPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector(sftp_host_key='', name='Test SFTP'):
    """Return a MagicMock that behaves like a ThreePlConnector for SFTP tests."""
    connector = MagicMock()
    connector.name = name
    connector.sftp_host_key = sftp_host_key
    connector.sftp_host = 'sftp.example.com'
    connector.sftp_port = 22
    connector.sftp_username = 'user'
    connector.sftp_password = 'pass'
    connector.sftp_outbound_path = '/out'
    connector.sftp_inbound_path = '/in'
    # get_credential('sftp_password') is called by sftp.py since credentials are
    # stored encrypted at rest.  Wire it up to return the plaintext value.
    connector.get_credential.side_effect = lambda field: getattr(connector, field)
    return connector


def _make_transport(connector):
    return SftpTransport(connector)


def _make_paramiko_mock():
    """Return a MagicMock that mimics the paramiko module surface used by _get_client."""
    pm = MagicMock()

    # SSHClient instance
    ssh_instance = MagicMock()
    sftp_instance = MagicMock()
    ssh_instance.open_sftp.return_value = sftp_instance
    pm.SSHClient.return_value = ssh_instance

    # HostKeys instance
    host_keys_instance = MagicMock()
    pm.HostKeys.return_value = host_keys_instance

    # Policy classes — use real unique objects so isinstance-style checks work
    pm.RejectPolicy = MagicMock(name='RejectPolicy')
    pm.RejectPolicy.return_value = MagicMock(name='RejectPolicyInstance')

    return pm, ssh_instance, sftp_instance, host_keys_instance


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestNoHostKeyUsesWarnPolicy(unittest.TestCase):
    """When sftp_host_key is empty/falsy, _WarnOnNewHostKeyPolicy must be used."""

    def _run_for_value(self, key_value):
        connector = _make_connector(sftp_host_key=key_value)
        transport = _make_transport(connector)
        pm, ssh_instance, sftp_instance, _ = _make_paramiko_mock()

        with patch.dict(sys.modules, {'paramiko': pm}):
            transport._get_client()

        # _WarnOnNewHostKeyPolicy instance was passed to set_missing_host_key_policy
        calls = ssh_instance.set_missing_host_key_policy.call_args_list
        self.assertEqual(len(calls), 1)
        policy_arg = calls[0][0][0]
        self.assertIsInstance(policy_arg, _WarnOnNewHostKeyPolicy)

        # RejectPolicy must NOT have been instantiated
        pm.RejectPolicy.assert_not_called()

        # HostKeys must NOT have been instantiated (no temp file work)
        pm.HostKeys.assert_not_called()

    def test_empty_string_uses_warn_policy(self):
        self._run_for_value('')

    def test_none_uses_warn_policy(self):
        self._run_for_value(None)

    def test_false_uses_warn_policy(self):
        self._run_for_value(False)


class TestHostKeySetUsesRejectPolicy(unittest.TestCase):
    """When sftp_host_key is set, strict mode (RejectPolicy) must be activated."""

    SAMPLE_HOST_KEY = (
        'sftp.example.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7example=='
    )

    def setUp(self):
        self.connector = _make_connector(sftp_host_key=self.SAMPLE_HOST_KEY)
        self.transport = _make_transport(self.connector)
        self.pm, self.ssh_instance, self.sftp_instance, self.host_keys_instance = (
            _make_paramiko_mock()
        )

    def _call_get_client(self):
        with patch.dict(sys.modules, {'paramiko': self.pm}):
            # Also patch tempfile and os.unlink inside the sftp module so we
            # do not actually touch the filesystem during unit tests.
            with patch.object(_sftp_mod.tempfile, 'NamedTemporaryFile') as mock_ntf, \
                 patch.object(_sftp_mod.os, 'unlink') as mock_unlink:

                # Simulate context manager that returns a file-like object
                fake_file = MagicMock()
                fake_file.__enter__ = MagicMock(return_value=fake_file)
                fake_file.__exit__ = MagicMock(return_value=False)
                fake_file.name = '/tmp/fake_known_hosts_abc.known_hosts'
                mock_ntf.return_value = fake_file

                result = self.transport._get_client()
                return result, mock_ntf, mock_unlink, fake_file

    def test_host_keys_object_created(self):
        self._call_get_client()
        self.pm.HostKeys.assert_called_once()

    def test_temp_file_written_with_key_text(self):
        _, mock_ntf, _, fake_file = self._call_get_client()
        mock_ntf.assert_called_once()
        # NamedTemporaryFile called with mode='w' and the known_hosts suffix
        kwargs = mock_ntf.call_args[1]
        self.assertEqual(kwargs['mode'], 'w')
        self.assertIn('known_hosts', kwargs['suffix'])
        # Key text was written to the file object
        fake_file.write.assert_called_once_with(self.SAMPLE_HOST_KEY)

    def test_host_keys_load_called_with_temp_path(self):
        _, _, _, fake_file = self._call_get_client()
        self.host_keys_instance.load.assert_called_once_with(fake_file.name)

    def test_temp_file_deleted_after_load(self):
        _, _, mock_unlink, fake_file = self._call_get_client()
        mock_unlink.assert_called_once_with(fake_file.name)

    def test_reject_policy_set_on_ssh_client(self):
        self._call_get_client()
        # RejectPolicy() was instantiated and passed to set_missing_host_key_policy
        self.pm.RejectPolicy.assert_called_once()
        reject_instance = self.pm.RejectPolicy.return_value
        self.ssh_instance.set_missing_host_key_policy.assert_called_once_with(
            reject_instance
        )

    def test_host_keys_assigned_to_ssh_client(self):
        self._call_get_client()
        self.assertEqual(self.ssh_instance._host_keys, self.host_keys_instance)
        self.assertIsNone(self.ssh_instance._host_keys_filename)

    def test_warn_policy_not_used_in_strict_mode(self):
        self._call_get_client()
        calls = self.ssh_instance.set_missing_host_key_policy.call_args_list
        self.assertEqual(len(calls), 1)
        policy_arg = calls[0][0][0]
        self.assertNotIsInstance(policy_arg, _WarnOnNewHostKeyPolicy)

    def test_ssh_connect_still_called(self):
        self._call_get_client()
        self.ssh_instance.connect.assert_called_once_with(
            hostname='sftp.example.com',
            port=22,
            username='user',
            password='pass',
            timeout=30,
        )


class TestInvalidHostKeyRaisesValidationError(unittest.TestCase):
    """When sftp_host_key text cannot be loaded, ValidationError must be raised."""

    BAD_HOST_KEY = 'this is not valid known_hosts content !!!'

    def test_invalid_host_key_raises_validation_error(self):
        from odoo.exceptions import ValidationError

        connector = _make_connector(sftp_host_key=self.BAD_HOST_KEY, name='BadConn')
        transport = _make_transport(connector)
        pm, ssh_instance, _, host_keys_instance = _make_paramiko_mock()

        # Make host_keys.load() raise a generic error to simulate a parse failure
        host_keys_instance.load.side_effect = Exception('Malformed entry on line 1')

        with patch.dict(sys.modules, {'paramiko': pm}):
            with patch.object(_sftp_mod.tempfile, 'NamedTemporaryFile') as mock_ntf, \
                 patch.object(_sftp_mod.os, 'unlink'):

                fake_file = MagicMock()
                fake_file.__enter__ = MagicMock(return_value=fake_file)
                fake_file.__exit__ = MagicMock(return_value=False)
                fake_file.name = '/tmp/fake_bad.known_hosts'
                mock_ntf.return_value = fake_file

                with self.assertRaises(ValidationError) as ctx:
                    transport._get_client()

        msg = str(ctx.exception)
        self.assertIn('BadConn', msg)
        self.assertIn('sftp_host_key', msg)
        self.assertIn('known_hosts', msg)

    def test_invalid_host_key_message_contains_original_error(self):
        from odoo.exceptions import ValidationError

        connector = _make_connector(sftp_host_key=self.BAD_HOST_KEY, name='BadConn')
        transport = _make_transport(connector)
        pm, _, _, host_keys_instance = _make_paramiko_mock()

        original_error = 'Malformed entry on line 1'
        host_keys_instance.load.side_effect = Exception(original_error)

        with patch.dict(sys.modules, {'paramiko': pm}):
            with patch.object(_sftp_mod.tempfile, 'NamedTemporaryFile') as mock_ntf, \
                 patch.object(_sftp_mod.os, 'unlink'):

                fake_file = MagicMock()
                fake_file.__enter__ = MagicMock(return_value=fake_file)
                fake_file.__exit__ = MagicMock(return_value=False)
                fake_file.name = '/tmp/fake_bad2.known_hosts'
                mock_ntf.return_value = fake_file

                with self.assertRaises(ValidationError) as ctx:
                    transport._get_client()

        self.assertIn(original_error, str(ctx.exception))


class TestTempFileAlwaysDeleted(unittest.TestCase):
    """The temp file must be deleted even when host_keys.load() raises."""

    HOST_KEY = 'sftp.example.com ssh-rsa AAAAB3NzaC1yc2EAAAA=='

    def test_unlink_called_when_load_raises(self):
        """os.unlink() is called in the finally block even on load() failure."""
        from odoo.exceptions import ValidationError

        connector = _make_connector(sftp_host_key=self.HOST_KEY)
        transport = _make_transport(connector)
        pm, _, _, host_keys_instance = _make_paramiko_mock()

        host_keys_instance.load.side_effect = Exception('Simulated parse error')

        unlink_calls = []

        def tracking_unlink(path):
            unlink_calls.append(path)

        with patch.dict(sys.modules, {'paramiko': pm}):
            with patch.object(_sftp_mod.tempfile, 'NamedTemporaryFile') as mock_ntf, \
                 patch.object(_sftp_mod.os, 'unlink', side_effect=tracking_unlink):

                fake_file = MagicMock()
                fake_file.__enter__ = MagicMock(return_value=fake_file)
                fake_file.__exit__ = MagicMock(return_value=False)
                fake_file.name = '/tmp/fake_finally.known_hosts'
                mock_ntf.return_value = fake_file

                with self.assertRaises(ValidationError):
                    transport._get_client()

        # unlink must have been called exactly once with the temp file path
        self.assertEqual(unlink_calls, ['/tmp/fake_finally.known_hosts'])

    def test_unlink_called_on_success(self):
        """os.unlink() is also called in the normal (success) code path."""
        connector = _make_connector(sftp_host_key=self.HOST_KEY)
        transport = _make_transport(connector)
        pm, _, _, _ = _make_paramiko_mock()

        unlink_calls = []

        def tracking_unlink(path):
            unlink_calls.append(path)

        with patch.dict(sys.modules, {'paramiko': pm}):
            with patch.object(_sftp_mod.tempfile, 'NamedTemporaryFile') as mock_ntf, \
                 patch.object(_sftp_mod.os, 'unlink', side_effect=tracking_unlink):

                fake_file = MagicMock()
                fake_file.__enter__ = MagicMock(return_value=fake_file)
                fake_file.__exit__ = MagicMock(return_value=False)
                fake_file.name = '/tmp/fake_success.known_hosts'
                mock_ntf.return_value = fake_file

                transport._get_client()

        self.assertEqual(unlink_calls, ['/tmp/fake_success.known_hosts'])


if __name__ == '__main__':
    unittest.main()
