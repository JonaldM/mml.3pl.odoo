{
    'name': '3PL Integration \u2014 Mainfreight',
    'version': '15.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Mainfreight Warehousing 3PL integration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['stock_3pl_core'],
    'data': [
        'security/ir.model.access.csv',
        'views/connector_mf_views.xml',
    ],
    'demo': ['data/connector_mf_demo.xml'],
    'installable': True,
    'auto_install': False,
}
