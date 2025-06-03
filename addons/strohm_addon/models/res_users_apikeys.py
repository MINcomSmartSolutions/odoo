from odoo import models, api, _
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT, INDEX_SIZE, API_KEY_SIZE
from odoo.exceptions import AccessError
import binascii
import os

from odoo.http import request

import logging
_logger = logging.getLogger(__name__)


class CustomAPIKeys(models.Model):
    _inherit = 'res.users.apikeys'
    _description = 'Custom API Keys for Users'

    @api.model
    def _generate_for_user(self, user_id, scope, name, expiration_date=None):
        """
        Generate an API key for a specific user

        :param int user_id: ID of the user to generate the key for
        :param str scope: the scope of the key
        :param str name: the name of the key
        :param date expiration_date: the expiration date of the key
        :return: str: the generated API key
        """
        self._check_expiration_date(expiration_date)

        # Check if current user has rights to generate keys for others
        if not self.env.is_admin() and self.env.user.id != user_id:
            raise AccessError(_("Only administrators can generate API keys for other users"))

        # Generate the key
        k = binascii.hexlify(os.urandom(API_KEY_SIZE)).decode()

        # # Get the target user
        # target_user = self.env['res.users'].browse(user_id)

        self.env.cr.execute("""
        INSERT INTO {table} (name, user_id, scope, expiration_date, key, index)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """.format(table=self._table),
        [name, user_id, scope, expiration_date or None, KEY_CRYPT_CONTEXT.hash(k), k[:INDEX_SIZE]])

        ip = request.httprequest.environ['REMOTE_ADDR'] if request else 'n/a'
        _logger.info("%s generated for user #%s: scope: <%s> from %s",
            self._description, user_id, scope, ip)

        return k
