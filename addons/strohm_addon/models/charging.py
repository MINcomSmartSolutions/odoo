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
                // TODO: Add more fields if needed. e.g. payment terms, bill_date etc.

        Returns:
            recordset: The created `account.move` record.
        """
        invoice_datetime_format_to = DEFAULT_SERVER_DATETIME_FORMAT

        AccountMove = self.env['account.move']
        Partner = self.env['res.partner'].browse(partner_id)
        if not Partner:
            raise UserError(_("Partner with id %s not found") % partner_id)

        # format session start and end dates
        _session_start = session_start.strftime(invoice_datetime_format_to)
        _session_end = session_end.strftime(invoice_datetime_format_to)

        invoice_lines = []
        for data in lines_data:
            # Find product (but don't create or modify it)
            product = self._get_or_create_product(data)

            # Build the invoice line vals
            qty = data.get('quantity', 1.0)
            # Use custom_rate if provided, otherwise fall back to product's list_price
            price_unit = data.get('price_unit', product.list_price)
            invoice_lines.append((0, 0, {
                'product_id': product.id,
                'quantity': qty,
                'price_unit': price_unit,  # This uses custom price without changing product's base price
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

    @api.model
    def ensure_standard_products(self):
        """
        Ensure all standard products exist in the database.

        Returns:
            dict: Dictionary mapping product SKUs to product records
        """
        products = {}

        # Define standard product here
        product_definitions = [
            {
                'name': 'Ladesitzung',
                'sku': 'standard_charging',
                'uom_name': 'kWh',
                'base_price': 0.35,
            },
        ]

        for data in product_definitions:
            sku = data.get('sku')
            _logger.info(f"Looking up product with SKU: {sku}")

            product = self.env['product.product'].with_context(active_test=False).search(
                [('default_code', '=', sku)], limit=1
            )

            if not product:
                _logger.info(f"Product with SKU {sku} not found, creating new product")

                # Create UoM if needed
                uom_name = data.get('uom_name', 'kWh')
                uom = self.env['uom.uom'].search([('name', '=', uom_name)], limit=1)
                if not uom:
                    _logger.info(f"UOM {uom_name} not found, creating new UOM")
                    category = self.env.ref('uom.uom_categ_energy', raise_if_not_found=False)
                    uom = self.env['uom.uom'].create({
                        'name': uom_name,
                        'category_id': category.id,
                        'rounding': 0.01,
                        'factor_inv': 1.0,
                    })
                    self.env.cr.commit()  # Commit UOM creation

                # Create product
                _logger.info(f"Creating product with SKU: {sku}, name: {data.get('name')}")
                country = self.env.ref('base.de', raise_if_not_found=False) or self.env.ref['res.country'].search(
                    [('code', '=', 'DE')], limit=1)

                # Use ref when possible instead of search
                tax = self.env.ref('l10n_de.tax_sale_19', raise_if_not_found=False) or self.env.ref[
                    'account.tax'].search([
                    ('amount', '=', 19),
                    ('price_include', '=', True),
                    ('country_id', '=', country.id),
                ], limit=1)

                if not tax:
                    raise UserError(
                        _("No tax found for Germany with 19% VAT. Make sure the Settings --> Invoice --> Fiscal Localization is set to Germany."))

                product = self.env['product.product'].create({
                    'name': data.get('name') or sku,
                    'default_code': sku,
                    'type': 'consu',
                    'uom_id': uom.id,
                    'uom_po_id': uom.id,
                    'list_price': data.get('base_price', 0.3),
                    'invoice_policy': data.get('invoice_policy', 'delivery'),
                    'tax_ids': data.get('tax_ids',[(6, 0, tax.ids)]),
                })

                self.env.cr.commit()  # Commit product creation
                _logger.info(f"Created product with ID: {product.id}")
            elif product.list_price != data.get('base_price', 0.3):
                _logger.info(
                    f"Updating price for product {sku} from {product.list_price} to {data.get('base_price', 0.3)}")
                product.list_price = data.get('base_price', 0.3)
                self.env.cr.commit()  # Commit price update

            products[sku] = product

        return products


    def _get_or_create_product(self, data):
        """
        Finds a product by SKU without modifying its base price.
        No longer creates products - they must be pre-created during initialization.
        """
        sku = data.get('sku')

        # Try to get product from API cache if available
        api = self.env.context.get('strohm_api')
        if api and hasattr(api, 'standard_products') and sku in api.standard_products:
            return api.standard_products[sku]

        # Fall back to database lookup if not cached
        product = self.env['product.product'].with_context(active_test=False).search(
            [('default_code', '=', sku)], limit=1
        )

        if not product:
            # Instead of creating a product on-the-fly, raise an error
            raise ValueError(
                f"Product with SKU '{sku}' not found. Products must be pre-created in system initialization.")

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
