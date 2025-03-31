from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    backend_external_url = fields.Char(string="Backend External URL", config_parameter='strohm.backend_external', readonly=True)
    backend_internal_url = fields.Char(string="Backend Internal URL", config_parameter='strohm.backend_internal', readonly=True)
