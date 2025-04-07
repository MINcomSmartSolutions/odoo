# models/res_partner.py
import os

from odoo import models
import requests
import json
import logging

_logger = logging.getLogger(__name__)

#TODO: Needs more debugging and network between odoo and backend conatainer
class ResPartner(models.Model):
    _inherit = 'res.partner'

    def write(self, vals):
        # Only proceed for portal users
        if self.user_ids and any(user.has_group('base.group_portal') for user in self.user_ids):
            # Store only the fields that are being modified
            changes = {}
            for rec in self:
                # Get current values for changed fields only
                changes[rec.id] = {field: rec[field] for field in vals}

            result = super(ResPartner, self).write(vals)

            # Get new values for changed fields only
            for rec in self:
                if rec.id in changes:
                    old_values = changes[rec.id]
                    new_values = {field: rec[field] for field in vals}
                    self._notify_backend('update', rec.id, old_values, new_values)

            return result
        return super(ResPartner, self).write(vals)

    def unlink(self):
           # Store data before deletion
           to_delete = []
           for rec in self:
               try:
                   # Check if the partner is in the process of being deleted after a user deletion
                   # by checking the context or recent logs

                   should_notify = True

                   # Log the decision factors for debugging
                   _logger.info(f"Partner {rec.id}: has_users={bool(rec.user_ids)}, "
                               f"will_notify={should_notify}")

                   partner_data = {
                       'id': rec.id,
                       'name': rec.name,
                       'should_notify': should_notify,
                       'data': rec.read()[0]
                   }
                   to_delete.append(partner_data)
               except Exception as e:
                   _logger.warning(f"Could not prepare data for partner {rec.id}: {str(e)}")

           # Rest of the method remains similar
           result = super(ResPartner, self).unlink()

           # Notify backend about all deleted partners
           for partner in to_delete:
               try:
                   self.with_context(active_test=False)._notify_backend(
                       'delete', partner['id'], partner.get('data', {}), {})
               except Exception as e:
                   _logger.error(f"Failed to notify backend about deletion of partner {partner['id']}: {str(e)}")

           _logger.info(f"Processed {len(to_delete)} partners, notified backend about {len(to_delete)} partners")
           return result

    def _notify_backend(self, operation, record_id, old_data, new_data):
        try:
            backend_url = os.environ.get('BACKEND_URL', '')
            api_key = os.environ.get('ODOO_API_SECRET', '')

            if not backend_url or not api_key:
                _logger.error("Backend URL or API key not configured")
                return

            # Clean data for JSON serialization
            def make_json_serializable(data):
                if isinstance(data, dict):
                    return {k: make_json_serializable(v) for k, v in data.items()}
                elif isinstance(data, list):
                    return [make_json_serializable(i) for i in data]
                elif isinstance(data, bytes):
                    import base64
                    return base64.b64encode(data).decode('utf-8')
                # Handle Odoo model records
                elif hasattr(data, '_name') and hasattr(data, 'id'):
                    # For model records, return a dictionary with id and name (if available)
                    result = {'id': data.id, 'model': data._name}
                    if hasattr(data, 'name') and data.name:
                        result['name'] = data.name
                    return result
                # Handle datetime objects
                elif hasattr(data, 'isoformat'):  # This covers both datetime and date objects
                    return data.isoformat()
                else:
                    return data


            payload = {
                'operation': operation,
                'record_id': record_id,
                'old_data': make_json_serializable(old_data),
                'new_data': make_json_serializable(new_data)
            }

            headers = {
                'Content-Type': 'application/json',
                'X-API-Key': api_key
            }

            response = requests.post(
                f"{backend_url}/internal/odoo/update_user",
                data=json.dumps(payload),
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                _logger.error(f"Failed to notify backend: {response.status_code} {response.text}")

        except Exception as e:
            _logger.exception(f"Error notifying backend: {str(e)}")
