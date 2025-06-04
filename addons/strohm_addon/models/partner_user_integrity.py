import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

class PartnerUserIntegrity(models.Model):
    _name = 'strohm_addon.partner_user_integrity'
    _description = 'Check Partner-User Integrity'

    @api.model
    def _check_partner_user_integrity(self):
        """
        Verify the integrity of the partner-user relationship:
        1. Every user should have exactly one partner
        2. The user's partner should have correct information
        """
        _logger.info("Starting partner-user integrity check")

        # Check users with missing partners
        users_without_partners = self.env['res.users'].sudo().search([
            ('active', 'in', [True, False]),  # Include inactive users
            ('partner_id', '=', False)
        ])

        if users_without_partners:
            user_list = ", ".join([f"{u.name} (ID: {u.id})" for u in users_without_partners[:10]])
            if len(users_without_partners) > 10:
                user_list += f" and {len(users_without_partners) - 10} more"
            _logger.error(f"Found {len(users_without_partners)} users without partners: {user_list}")
            # Automatic fix attempt could be implemented here if required

        # Check users with partners missing essential information
        users_with_incomplete_partners = self.env['res.users'].sudo().search([
            ('active', '=', True),  # Only check active users
            ('partner_id', '!=', False)
        ]).filtered(lambda u: not u.partner_id.name or not u.partner_id.email)

        if users_with_incomplete_partners:
            user_list = ", ".join([f"{u.name} (ID: {u.id})" for u in users_with_incomplete_partners[:10]])
            if len(users_with_incomplete_partners) > 10:
                user_list += f" and {len(users_with_incomplete_partners) - 10} more"
            _logger.error(f"Found {len(users_with_incomplete_partners)} users with incomplete partner information: {user_list}")

            # Try to fix incomplete partners by copying data from user to partner
            for user in users_with_incomplete_partners:
                try:
                    vals = {}
                    if not user.partner_id.name and user.name:
                        vals['name'] = user.name
                    if not user.partner_id.email and user.email:
                        vals['email'] = user.email

                    if vals:
                        user.partner_id.write(vals)
                        _logger.info(f"Fixed incomplete partner information for user {user.name} (ID: {user.id})")
                except Exception as e:
                    _logger.error(f"Failed to fix partner information for user {user.name} (ID: {user.id}): {str(e)}")

        # Check partners with multiple users (which could be valid in some cases but should be logged)
        multi_user_partners = self.env['res.partner'].sudo().search([])
        multi_user_partners = multi_user_partners.filtered(lambda p: len(p.user_ids) > 1)

        if multi_user_partners:
            partner_list = ", ".join([f"{p.name} (ID: {p.id}, Users: {len(p.user_ids)})" for p in multi_user_partners[:10]])
            if len(multi_user_partners) > 10:
                partner_list += f" and {len(multi_user_partners) - 10} more"
            _logger.info(f"Found {len(multi_user_partners)} partners with multiple users: {partner_list}")

        _logger.info("Partner-user integrity check completed")
        return True
