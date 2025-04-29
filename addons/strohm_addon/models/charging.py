from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


class ChargingSessionInvoice(models.TransientModel):
    _name = 'charging.session.invoice'
    _description = 'Charging Invoice Related to Ladeabrechnung'

    @api.model
    def generate(self, session_start, session_end, partner_id, lines_data):
        """
        Generate an invoice for a charging session.

        Args:
            session_start (datetime): Session start datetime in UTC.
            session_end (datetime): Session end datetime in UTC.
            partner_id (int): ID of the sale/customer (`res.partner`).
            lines_data (list[dict]): Invoice line data. List of dicts, each with keys:
                - name (str): Product name.
                - sku (str): Internal reference for product.
                - uom_name (str): Unit of measure name (e.g., "kWh"; only "kWh" accepted for now).
                - base_price (float): Standard list price for product (e.g., 0.35).
                - custom_rate (float): Actual invoice price (e.g., 0.38).
                - quantity (float): Consumed quantity (e.g., 150, in kWh).
                // TODO: Add more fields if needed. e.g. tax, bill_date etc.

        Returns:
            recordset: The created `account.move` record.
        """
        invoice_datetime_format_to = DEFAULT_SERVER_DATETIME_FORMAT
        country = self.env.ref('base.de', raise_if_not_found=False) or self.env['res.country'].search(
            [('code', '=', 'DE')], limit=1)

        AccountMove = self.env['account.move']
        Partner = self.env['res.partner'].browse(partner_id)
        if not Partner:
            raise UserError(_("Partner with id %s not found") % partner_id)

        # Use ref when possible instead of search
        tax = self.env.ref('l10n_de.tax_sale_19', raise_if_not_found=False) or self.env['account.tax'].search([
            ('amount', '=', 19),
            ('price_include', '=', True),
            ('country_id', '=', country.id),
        ], limit=1)

        if not tax:
            raise UserError(
                _("No tax found for Germany with 19% VAT. Make sure the Settings --> Invoice --> Fiscal Localization is set to Germany."))

        # format session start and end dates
        _session_start = session_start.strftime(invoice_datetime_format_to)
        _session_end = session_end.strftime(invoice_datetime_format_to)

        invoice_lines = []
        for data in lines_data:
            # 1) ensure product exists
            product = self._get_or_create_product(data)

            # 2) build the invoice line vals
            qty = data.get('quantity', 1.0)
            price_unit = data.get('custom_rate') or product.list_price
            line_name = data.get('name') or product.name
            invoice_lines.append((0, 0, {
                'product_id': product.id,
                'name': line_name,
                'quantity': qty,
                'price_unit': price_unit,
                'tax_ids': [(6, 0, tax.ids)],  # Apply 19% tax
            }))

        # 3) create the draft customer invoice
        move_vals = {
            'move_type': 'out_invoice',  # customer invoice
            'invoice_date': fields.Date.today(),
            'partner_id': Partner.id,
            'invoice_line_ids': invoice_lines,
            'session_start': _session_start,
            'session_end': _session_end,
        }
        invoice = AccountMove.create(move_vals)

        # Optional: post immediately
        # invoice.action_post()

        return invoice

    def _get_or_create_product(self, data):
        """
        Finds or creates a product.product using:
          - default_code = data['sku']
        and sets up its UoM and list_price = data['base_price'].
        """
        # Try fetching the product and UoM in parallel using prefetch

        sku = data.get('sku')

        product = self.env['product.product'].with_context(active_test=False).search([('default_code', '=', sku)],
                                                                                     limit=1)

        # Only search for UoM if a product needs to be created
        if not product:
            # FIXME: Might not need to search for UoM, as it should be created with l10n_de
            uom_name = data.get('uom_name', 'kWh')
            uom = self.env['uom.uom'].search([('name', '=', uom_name)], limit=1)
            if not uom:
                category = self.env.ref('uom.uom_categ_energy', raise_if_not_found=True)
                uom = self.env['uom.uom'].create({
                    'name': uom_name,
                    'category_id': category.id,
                    'rounding': 0.01,
                    'factor_inv': 1.0,
                })

            # Create product with all fields at once
            product = self.env['product.product'].create({
                'name': data.get('name') or sku,
                'default_code': sku,
                'type': 'consu',
                'uom_id': uom.id,
                'uom_po_id': uom.id,
                'list_price': data.get('base_price', 0.0),
                'invoice_policy': 'delivery'
            })
        elif product.list_price != data.get('base_price', 0.0):
            product.list_price = data.get('base_price', 0.0)

        return product


class SessionTimeline(models.Model):
    _name = 'charging.session.timeline'
    _description = 'Charging Session Timeline'

    # Use delegation inheritance instead of direct inheritance
    move_id = fields.Many2one('account.move', string='Related Invoice',
                              required=True, ondelete='cascade',
                              auto_join=True, delegate=True, index=True)

    # All dates are stored in UTC and formatted on the client side
    session_start = fields.Datetime(string='Charging Session Start', readonly=False)
    session_end = fields.Datetime(string='Charging Session End', readonly=False)


class AccountMove(models.Model):
    _inherit = 'account.move'

    session_start = fields.Datetime(
        string='Session Start',
        related='charging_session_timeline_id.session_start',
        store=True,
        readonly=False,
    )
    session_end = fields.Datetime(
        string='Session End',
        related='charging_session_timeline_id.session_end',
        store=True,
        readonly=False,
    )
    charging_session_timeline_id = fields.One2many(
        'charging.session.timeline', 'move_id',
        string='Charging Session Timeline'
    )

    # In case utc datetime is needed in a specific format

    # session_start_tz = fields.Char(
    #     string="Session Start (Formatted)",
    #     compute='_compute_session_times'
    # )
    #
    # session_end_tz = fields.Char(
    #     string="Session End (Formatted)",
    #     compute='_compute_session_times'
    # )
    #
    # @api.depends('session_start', 'session_end')
    # def _compute_session_times(self):
    #     target_timezone = 'Europe/Berlin'  # Replace with your desired timezone
    #     time_format = '%d.%m.%Y %H:%M'  # German format example
    #
    #     for record in self:
    #         # Convert session_start
    #         if record.session_start:
    #             utc_dt = pytz.utc.localize(record.session_start)
    #             local_dt = utc_dt.astimezone(pytz.timezone(target_timezone))
    #             record.session_start_tz = local_dt.strftime(time_format)
    #         else:
    #             record.session_start_tz = ''
    #
    #         # Convert session_end
    #         if record.session_end:
    #             utc_dt = pytz.utc.localize(record.session_end)
    #             local_dt = utc_dt.astimezone(pytz.timezone(target_timezone))
    #             record.session_end_tz = local_dt.strftime(time_format)
    #         else:
    #             record.session_end_tz = ''
