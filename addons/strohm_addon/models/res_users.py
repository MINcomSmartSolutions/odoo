from odoo import models


class StrohmResUsers(models.Model):
    _inherit = 'res.users'

    def _rpc_api_keys_only(self):
        # First check if the parent implementation returns True
        res = super()._rpc_api_keys_only()
        if res:
            return True
        # Your custom condition here (e.g., a new field or configuration)
        # return self.your_custom_condition
        return True  # Always force API keys for RPC
