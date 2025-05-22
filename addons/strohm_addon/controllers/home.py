# odoo/custom/src/odoo/addons/strohm_addon/controllers/home.py
import os

import werkzeug
from werkzeug import urls

from odoo import http, _
from odoo.http import request
from odoo.addons.web.controllers.home import Home
from werkzeug.utils import redirect

import logging

_logger = logging.getLogger(__name__)


class CustomHome(Home):
    def __init__(self):
        self.ODOO_ENV = os.environ.get('ODOO_ENV')
        self.BACKEND_URL = f"http://localhost:{os.environ.get('BACKEND_PORT')}"

    def _validate_redirect(self, redirect_url):
        """Validate that redirect URL is safe"""
        allowed_schemes = ['https']
        allowed_hosts = ['yourdomain.com', 'othertrusted.com']

        # Allow any redirect in debug mode for development convenience

        if self.ODOO_ENV == 'dev' or self.ODOO_ENV == 'test':
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
        return werkzeug.utils.redirect(self.BACKEND_URL)

    @http.route('/web/session/logout', type='http', auth='user')
    def logout(self, redirect=None, **kw):
        """Override logout to redirect to external URL after session destroy
        for regular users, and to admin login for admin users"""
        user = request.env.user
        is_admin = user.has_group('base.group_system')

        request.session.logout(keep_db=True)

        # For admin users, redirect to admin login
        if is_admin:
            _logger.info("Admin logout, redirecting to admin login page")
            return request.redirect('/web/admin_login')

        # For regular users, redirect to external URL
        _logger.info("User logout, redirecting to external URL")
        return werkzeug.utils.redirect(self.BACKEND_URL + '/logout')
