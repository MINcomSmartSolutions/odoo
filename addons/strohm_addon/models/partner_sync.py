import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

class PartnerSync(models.Model):
    _name = 'strohm_addon.partner_sync'
    _description = 'Partner Sync with Backend'

    @api.model
    def sync_partner_changes(self, partner_ids, old_values=None):
        """Send partner changes to backend system"""
        if not partner_ids:
            return False

        UserSync = self.env['strohm_addon.user_sync']
        partners = self.env['res.partner'].sudo().browse(partner_ids)

        for partner in partners:
            try:
                # Check if partner is associated with portal users
                if not partner.user_ids or not any(user.has_group('base.group_portal') for user in partner.user_ids):
                    continue

                # Get current partner data
                new_values = {
                    'id': partner.id,
                    'name': partner.name,
                    'email': partner.email,
                    'active': partner.active,
                    'phone': partner.phone,
                    'mobile': partner.mobile,
                    'street': partner.street,
                    'street2': partner.street2,
                    'city': partner.city,
                    'zip': partner.zip,
                    'country_id': partner.country_id.id if partner.country_id else False,
                    'user_ids': partner.user_ids.ids,
                    'peppol_endpoint': partner.peppol_endpoint,
                    'vat': partner.vat,
                }

                # Prepare old values if available
                old_data = {}
                if old_values and partner.id in old_values:
                    old_data = old_values[partner.id]

                # Send to backend with consistent format
                event_type = 'partner_changed'
                data = {
                    'record_id': partner.id,
                    'old_data': UserSync._make_json_serializable(old_data),
                    'new_data': UserSync._make_json_serializable(new_values)
                }
                UserSync._send_to_backend(event_type, data)

            except Exception as e:
                _logger.error(f"Failed to sync partner changes for partner {partner.id}: {str(e)}")

        return True

    @api.model
    def sync_partner_deletion(self, partner_data):
        """Send partner deletion to backend system"""
        try:
            if not partner_data.get('has_portal_user', False):
                return True

            UserSync = self.env['strohm_addon.user_sync']

            # Format data consistently with other operations
            event_type = 'partner_deleted'
            data = {
                'record_id': partner_data.get('id'),
                'old_data': UserSync._make_json_serializable(partner_data),
                'new_data': {}
            }
            UserSync._send_to_backend(event_type, data)
            return True
        except Exception as e:
            _logger.error(f"Failed to sync partner deletion: {str(e)}")
            return False
