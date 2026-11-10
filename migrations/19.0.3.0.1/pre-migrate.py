def migrate(cr, version):
    # Allow one user to have distinct checklist assignments. Existing rows
    # receive phase='all' through the field default, preserving their rights.
    cr.execute('ALTER TABLE IF EXISTS araymond_inventory_approver '
               'DROP CONSTRAINT IF EXISTS araymond_inventory_approver_user_unique')
