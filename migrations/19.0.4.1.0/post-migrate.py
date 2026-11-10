def migrate(cr, version):
    # Preserve the responsible user on existing risk lines when displaying
    # the new multi-user field. Do not modify checklist approval history.
    cr.execute('''
        INSERT INTO ar_flow_line_responsible_rel (line_id, user_id)
        SELECT id, responsible_id FROM araymond_flow_line
        WHERE checklist_id IN (
            SELECT id FROM araymond_flow_checklist WHERE phase = 'risk'
        ) AND responsible_id IS NOT NULL
        ON CONFLICT DO NOTHING
    ''')
