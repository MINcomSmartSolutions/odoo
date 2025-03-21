from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    logout_url = fields.Char(string="Logout URL", config_parameter='strohm.logout_url', readonly=True)
