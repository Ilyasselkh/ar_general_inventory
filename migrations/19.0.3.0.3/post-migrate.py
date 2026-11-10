def migrate(cr, version):
    # Preserve all existing assignments, including archived ones. Do not grant
    # access to any additional checklist or change earlier validation records.
    cr.execute('''
        INSERT INTO ar_inventory_approver_user_rel (approver_id, user_id)
        SELECT id, user_id FROM araymond_inventory_approver
        WHERE user_id IS NOT NULL
        ON CONFLICT DO NOTHING
    ''')
