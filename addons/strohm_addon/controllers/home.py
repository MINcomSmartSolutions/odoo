# odoo/custom/src/odoo/addons/strohm_addon/controllers/home.py
import os

import werkzeug

from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.home import Home
from werkzeug.utils import redirect

import logging
_logger = logging.getLogger(__name__)


class CustomHome(Home):
    def __init__(self):
        self.DOODBA_ENV =  os.environ.get('DOODBA_ENVIRONMENT')

    def _validate_redirect(self, redirect_url):
        """Validate that redirect URL is safe"""
        allowed_schemes = ['https']
        allowed_hosts = ['yourdomain.com', 'othertrusted.com']

        # Allow any redirect in debug mode for development convenience

        if self.DOODBA_ENV == 'devel':
            return True

        if not redirect_url:
            return False

        parsed = werkzeug.urls.url_parse(redirect_url)
        return (not parsed.scheme or parsed.scheme in allowed_schemes) and \
            (not parsed.netloc or parsed.netloc in allowed_hosts)


    @http.route('/web/admin_login', type='http', auth='none')
    def web_admin_login(self, redirect=None, **kw):
        """Custom admin login endpoint that handles login directly"""

        _logger.info("Admin login attempt from IP: %s", request.httprequest.remote_addr)

        # Validate the redirect URL if provided
        if redirect and not self._validate_redirect(redirect):
            _logger.warning("Suspicious redirect URL blocked: %s", redirect)
            redirect = None

        # Use the parent implementation directly
        return super(CustomHome, self).web_login(redirect=redirect, **kw)

    @http.route('/web/login', type='http', auth='none')
    def web_login(self, redirect=None, **kw):
        """Redirect GET requests to admin_login but handle POST normally"""
        # Validate the redirect URL if provided
        if redirect and not self._validate_redirect(redirect):
            _logger.warning("Suspicious redirect URL blocked: %s", redirect)
            redirect = None

        # Handle POST requests (actual login attempts) with parent implementation
        if request.httprequest.method == 'POST':
            return super(CustomHome, self).web_login(redirect=redirect, **kw)

        # Redirect GET requests to admin_login
        return request.redirect('/web/admin_login')

    @http.route('/web/session/logout', type='http', auth='user')
    def logout(self, redirect=None, **kw):
        """Override logout to redirect to external URL after session destroy
        regardless of any redirect parameter"""
        request.session.logout(keep_db=True)

        # Ignore any incoming redirect parameter and always use our external URL
        _logger.info("Processing logout request, redirecting ")

        # FIXME: This URL should be configurable
        # base_url = request.env['ir.config_parameter'].sudo().get_param('strohm_addon.backend_internal')
        base_url = 'http://localhost:3000'
        return werkzeug.utils.redirect(base_url + '/logout?successful_logout=true')
