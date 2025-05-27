import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
import time

# Fix imports
from odoo import http, fields, _
from odoo.http import request, Controller
from odoo.exceptions import ValidationError, UserError
import secrets
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging
import werkzeug.urls
import werkzeug.utils
from ..models.res_users_apikeys import CustomAPIKeys
# import debugpy

_logger = logging.getLogger(__name__)


# TODO: Input validation and sanitization

class StrohmAPI(Controller):
    def __init__(self):
        super().__init__()
        _logger.info("Initializing StrohmAPI")
        self.datetime_format = "%Y-%m-%dT%H:%M:%S"

        # debugpy.wait_for_client()
        # debugpy.breakpoint()

        if os.environ.get('ODOO_ENV') == 'dev':
            _logger.setLevel(logging.DEBUG)

        # Check current company and its fiscal country
        company = request.env.company
        _logger.info(f"Using company: {company.name} (id: {company.id})")
        if company.country_id.code != 'DE':
            _logger.warning(
                f"Company {company.name} does not have Germany set as fiscal country. Current: {company.country_id.name or 'Not set'}")
        else:
            _logger.info(f"Company {company.name} has correct fiscal country: {company.country_id.name}")

        # Check if de_DE is enabled
        lang = request.env['res.lang'].sudo().search([('code', '=', 'de_DE')], limit=1)
        if not lang:
            # If language doesn't exist in the database, install it
            _logger.warning("German language (de_DE) not found, please install it")
        elif not lang.active:
            # If language exists but is not active, activate it
            lang.sudo().write({'active': True})
            _logger.debug("German language (de_DE) activated")

        # Initialize standard products during API startup
        self._ensure_standard_products()

        self.API_SECRET = os.environ.get('ODOO_API_SECRET')
        if not self.API_SECRET:
            _logger.error("API secret not found in environment variables. Please set ODOO_API_SECRET")
            raise ValueError("API secret not found in environment variables. Please set ODOO_API_SECRET")


    def _ensure_standard_products(self):
        """Pre-create standard products used by the charging system"""
        try:
            _logger.info("Ensuring standard charging products exist")

            # Use the ChargingSessionInvoice model to ensure products exist
            charging_model = request.env['charging.session.invoice'].sudo()
            self.standard_products = charging_model.ensure_standard_products()

        except Exception as e:
            _logger.error(f"Failed to initialize standard products: {str(e)}", exc_info=True)


    def _encrypt_api_key(self, api_key):
        """Encrypt API key using environment variable secret"""

        salt = self._generate_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            # See: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2
            iterations=600_000,
        )
        api_secret = self.API_SECRET
        key = base64.b64encode(kdf.derive(api_secret.encode()))
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
            'key_salt': base64.urlsafe_b64encode(salt).decode()
        }

    def _decrypt_api_key(self, encoded_api_key, encoded_salt):
        """Decrypt API key using environment variable secret"""

        _logger.debug("🔑 Decrypting API key")
        try:
            # Decode base64 inputs once
            encrypted_key = base64.urlsafe_b64decode(encoded_api_key)
            salt = base64.urlsafe_b64decode(encoded_salt)

            # Derive the same key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                # See: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2
                iterations=600_000,
            )
            api_secret = self.API_SECRET
            key = base64.b64encode(kdf.derive(api_secret.encode()))
            f = Fernet(key)

            decrypted_key = f.decrypt(encrypted_key).decode()
            _logger.debug("🔑 API key decrypted successfully")
            return decrypted_key
        except InvalidToken:
            raise ValidationError("Invalid API or salt")
        except Exception as e:
            _logger.error(f"Decryption failed: {str(e)}", exc_info=e, stack_info=True)
            raise ValueError(f"Decryption failed: {str(e)}")

    def _validate_admin_token(self, headers):
        """Validate Bearer token from Authorization header"""

        # TODO Even tough the uri is internal and not exposed to internet, admin token can be hijacked? Better hash it.

        auth_header = headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return False

        token = auth_header.split(' ')[1]

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

        if isinstance(message, str):
            message = message.encode('utf-8')

        return hmac.new(
            secret,
            message,
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

    def _check_valid_payment_method(self, partner_id):
        """Check if user has a valid payment method"""
        _logger.debug(f"Checking if partner {partner_id} has a valid payment method")
        payment_token = request.env['payment.token'].sudo().search(
            [('partner_id', '=', partner_id), ('active', '=', True)], limit=1
        )
        return bool(payment_token and payment_token.exists())

    @http.route('/internal/test', type='http', auth='public', methods=['GET'], csrf=False)
    def test(self, **kw):
        """Test endpoint"""
        return request.make_json_response({'status': self._check_valid_payment_method(44)}, status=200)

    @http.route('/internal/user/valid_pm', type='http', auth='public', methods=['POST'], csrf=False)
    def check_payment_method(self, **kw):
        try:
            data = json.loads(request.httprequest.data)

            timestamp = data.get('timestamp')
            req_user_id = data.get('user_id')
            req_partner_id = data.get('partner_id')
            encrypted_key = data.get('key')
            key_salt = data.get('key_salt')
            salt = data.get('salt')
            hash = data.get('hash')

            if not req_user_id or not encrypted_key or not key_salt or not timestamp or not hash or not salt:
                raise ValidationError("Missing required parameters")

            decrypted_key = self._decrypt_api_key(encrypted_key, key_salt)

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)
            if not user_id == int(req_user_id):
                raise ValidationError("Invalid API key")

            partner_id = request.env['res.users'].sudo().browse(req_user_id).partner_id.id
            if not partner_id or not partner_id == int(req_partner_id):
                raise ValidationError("Invalid partner ID")

            message = f"{timestamp}{req_user_id}{req_partner_id}{encrypted_key}{key_salt}{salt}"
            if not (self._validate_hash(hash, message)):
                return request.make_json_response({'error': 'Invalid signature'}, status=403)

            has_valid_payment_method = 1 if (self._check_valid_payment_method(partner_id)) else 0

            resp_timestamp = datetime.utcnow().strftime(self.datetime_format)
            _salt = self._generate_salt(decode=True)
            resp_message = f"{resp_timestamp}{has_valid_payment_method}{_salt}"
            _hash = self._generate_hash(resp_message)

            return request.make_json_response(
                {'timestamp': resp_timestamp, 'result': has_valid_payment_method, 'salt': _salt, 'hash': _hash},
                status=200)

        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"Valid payment method check error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)

    @http.route('/internal/rotate_api_key', type='http', auth='public', methods=['POST'], csrf=False)
    def rotate_api_key(self, **kw):
        """Rotate API key for a user"""
        try:
            # Validate admin token
            if not self._validate_admin_token(request.httprequest.headers):
                return request.make_json_response({'error': 'Invalid admin token'}, status=401)

            data = json.loads(request.httprequest.data)

            timestamp = data.get('timestamp')
            req_user_id = data.get('user_id')
            encrypted_key = data.get('key')
            key_salt = data.get('key_salt')
            salt = data.get('salt')
            hash = data.get('hash')

            if not req_user_id or not encrypted_key or not key_salt or not timestamp or not hash or not salt:
                raise ValidationError("Missing required parameters")

            decrypted_key = self._decrypt_api_key(encrypted_key, key_salt)

            # Continue with the existing validation logic
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=decrypted_key)
            if not user_id == int(req_user_id):
                raise ValidationError("Invalid API key")

            message = f"{timestamp}{req_user_id}{encrypted_key}{key_salt}{salt}"
            if not (self._validate_hash(hash, message)):
                return request.make_json_response({'error': 'Invalid signature'}, status=403)

            # Set the api keys expiration date to right now to invalidate it
            request.env['res.users.apikeys'].sudo().search([('user_id', '=', req_user_id)]).write(
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
                return request.make_json_response(
                    {'error': f'A partner with email {data.get("email")} already exists'}, status=409
                )
            else:
                partner = request.env['res.partner'].sudo().create(
                    {k: v for k, v in partner_values.items() if v}
                )

            portal_group = request.env.ref('base.group_portal')
            user_values = {
                'name': data.get('name'),
                'login': data.get('email'),
                'email': data.get('email'),
                'partner_id': partner.id,
                'lang': 'de_DE',
                'active': True,
                'groups_id': [(6, 0, [portal_group.id])],
            }

            # Check if user with this login/email already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', data.get('email'))], limit=1)
            if existing_user:
                return request.make_json_response({'error': f'A user with email {data.get("email")} already exists'},
                                                  status=409)

            user = request.env['res.users'].sudo().with_context(no_reset_password=True).create(user_values)


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
            # TODO: Add user_id and partner_id to the request for more specific validation
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

            _logger.debug('🔑 User"s API key decrypted successfully')

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
            _logger.debug('🔑 User session authenticated successfully')

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

    @http.route('/internal/bill/create', type='http', auth='public', methods=['POST'], csrf=False)
    def create_bill(self, **kw):
        try:

            raw_data = request.httprequest.data
            if not raw_data:
                raise ValidationError("No data provided in request body")
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON: {str(e)}")

            lines_data = data.get('lines_data')
            timestamp = str(data.get('timestamp'))
            encrypted_api_key = str(data.get('key'))
            key_salt = str(data.get('key_salt'))
            req_session_start = data.get('session_start')
            req_session_end = data.get('session_end')

            # Validate required fields
            session_start = datetime.strptime(req_session_start, self.datetime_format)
            session_end = datetime.strptime(req_session_end, self.datetime_format)

            # salt = str(data.get('salt'))
            # hash = str(data.get('hash'))

            # if not lines_data or not encrypted_api_key or not key_salt or not timestamp or not hash or not salt:
            #     raise ValidationError("Missing required parameters")

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
            partner_id = request.env['res.users'].sudo().browse(user_id).partner_id.id

            if not user_id or not partner_id:
                raise ValidationError("Invalid API key")

            # Create the message that was used for the signature
            # message = f"{timestamp}{user_id}{partner_id}{encrypted_api_key}{key_salt}"
            # if not self._validate_hash(hash, message):
            #     return request.make_json_response({'error': 'Invalid signature'}, status=403)

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

            # Generate the bill using the model method
            bill = request.env['charging.session.invoice'].sudo().generate(session_start, session_end, partner_id,
                                                                           lines_data)

            return request.make_json_response({
                'success': True,
                'bill_id': bill.id,
                'message': "Bill created successfully"
            }, status=201)

        except ValidationError as ve:
            return request.make_json_response({'error': str(ve)}, status=400)
        except Exception as e:
            _logger.error(f"Bill creation error: {str(e)}", exc_info=True, stack_info=True)
            return request.make_json_response({'error': str(e)}, status=500)
