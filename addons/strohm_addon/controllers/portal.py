import os

import werkzeug
from werkzeug import urls
from werkzeug.utils import redirect

from odoo.addons.portal.controllers.portal import CustomerPortal, get_error
from odoo.http import route
from odoo import _
from odoo.http import request


class CustomCustomerPortal(CustomerPortal):
    """Extend the CustomerPortal class to add custom routes."""

    @route('/my/deactivate_account', type='http', auth='user', website=True, methods=['POST'])
    def deactivate_account(self, validation, **post):
        values = self._prepare_portal_layout_values()
        values['open_deactivate_modal'] = True
        values['get_error'] = get_error

        backend_url = os.environ.get('BACKEND_URL')

        if validation != request.env.user.login:
            values['error_message'] = _('The validation code does not match your login.')
        else:
            request.env.user.sudo()._deactivate_portal_user(**post)
            request.session.logout()

            return werkzeug.utils.redirect(backend_url + '/logout?message=%s' % urls.url_quote(_('Account deleted!')))

        return request.render('portal.portal_my_security', values, headers={
            'X-Frame-Options': 'SAMEORIGIN',
            'Content-Security-Policy': "frame-ancestors 'self'",
        })
