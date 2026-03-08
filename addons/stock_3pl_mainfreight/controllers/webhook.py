import hmac
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _validate_webhook_secret(env, request_secret):
    """Return True iff request_secret matches the configured webhook secret.

    Returns False if either the request secret or the stored secret is
    missing/empty — an unconfigured system must never grant access.
    """
    if not request_secret:
        return False
    stored = env['ir.config_parameter'].sudo().get_param(
        'stock_3pl_mainfreight.webhook_secret', default='')
    return bool(stored) and hmac.compare_digest(stored, request_secret)


class MFWebhookController(http.Controller):

    @http.route('/mf/webhook/order-confirmation', type='http',
                auth='none', methods=['POST'], csrf=False)
    def order_confirmation(self, **kwargs):
        return self._handle_webhook('order_confirmation')

    @http.route('/mf/webhook/inward-confirmation', type='http',
                auth='none', methods=['POST'], csrf=False)
    def inward_confirmation(self, **kwargs):
        return self._handle_webhook('inward_confirmation')

    @http.route('/mf/webhook/tracking-update', type='http',
                auth='none', methods=['POST'], csrf=False)
    def tracking_update(self, **kwargs):
        return self._handle_webhook('tracking_update')

    def _handle_webhook(self, event_type):
        secret = request.httprequest.headers.get('X-MF-Secret')
        if not _validate_webhook_secret(request.env, secret):
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        body = request.httprequest.data.decode('utf-8', errors='replace')
        _logger.debug('MF webhook %s received (%d bytes)', event_type, len(body))
        # Wire to inbound message queue once cloud hosting is confirmed (currently
        # MF delivers via SFTP poll; webhooks are a future push-delivery option).
        return request.make_json_response({'status': 'received'})
