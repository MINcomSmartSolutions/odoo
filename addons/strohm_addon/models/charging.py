from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.tools.safe_eval import datetime
import pytz
_logger = logging.getLogger(__name__)


class ChargingSessionInvoice(models.TransientModel):
    _name = 'charging.session.invoice'
    _description = 'Charging Invoice Related to Ladeabrechnung'



    @api.model
    def generate(self,session_start,session_end, partner_id, lines_data):
        """
        :param partner_id:    ID of the sale/customer (res.partner)
        :param lines_data:    list of dicts, each with keys:
            - name:          product name (string)
            - sku:           internal reference for product (string)
            - uom_name:      unit of measure name, e.g. "kWh" (string)
            - base_price:    standard list price for product, e.g. 0.35 (float)
            - custom_rate:   actual invoice price, e.g. 0.38 (float)
            - quantity:      consumed quantity, e.g. 150 (float)
            - session_start: start date of the charging session (datetime)
            - session_end:   end date of the charging session (datetime)
        :return:            created account.move record
        """
        invoice_datetime_format_from = "%Y%m%dT%H:%M:%S"
        invoice_datetime_format_to = DEFAULT_SERVER_DATETIME_FORMAT

        AccountMove = self.env['account.move']
        Partner = self.env['res.partner'].browse(partner_id)
        if not Partner:
            raise UserError(_("Partner with id %s not found") % partner_id)

        # Get country id of germany
        country = self.env['res.country'].search([('code', '=', 'DE')], limit=1)

        tax = self.env['account.tax'].search([
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



            print(session_start, session_end)
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
        Product = self.env['product.product']
        UoM = self.env['uom.uom']
        sku = data.get('sku')
        name = data.get('name') or sku
        base_price = data.get('base_price', 0.0)
        uom_name = data.get('uom_name', 'kWh')

        # 1) find or create the UoM
        uom = UoM.search([('name', '=', uom_name)], limit=1)
        if not uom:
            # Get a valid UoM category (trying electrical first, fallback to default 'Unit')
            category = self.env.ref('uom.uom_categ_energy', raise_if_not_found=True)

            # Create the UoM with a valid category
            uom = UoM.create({
                'name': uom_name,
                'category_id': category.id,
                'rounding': 0.01,
                'factor_inv': 1.0,
            })

        product = Product.search([('default_code', '=', sku)], limit=1)
        if product:
            if product.list_price != base_price:
                product.write({'list_price': base_price})
            return product

        product = Product.create({
            'name': name,
            'default_code': sku,
            'type': 'consu',
            'uom_id': uom.id,
            'uom_po_id': uom.id,
            'list_price': base_price,
            'invoice_policy': 'delivery'
        })
        return product


class SessionTimeline(models.Model):
    _name = 'charging.session.timeline'
    _description = 'Charging Session Timeline'

    # Use delegation inheritance instead of direct inheritance
    move_id = fields.Many2one('account.move', string='Related Invoice',
                              required=True, ondelete='cascade',
                              auto_join=True, delegate=True)

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
