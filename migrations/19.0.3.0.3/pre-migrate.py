def migrate(cr, version):
    cr.execute('ALTER TABLE IF EXISTS araymond_inventory_approver '
               'DROP CONSTRAINT IF EXISTS araymond_inventory_approver_user_phase_unique')
