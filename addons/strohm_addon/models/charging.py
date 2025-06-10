import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


class ChargingSessionInvoice(models.TransientModel):
    _name = 'charging.session.invoice'
    _description = 'Charging Invoice Related to Ladeabrechnung'

    @api.model
    def generate(self, session_start, session_end, partner, lines_data):
        """
        Generate an invoice for a charging session.

        Args:
            session_start (Datetime): Session start datetime UTC
            session_end (Datetime): Session end datetime UTC
            partner (res.partner): The partner for whom the invoice is created.
            lines_data (BillLineItem): List of dictionaries containing line item data for the invoice.
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

        AccountMove = self.env['account.move']
        Partner = self.env['res.partner'].browse(partner.id)
        if not Partner or not Partner.exists():
            raise ValidationError('Partner not found for the user. Please ensure the user has a valid partner record.')

        invoice_lines = []
        for data in lines_data:
            # Find product (but don't create or modify it)
            product = self._get_or_create_product(data)

            # Build the invoice line vals - use direct attribute access instead of .get()
            qty = data.quantity
            price_unit = data.price_unit
            invoice_lines.append((0, 0, {
                'product_id': product.id,
                'quantity': qty,
                'price_unit': price_unit,
            }))

        # Create the draft customer invoice
        move_vals = {
            'move_type': 'out_invoice',  # customer invoice
            'invoice_date': fields.Date.today(),
            'partner_id': Partner.id,
            'invoice_line_ids': invoice_lines,
            'session_start': session_start.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
            'session_end': session_end.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
        }

        invoice = AccountMove.create(move_vals)

        # post immediately
        # invoice.action_post()

        #TODO: charge the user with their default payment method???

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
                # list_price is set to base_price by default and can be set changed with price_unit when creating invoice
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
                _logger.info(f"Creating product with SKU: {sku}, name: {data.get('name', sku) }")
                country = self.env.ref('base.de', raise_if_not_found=False) or self.env.ref['res.country'].search(
                    [('code', '=', 'DE')], limit=1)

                # Use ref when possible instead of search
                tax = self.env.ref('l10n_de.tax_sale_19', raise_if_not_found=False)
                if not tax:
                    tax = self.env['account.tax'].search([
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
                    'invoice_policy': 'delivery',
                    # In Odoo, the tuple `(6, 0, ids)` is a special command used in many2many and one2many fields to set the field's value. Here:
                    #
                    # - `6` is the command to replace all existing records with the provided list.
                    # - `0` is ignored (kept for compatibility).
                    # - `tax.ids` is the list of IDs to set.
                    #
                    # So, `[(6, 0, tax.ids)]` means: replace all current tax records with the ones in `tax.ids`.
                    'tax_ids': [(6, 0, tax.ids)],
                })

                self.env.cr.commit()  # Commit product creation
                _logger.info(f"Created product with ID: {product.id}")

            products[sku] = product

        return products


    def _get_or_create_product(self, data):
        """
        Finds a product by SKU without modifying its base price.
        """
        # Use direct attribute access instead of .get()
        sku = data.sku

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
