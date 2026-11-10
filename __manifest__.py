{

    'name': 'AR - Inventaire général',
    'version': '19.0.4.55.0',
    'author':'Issam Eddabbar',
    'description':"This application is used to serve as an inventory tool for the Araymond Wherehouse",
    'installable': True,


    'depends': ['base', 'web', 'mail'],
    'external_dependencies': {
        'python': ['openpyxl'],
    },
    'data': [
        'security/settings_menu_group.xml',
        'security/ir.model.access.csv',
        'views/menu.xml',
        'views/inventory.xml',
        'views/team_workflow.xml',
        'views/checklist_settings.xml',
        'views/session_preparation.xml',
        'views/complete_flow.xml',
        'views/checklist_configuration.xml',
        'views/session_assignments.xml',
        'views/checklist_scope.xml',
        'views/scan_entry.xml',
        'views/scan_consultation.xml',
        ],
    'assets': {
        'web.assets_backend': [
            'ar_general_inventory/static/src/scss/inventory.scss',
            'ar_general_inventory/static/src/js/scan_confirmation_dialog.js',
            'ar_general_inventory/static/src/js/session_form.js',
            'ar_general_inventory/static/src/js/session_chatter.js',
            'ar_general_inventory/static/src/js/scan_lines.js',
            'ar_general_inventory/static/src/xml/scan_confirmation_dialog.xml',
            'ar_general_inventory/static/src/xml/list_headers.xml',
        ],
    },
}
