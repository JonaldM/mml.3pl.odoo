# addons/stock_3pl_core/transport/sftp.py
import io
import logging
import os
import tempfile

from odoo.exceptions import ValidationError

from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)


class _WarnOnNewHostKeyPolicy:
    """Accept new host keys but log a WARNING so operators notice unexpected keys.

    In production, replace with paramiko.RejectPolicy() and pre-populate
    known_hosts via the connector's sftp_host_key field.
    """

    def missing_host_key(self, client, hostname, key):
        _logger.warning(
            'SFTP: accepting unverified host key for %s (%s %s). '
            'Set sftp_host_key on the connector to enforce strict verification.',
            hostname, key.get_name(), key.get_fingerprint().hex(),
        )


class SftpTransport(AbstractTransport):
    """SFTP transport adapter using paramiko.

    Lazily imports paramiko so module load does not fail if paramiko is absent.
    Files are deleted from the inbound path immediately after pickup (deduplication
    via SFTP-delete is the design contract — see design doc Section: Idempotency).
    """

    def _get_client(self):
        import paramiko
        ssh = paramiko.SSHClient()

        if self.connector.sftp_host_key:
            # Strict mode: pre-load the known host key and reject anything else.
            # Write the key text to a temp file because paramiko.HostKeys.load()
            # requires a filename, not a file-like object.
            host_keys = paramiko.HostKeys()
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    suffix='.known_hosts',
                    delete=False,
                    encoding='utf-8',
                ) as f:
                    f.write(self.connector.sftp_host_key)
                    tmp_path = f.name
                host_keys.load(tmp_path)
            except Exception as e:
                raise ValidationError(
                    f'SFTP connector "{self.connector.name}": invalid sftp_host_key '
                    f'format. Expected known_hosts format '
                    f'(e.g. "hostname key-type base64-key"). Error: {e}'
                )
            finally:
                if tmp_path is not None:
                    os.unlink(tmp_path)
            ssh._host_keys = host_keys
            ssh._host_keys_filename = None
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # Fail-secure: reject connections when no host key is configured.
            # Set sftp_host_key on the connector to allow SFTP connections.
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

        ssh.connect(
            hostname=self.connector.sftp_host,
            port=self.connector.sftp_port or 22,
            username=self.connector.sftp_username,
            password=self.connector.get_credential('sftp_password'),
            timeout=30,
        )
        return ssh.open_sftp(), ssh

    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        """Upload a file to the SFTP outbound path.

        `filename` is required. If omitted, returns a retriable error.
        Payload can be str or bytes; str is encoded to UTF-8.
        """
        if not filename:
            return self._retriable_error('SFTP send requires a filename')
        sftp, ssh = None, None
        try:
            # Ensure filename cannot traverse outside the configured outbound directory
            safe_name = os.path.basename(filename)
            if safe_name != filename:
                raise ValidationError(
                    f'SFTP upload refused: filename "{filename}" contains path separators.'
                )
            sftp, ssh = self._get_client()
            data = payload if isinstance(payload, bytes) else payload.encode('utf-8')
            path = f"{self.connector.sftp_outbound_path}/{safe_name}"
            sftp.putfo(io.BytesIO(data), path)
            return self._success()
        except Exception as e:
            _logger.error('SFTP send failed: %s', e)
            return self._retriable_error(str(e).split('\n')[0][:200])
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()

    def poll(self, path=None):
        """Retrieve and delete all files from the SFTP inbound path.

        Returns a list of (filename, content) tuples so callers can use
        the filename for duplicate-detection and logging.
        Files are deleted immediately after successful read to prevent
        double-processing on repeat polls (design contract).
        Returns [] on any error.
        """
        sftp, ssh = None, None
        results = []
        try:
            sftp, ssh = self._get_client()
            inbound = path or self.connector.sftp_inbound_path
            files = sftp.listdir(inbound)
            for fname in files:
                fpath = f'{inbound}/{fname}'
                try:
                    with sftp.open(fpath, 'rb') as f:
                        content = f.read().decode('utf-8')
                    sftp.remove(fpath)  # Delete immediately after pickup
                    results.append((fname, content))
                except Exception as e:
                    _logger.warning('SFTP: could not read/delete %s: %s', fpath, e)
        except Exception as e:
            _logger.warning('SFTP poll failed: %s', e)
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()
        return results
