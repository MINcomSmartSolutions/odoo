import logging
import os
from odoo import models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class PaymentTransactionSync(models.Model):
    _name = 'strohm_addon.payment_transaction_sync'
    _description = 'Payment Transaction Sync with Backend'


    @api.model
    def sync_payment_rejection(self, payment_data):
        """Send payment rejection to backend system and raise UserError"""
        try:
            UserSync = self.env['strohm_addon.user_sync']

            # Format data consistently with other operations
            event_type = 'payment_rejected'
            data = {
                'record_id': payment_data.get('id'),
                'old_data': UserSync._make_json_serializable(payment_data),
                'new_data': {}
            }

            # Send to backend
            UserSync._send_to_backend(event_type, data)

            # Raise UserError with the rejection reason
            error_message = payment_data.get('state_message') or _("Your payment was rejected. Please try again or use a different payment method.")
            raise UserError(_(
                "Payment Error: %s", error_message
            ))

        except UserError:
            # Re-raise UserError to show it to the user
            raise
        except Exception as e:
            _logger.error(f"Failed to sync payment rejection: {str(e)}")
            return False
