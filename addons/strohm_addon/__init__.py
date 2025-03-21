from . import controllers
from . import models
import logging
_logger = logging.getLogger(__name__)


def _set_parameters_init_hook(env):
    """Code to execute when the module is installed"""

    # Disable default digest emails
    try:
        env['ir.config_parameter'].set_param('digest.default_digest_emails', 'False')
    except Exception as e:
        _logger.warning("Failed to disable digest.default_digest_emails: %s", e)

    # Disable default digest ID
    try:
        env['ir.config_parameter'].set_param('digest.default_digest_id', '0')
    except Exception as e:
        _logger.warning("Failed to disable digest.default_digest_id: %s", e)

    # Also deactivate any existing digests
    try:
        digests = env['digest.digest'].search([])
        if digests:
            digests.write({'state': 'deactivated'})
    except Exception as e:
        _logger.warning("Failed to deactivate digest.digest records: %s", e)
