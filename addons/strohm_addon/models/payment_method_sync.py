import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class PaymentMethodSync(models.Model):
    _name = 'strohm_addon.payment_method_sync'
    _description = 'Payment Method Sync with Backend'

    @api.model
    def sync_payment_method_change(self, token_ids):
        """Send payment validity status to backend system when tokens are added/modified/removed"""
        if not token_ids:
            return False

        tokens = self.env['payment.token'].sudo().browse(token_ids)
        for token in tokens:
            try:
                # Only process tokens for portal users
                portal_users = [user for user in token.partner_id.user_ids if user.has_group('base.group_portal')]
                if not portal_users:
                    continue

                # Check if partner has any valid payment method - simplified approach
                has_valid_payment_method = self._check_valid_payment_method(token.partner_id.id)

                # Send separate notifications for each user associated with this partner
                for user in portal_users:
                    # Send to backend using the user_sync methods with individual user data
                    event_type = 'payment_validity_changed'
                    data = {
                        'has_valid_payment_method': has_valid_payment_method
                    }

                    self.env['strohm_addon.user_sync']._send_to_backend(
                        event_type,
                        data,
                        user_id=user.id,
                        partner_id=token.partner_id.id
                    )

            except Exception as e:
                _logger.error(f"Failed to sync payment validity status for token {token.id}: {str(e)}")
                # Don't raise exception to avoid interrupting the workflow

        return True

    @api.model
    def sync_payment_method_deletion(self, token_data):
        """Send payment validity status after token deletion"""
        try:
            # Extract partner information
            partner_id = token_data.get('partner_id')
            if not partner_id:
                return False

            # Find all portal users associated with this partner
            partner = self.env['res.partner'].sudo().browse(partner_id)
            portal_users = [user for user in partner.user_ids if user.has_group('base.group_portal')]
            if not portal_users:
                return False

            # Check if partner still has any valid payment method after this deletion
            has_valid_payment_method = self._check_valid_payment_method(partner_id)

            # Send separate notifications for each user
            for user in portal_users:
                # Format minimal data for backend
                event_type = 'payment_validity_changed'
                data = {
                    'has_valid_payment_method': has_valid_payment_method
                }

                self.env['strohm_addon.user_sync']._send_to_backend(
                    event_type,
                    data,
                    user_id=user.id,
                    partner_id=partner_id
                )

            return True
        except Exception as e:
            _logger.error(f"Failed to sync payment validity status after deletion: {str(e)}")
            return False

    @api.model
    def _check_valid_payment_method(self, partner_id):
        """Check if user has any valid payment method"""
        if not partner_id:
            return False

        # Use search_count() instead of search() with count=True
        payment_tokens_count = self.env['payment.token'].sudo().search_count(
            [('partner_id', '=', partner_id), ('active', '=', True)]
        )
        return payment_tokens_count > 0
