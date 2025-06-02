import os

from odoo import models, api
import requests
import json
import logging

_logger = logging.getLogger(__name__)

class UserSync(models.Model):
    _name = 'strohm_addon.user_sync'
    _description = 'User Sync with Backend'

    @api.model
    def sync_user_changes(self, user_ids, old_values=None):
        """Send user changes to backend system"""
        if not user_ids:
            return False

        users = self.env['res.users'].sudo().browse(user_ids)
        for user in users:
            try:
                # Get current user data
                new_values = {
                    'id': user.id,
                    'login': user.login,
                    'name': user.name,
                    'email': user.email,
                    'active': user.active,
                }

                # Prepare old values if available
                old_data = {}
                if old_values and user.id in old_values:
                    old_data = old_values[user.id]

                # Send to backend with consistent format
                event_type = 'user_changed'
                data = {
                    'record_id': user.id,
                    'old_data': self._make_json_serializable(old_data),
                    'new_data': self._make_json_serializable(new_values)
                }
                self._send_to_backend(event_type, data)

            except Exception as e:
                _logger.error(f"Failed to sync user changes for user {user.id}: {str(e)}")

        return True

    @api.model
    def sync_user_deletion(self, user_data):
        """Send user deletion to backend system"""
        try:
            # Format data consistently with other operations
            event_type = 'user_deleted'
            data = {
                'record_id': user_data.get('id'),
                'old_data': self._make_json_serializable(user_data),
                'new_data': {}
            }
            self._send_to_backend(event_type, data)
            return True
        except Exception as e:
            _logger.error(f"Failed to sync user deletion: {str(e)}")
            return False

    def _make_json_serializable(self, data):
        """Clean data for JSON serialization - shared by partner and user sync"""
        if isinstance(data, dict):
            return {k: self._make_json_serializable(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._make_json_serializable(i) for i in data]
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

    def _send_to_backend(self, event_type, data):
        """Send data to backend with appropriate event type"""
        backend_url = os.environ.get('BACKEND_HOST', '127.0.0.1')
        backend_port = os.environ.get('BACKEND_PORT', '3000')
        backend_url = f"http://{backend_url}:{backend_port}/internal/user/sync"
        api_key = os.environ.get('BACKEND_API_KEY')

        if not backend_url:
            _logger.error("Backend sync URL not configured")
            return False

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}' if api_key else '',
        }

        payload = {
            'event': event_type,
            'data': data,
        }

        response = requests.post(
            backend_url,
            headers=headers,
            data=json.dumps(payload),
            timeout=10
        )

        if response.status_code not in (200, 201, 202):
            _logger.error(f"Backend sync failed: {response.status_code} {response.text}")
            return False

        return True
