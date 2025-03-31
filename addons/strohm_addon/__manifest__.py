# noinspection PyStatementEffect
{
    'name': "Ladeabrechnung Integration",
    'summary': "API integration for user management and authorization",
    'description': """
        WIP
    """,
    'version': '18.0.1.0.0',
    'category': 'Services',
    'author': 'MINcom Smart Solutions GmbH',
    'website': 'https://min2sol.com',
    'depends': [
        'base',
        'web',
        'portal',
        'account',
    ],
    'data': [
        'views/portal_templates.xml',
        'data/res_config_settings_data.xml',
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
    'license': 'LGPL-3',
    'post_init_hook': '_set_parameters_init_hook',
}
