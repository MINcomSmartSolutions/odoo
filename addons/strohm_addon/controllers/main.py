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
        self.datetime_format = "%Y%m%dT%H:%M:%S"

        # Check if de_DE is enabled
        lang = request.env['res.lang'].sudo().search([('code', '=', 'de_DE')], limit=1)
        if not lang:
            # If language doesn't exist in the database, install it
            _logger.warning("German language (de_DE) not found, install it")
        elif not lang.active:
            # If language exists but is not active, activate it
            lang.sudo().write({'active': True})
            _logger.debug("German language (de_DE) activated")

        self.API_SECRET = os.environ.get('ODOO_API_SECRET')

    def _encrypt_api_key(self, api_key):
        """Encrypt API key using environment variable secret"""

        salt = self._generate_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=6000,
        )

        key = base64.b64encode(kdf.derive(self.API_SECRET.encode()))
        f = Fernet(key)
        encrypted_key = f.encrypt(api_key.encode())

        # Verify encryption by decrypting and comparing
        try:
            decrypted = f.decrypt(encrypted_key).decode('utf-8')
            if decrypted != api_key:
                _logger.error("Encryption verification failed: decrypted key doesn't match original")
                raise ValueError("Encryption verification failed")
        except Exception as e:
            _logger.error(f"Encryption verification failed: {str(e)}", exc_info=e, stack_info=True)
            raise ValidationError("Encryption verification failed")

        return {
            'key': base64.urlsafe_b64encode(encrypted_key).decode('utf-8'),
            'key_salt': base64.urlsafe_b64encode(salt).decode('utf-8')
        }

    def _decrypt_api_key(self, encoded_api_key, encoded_salt):
        _logger.info("🔑 Decrypting API key")
        _logger.info(encoded_api_key)
        _logger.info(encoded_salt)
        """Decrypt API key using environment variable secret"""
        try:
            # Decode base64 inputs once
            encrypted_key = base64.urlsafe_b64decode(encoded_api_key)
            salt = base64.urlsafe_b64decode(encoded_salt)

            # Derive the same key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=6000,
            )

            key = base64.b64encode(kdf.derive(self.API_SECRET.encode()))
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

        _logger.info(f"🔑 Provided Token: {token}")

        # Verify token using _check_credentials
        admin_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=token)
        if not admin_id:
            _logger.info("❌ Token verification failed.")
            return False

        _logger.info("✅ Token verification succeeded.")

        # Update request environment with the authenticated user
        admin = request.env['res.users'].sudo().browse(admin_id)
        if not admin.has_group('base.group_system'):
            _logger.info("❌ Admin doesn't have system access.")
            return False

        _logger.info("✅ Admin has system access.")
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

    def _generate_hash(self, message, secret=None):
        """
        Generate HMAC signature for authentication validation.

        Args:
            message (string): Message to be signed
            secret (bytes): Secret key used for generating the signature

        Returns:
            str: Hexadecimal digest of the HMAC signature
        """

        if secret is None:
            secret = self.API_SECRET.encode()

        return hmac.new(
            secret,
            message.encode(),
            hashlib.sha256
        ).hexdigest()

    def _generate_salt(self, decode=False):
        """Create a random salt for encryption"""
        salt = secrets.token_bytes(16)
        if decode:
            return base64.urlsafe_b64encode(salt).decode('utf-8')
        return salt

    def _validate_hash(self, hash, message, secret=None):
        """
        Validate HMAC signature for authentication validation.

        Args:
            hash (str): Hexadecimal digest of the HMAC signature
            message (str): Message used for generating the signature
            secret (bytes): Secret key used for generating the signature

        Returns:
            bool: True if the hash is valid, False otherwise
        """
        if secret is None:
            secret = self.API_SECRET.encode()

        expected_hash = self._generate_hash(message, secret)
        return secrets.compare_digest(hash, expected_hash)

    @http.route('/internal/rotate_api_key', type='http', auth='public', methods=['POST'], csrf=False)
    def rotate_api_key(self, **kw):
        """Rotate API key for a user"""
        try:
            # Validate admin token
            if not self._validate_admin_token(request.httprequest.headers):
                return request.make_json_response({'error': 'Invalid admin token'}, status=401)

            data = json.loads(request.httprequest.data)

            timestamp = data.get('timestamp')
            user_id = data.get('user_id')
            encrypted_key = data.get('key')
            key_salt = data.get('key_salt')
            salt = data.get('salt')
            hash = data.get('hash')

            if not user_id or not encrypted_key or not key_salt or not timestamp or not hash or not salt:
                raise ValidationError("Missing required parameters")

            decrypted_key = self._decrypt_api_key(encrypted_key, key_salt)

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)
            if not user_id == int(user_id):
                raise ValidationError("Invalid API key")

            message = f"{timestamp}{user_id}{encrypted_key}{key_salt}{salt}"
            if not (self._validate_hash(hash, message)):
                return request.make_json_response({'error': 'Invalid signature'}, status=403)

            # Set the api keys expiration date to right now to invalidate it
            request.env['res.users.apikeys'].sudo().search([('user_id', '=', user_id)]).write(
                {'expiration_date': fields.Datetime.now()})

            # Generate new API key
            new_api_key = request.env['res.users.apikeys'].sudo()._generate_for_user(
                user_id,
                'rpc',  # scope
                'Auto-generated User API key',  # name
                None  # TODO: Set a viable expiration_date
            )

            timestamp = datetime.utcnow().strftime(self.datetime_format)
            _salt = self._generate_salt(decode=True)
            new_encrypted_token_data = self._encrypt_api_key(new_api_key)

            _hash = self._generate_hash(
                f"{timestamp}{user_id}{new_encrypted_token_data['key']}{new_encrypted_token_data['key_salt']}{_salt}",
            )

            # Return the new encrypted API key
            return request.make_json_response({
                'success': True,
                'timestamp': timestamp,
                'user_id': user_id,
                'key': new_encrypted_token_data['key'],
                'key_salt': new_encrypted_token_data['key_salt'],
                'salt': _salt,
                'hash': _hash,
            }, status=200)


        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"API key rotation error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)

    @http.route('/internal/user/create', type='http', auth='public', methods=['POST'], csrf=False)
    def create_user(self, **kw):
        try:
            # Validate admin token
            if not self._validate_admin_token(request.httprequest.headers):
                return request.make_json_response({'error': 'Invalid admin token'}, status=401)

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
                'Auto-generated User API key',  # name
                None  # TODO: Set a viable expiration_date
            )

            # Encrypt API key for transport, this encryption is done by us (independent of odoo framework)
            encrypted_token_data = self._encrypt_api_key(api_key)
            _datetime = datetime.now().strftime(self.datetime_format)
            _salt = self._generate_salt(decode=True)
            _hash = self._generate_hash(
                f"{_datetime}{user.id}{partner.id}{encrypted_token_data['key']}{encrypted_token_data['key_salt']}{_salt}",
            )

            # _logger.info("🔑 API key encrypted successfully")
            # _logger.info(f"🔑 User ID: {user.id}")
            # _logger.info(f"🔑 Encrypted API key: {encrypted_token_data['key']}")
            # _logger.info(f"🔑 Encrypted API key salt: {encrypted_token_data['key_salt']}")
            # _logger.info(f"🔑 Encrypted API key salt: {_salt}")
            # _logger.info(f"🔑 Encrypted API key hash: {_hash}")
            return request.make_json_response({
                'timestamp': _datetime,
                'user_id': user.id,
                'partner_id': partner.id,
                'key': encrypted_token_data['key'],
                'key_salt': encrypted_token_data['key_salt'],
                'salt': _salt,
                'hash': _hash,
            }, status=201)


        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"User creation error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)

    @http.route('/portal_login', type='http', auth='public', methods=['GET'], csrf=False)
    def portal_auto_login(self, **kw):
        try:
            timestamp = str(kw.get('timestamp'))
            encrypted_api_key = str(kw.get('key'))
            key_salt = kw.get('key_salt')
            salt = str(kw.get('salt'))
            hash = str(kw.get('hash'))

            if not encrypted_api_key or not key_salt or not timestamp or not hash:
                return request.make_json_response({'error': 'Missing parameters'}, status=401)

            # Verify timestamp isn't too old (5-minute window)
            # Parse ISO timestamp to Unix time
            timestamp_dt = datetime.strptime(timestamp, self.datetime_format)
            timestamp_unix = int(timestamp_dt.timestamp())
            if int(time.time()) - timestamp_unix > 300:
                return request.make_json_response({'error': 'Link expired'}, status=403)

            decrypted_key = self._decrypt_api_key(encrypted_api_key, key_salt)

            _logger.debug('🔑 API key decrypted successfully')

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)

            if not user_id:
                raise ValidationError("Invalid API key")

            # Create the message that was used for the signature
            message = f"{timestamp}{user_id}{encrypted_api_key}{key_salt}{salt}"
            if not (self._validate_hash(hash, message)):
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
