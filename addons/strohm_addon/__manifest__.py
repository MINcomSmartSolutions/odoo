# noinspection PyStatementEffect
{
    'name': "Ladeabrechnung Integration",
    'summary': "Integration for Ladeabrechnung",
    'description': """
        1. Company's Fiscal Localization should be set to Germany for invoicing.
        2. The module is designed to work with the Odoo Community Edition (CE) version 18.0.
    """,
    'version': '18.0.1.0.0',
    'category': 'Services',
    'author': 'MINcom Smart Solutions GmbH',
    'website': 'https://min2sol.com',
    'depends': [
        'base',
        'web',
        'l10n_de',
        'portal',
        'account',
        'sales_team',
        'sale_service',
        'account',
        'payment',
    ],
    'data': [
        'views/portal_templates.xml',
        'views/charging_session_invoice.xml',
        'security/ir.model.access.csv',
    ],
    'external_dependencies': {
        'python': [
            'cryptography',
            'python-dotenv',
        ],
    },
    'application': False,
    'installable': True,
    'auto_install': False,
    'post_init_hook': '_set_parameters_init_hook',
}
