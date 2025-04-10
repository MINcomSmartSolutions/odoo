import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
import time

# Fix imports
from odoo import http, fields
from odoo.http import request, Controller
from odoo.exceptions import ValidationError
import secrets
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging
import werkzeug.urls
import werkzeug.utils
from ..models.res_users_apikeys import CustomAPIKeys

_logger = logging.getLogger(__name__)


class UserAPI(Controller):
    def __init__(self):
        super().__init__()
        _logger.info("Initializing UserAPI")

        # Check if de_DE is enabled
        lang = request.env['res.lang'].sudo().search([('code', '=', 'de_DE')], limit=1)
        if not lang:
            # If language doesn't exist in the database, install it
            _logger.warning("German language (de_DE) not found, install it")
        elif not lang.active:
            # If language exists but is not active, activate it
            lang.sudo().write({'active': True})
            _logger.debug("German language (de_DE) activated")

        self.api_secret = os.environ.get('ODOO_API_SECRET')

    def _encrypt_api_key(self, api_key):
        """Encrypt API key using environment variable secret"""

        salt = secrets.token_bytes(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=32,
            salt=salt,
            iterations=6000,
        )

        key = base64.b64encode(kdf.derive(self.api_secret.encode()))
        f = Fernet(key)
        encrypted_key = f.encrypt(api_key.encode())

        # Verify encryption by decrypting and comparing
        try:
            decrypted = f.decrypt(encrypted_key).decode()
            if decrypted != api_key:
                _logger.error("Encryption verification failed: decrypted key doesn't match original")
                raise ValueError("Encryption verification failed")
        except Exception as e:
            _logger.error(f"Encryption verification failed: {str(e)}", exc_info=e, stack_info=True)
            raise ValidationError("Encryption verification failed")

        return {
            'key': base64.urlsafe_b64encode(encrypted_key).decode(),
            'salt': base64.urlsafe_b64encode(salt).decode()
        }

    def _decrypt_api_key(self, encoded_api_key, encoded_salt):
        """Decrypt API key using environment variable secret"""
        try:
            # Decode base64 inputs once
            encrypted_key = base64.urlsafe_b64decode(encoded_api_key)
            salt = base64.urlsafe_b64decode(encoded_salt)


            # Derive the same key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA512(),
                length=32,
                salt=salt,
                iterations=6000,
            )

            key = base64.b64encode(kdf.derive(self.api_secret.encode()))
            f = Fernet(key)

            decrypted_key = f.decrypt(encrypted_key).decode()
            return decrypted_key
        except InvalidToken:
            raise ValidationError("Invalid API or salt")
        except Exception as e:
            _logger.error(f"Decryption failed: {str(e)}", exc_info=e, stack_info=True)
            raise ValueError(f"Decryption failed: {str(e)}")

    def _validate_admin_token(self, headers):
        """Validate Bearer token from Authorization header"""
        auth_header = headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return False

        token = auth_header.split(' ')[1]

        _logger.debug(f"🔑 Provided Token: {token}")

        # Verify token using _check_credentials
        admin_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=token)
        if not admin_id:
            _logger.debug("❌ Token verification failed.")
            return False

        _logger.debug("✅ Token verification succeeded.")

        # Update request environment with the authenticated user
        admin = request.env['res.users'].sudo().browse(admin_id)
        if not admin.has_group('base.group_system'):
            _logger.debug("❌ Admin doesn't have system access.")
            return False

        _logger.debug("✅ Admin has system access.")
        request.update_env(user=admin)
        return True

    def _validate_required_fields(self, data):
        """Validate required fields for user creation"""
        required_fields = ['name', 'email']
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            raise ValidationError(f"Missing required fields: {', '.join(missing_fields)}")

        # Validate email format
        if data.get('email') and '@' not in data.get('email'):
            raise ValidationError("Invalid email format")

        return True

    def _assign_user_group(self, user):
        """
        Assign portal group to user and remove internal group
        Returns: True if successful, False otherwise
        """
        try:
            # Get the groups
            portal_group = request.env.ref('base.group_portal')
            internal_group = request.env.ref('base.group_user')

            if not portal_group:
                _logger.error("Portal group 'base.group_portal' not found")
                return False

            # Remove the user from internal users grouo and add to portal users group
            user.write({
                'groups_id': [
                    (3, internal_group.id),  # Remove from internal group
                    (4, portal_group.id)  # Add to portal group
                ]
            })

            _logger.debug(f"User {user.id} ({user.name}) configured as portal user")
            return True
        except Exception as e:
            _logger.error(f"Failed to assign portal group: {str(e)}")
            return False

    def _generate_signature(self, state, secret):
        """
        Generate HMAC signature for authentication validation.

        Args:
            state (dict): Dictionary containing 'key', 'timestamp', 'odoo_user_id' and 'salt'
            secret (bytes): Secret key used for generating the signature

        Returns:
            str: Hexadecimal digest of the HMAC signature
        """
        message = f"{state['key']}{state['timestamp']}{state['odoo_user_id']}{state['salt']}".encode()
        return hmac.new(
            secret,
            message,
            hashlib.sha256
        ).hexdigest()

    @http.route('/internal/rotate_api_key', type='http', auth='public', methods=['POST'],csrf=False)
    def rotate_api_key(self, **kw):
        """Rotate API key for a user"""
        try:
            # Validate admin token
            if not self._validate_admin_token(request.httprequest.headers):
                return request.make_json_response({'error': 'Invalid admin token'}, status= 401)

            data = json.loads(request.httprequest.data)
            user_id = data.get('user_id')
            encrypted_key = data.get('api_key')
            salt = data.get('salt')

            if not user_id or not encrypted_key or not salt:
                raise ValidationError("Missing required parameters")

            decrypted_key = self._decrypt_api_key(encrypted_key, salt)

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)
            if not user_id or not user_id == int(user_id):
                raise ValidationError("Invalid API key")

            # Set the api keys expiration date to right now to invalidate it
            request.env['res.users.apikeys'].sudo().search([('user_id', '=', user_id)]).write({'expiration_date': fields.Datetime.now()})

            # Generate new API key
            new_api_key = request.env['res.users.apikeys'].sudo()._generate_for_user(
                user_id,
                'rpc',  # scope
                'Auto-generated API key',  # name
                None #TODO: Set a viable expiration_date
            )

            new_encrypted_data = self._encrypt_api_key(new_api_key)

            # Return the new encrypted API key
            return request.make_json_response({
                'success': True,
                'user_id': user_id,
                'encrypted_key': new_encrypted_data['key'],
                'salt': new_encrypted_data['salt']
            }, status=200)


        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"API key rotation error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)


    @http.route('/internal/create', type='http', auth='public', methods=['POST'], csrf=False)
    def create_user(self, **kw):
        try:
            # Validate admin token
            if not self._validate_admin_token(request.httprequest.headers):
                return request.make_json_response({'error': 'Invalid admin token'}, status= 401)

            data = json.loads(request.httprequest.data)

            # Validate required fields
            self._validate_required_fields(data)

            # Create user and partner
            germany = request.env['res.country'].sudo().search([('code', '=', 'DE')], limit=1)

            partner_values = {
                'name': data.get('name'),
                'email': data.get('email'),
                'country_id': germany.id,
                'lang': 'de_DE',
                'tz': 'Europe/Berlin',
            }

            # Check if partner with this email already exists
            existing_partner = request.env['res.partner'].sudo().search([('email', '=', data.get('email'))], limit=1)
            if existing_partner:
                partner = existing_partner
            else:
                partner = request.env['res.partner'].sudo().create(
                    {k: v for k, v in partner_values.items() if v}
                )

            user_values = {
                'name': data.get('name'),
                'login': data.get('email'),
                'email': data.get('email'),
                'partner_id': partner.id,
                'lang': 'de_DE',
                'active': True,
            }

            # Check if user with this login/email already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', data.get('email'))], limit=1)
            if existing_user:
                return request.make_json_response({'error': f'A user with email {data.get("email")} already exists'},
                                                  status=409)

            user = request.env['res.users'].sudo().with_context(no_reset_password=True).create(user_values)

            # Assign user groups
            self._assign_user_group(user)

            # Generate and store API key for new user using the new method
            api_key = request.env['res.users.apikeys'].sudo()._generate_for_user(
                user.id,
                'rpc',  # scope
                'Auto-generated API key',  # name
                None #TODO: Set a viable expiration_date
            )

            # Encrypt API key for transport, this encryption is done by us (independent of odoo framework)
            encrypted_data = self._encrypt_api_key(api_key)

            return request.make_json_response({
                'success': True,
                'user_id': user.id,
                'partner_id': partner.id,
                'encrypted_key': encrypted_data['key'],
                'salt': encrypted_data['salt']
            }, status=201)


        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            return request.make_json_response({'error': str(e)}, status=500)

    @http.route('/portal_login', type='http', auth='public', methods=['GET'], csrf=False)
    def portal_auto_login(self, **kw):
        try:
            encoded_api_key = str(kw.get('api_key'))
            encoded_salt = str(kw.get('salt'))
            timestamp = str(kw.get('timestamp'))
            signature = str(kw.get('signature'))

            if not encoded_api_key or not encoded_salt or not timestamp or not signature:
                return request.make_json_response({'error': 'Missing parameters'}, status=401)

            # Verify timestamp isn't too old (5-minute window)
            # Parse ISO timestamp to Unix time
            timestamp_dt = datetime.strptime(timestamp, "%Y%m%dT%H:%M:%S")
            timestamp_unix = int(timestamp_dt.timestamp())
            if int(time.time()) - timestamp_unix > 300:
                return request.make_json_response({'error': 'Link expired'}, status=403)

            decrypted_key = self._decrypt_api_key(encoded_api_key, encoded_salt)

            _logger.debug('🔑 API key decrypted successfully')

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)

            if not user_id:
                raise ValidationError("Invalid API key")

            # Verify signature
            expected_signature = self._generate_signature({
                'key': encoded_api_key,
                'timestamp': timestamp,
                'odoo_user_id': user_id,
                'salt': encoded_salt,
            }, self.api_secret.encode())

            if not secrets.compare_digest(signature, expected_signature):
                return request.make_json_response({'error': 'Invalid signature'}, status=403)

            _logger.debug(f"🔑 User ID: {user_id}")

            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists():
                return request.make_json_response({'error': 'User not found'}, status=404)

            request.httprequest.environ['wsgi.interactive'] = False

            # Changed 'token' to 'password' to match Odoo's expectation
            credential = {'login': user.login, 'password': decrypted_key, 'type': 'webauthn'}

            # Proper authentication
            request.session.authenticate(request.env.cr.dbname, credential)
            _logger.debug('🔑 User authenticated successfully')

            # TODO: Do we need to create session everytime we login?
            request.env.user = user
            request.session.session_token = user._compute_session_token(request.session.sid)
            request.session.uid = user.id
            request.session.login = user.login
            request.session.context = dict(request.session.context, uid=user.id)

            # Get the redirect path (default to portal home page)
            redirect_path = kw.get('redirect', '/my')

            # Redirect user directly to the portal page
            return werkzeug.utils.redirect(redirect_path)

        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"Portal login error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)
