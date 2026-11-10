import base64
from io import BytesIO
from openpyxl import Workbook
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFullFlow(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env = self.env(user=self.env.ref('base.user_admin'))
        self.env['araymond.flow.configuration'].search([]).write({'responsible_ids': [(6, 0, self.env.user.ids)]})
        self.go_role = self.env['araymond.go.responsible'].create({
            'name': 'Responsable magasin',
            'configuration_id': self.env.ref('ar_general_inventory.flow_configuration_go').id,
            'validator_ids': [(6, 0, self.env.user.ids)],
        })
        self.env['araymond.inventory.approver'].create({'phase': 'all', 'user_ids': [(6, 0, self.env.user.ids)]})
        self.env['araymond.flow.team.config'].search([]).write({'user_ids': [(6, 0, self.env.user.ids)]})
        self.env['araymond.flow.template'].create({'phase': 'inventory_day', 'name': 'Contrôle spécifique du jour'})
        self.session = self.env['araymond.maininvsession'].create({'desc': 'Inventaire complet'})
        self.env['araymond.flow.assignment'].create([
            {'session_id': self.session.id, 'role': role, 'user_id': self.env.uid, 'shift': 'A'}
            for role in ('entry', 'control', 'scan1', 'scan2', 'scan3')
        ])

    def _location_creators(self):
        users = []
        for slot in ('1', '2'):
            user = self.env['res.users'].create({'name': 'Créateur ' + slot,
                'login': 'location_creator_' + slot,
                'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
            self.env['araymond.flow.assignment'].create({'session_id': self.session.id,
                'role': 'scan' + slot, 'user_id': user.id, 'shift': 'A'})
            users.append(user)
        return users

    def _create_location(self, user, name, rack='A'):
        self.session.with_user(user).write({'manual_scan_count_ids': [(0, 0, {
            'emplacement': name, 'location_rack': rack})]})

    def test_shared_rack_requirements_follow_cross_counts_and_restart(self):
        from ..models.complete_flow import internal
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False, two_racks=True)
        internal(self.zone).write({'ummondatory': True})
        internal(self.other_zone).write({'dummondatory': True})
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        self.check('pre').line_ids.write({'answer': 'ok'})
        for check in self.check('pre'):
            check.action_validate()
        self._create_location(creator1, 'X001', 'A')
        self._create_location(creator2, 'X002', 'B')
        pool = self.session._session_scan_pool()
        for count in pool.first_count_ids:
            self.assertEqual(count.ummondatory, count.location_rack == 'A')
            self.assertEqual(count.dummondatory, count.location_rack == 'B')
            self.finish(count, 10)
        for count in pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.assertEqual(count.location_rack, count.origin_count_id.location_rack)
            self.assertEqual(count.ummondatory, count.location_rack == 'A')
            self.assertEqual(count.dummondatory, count.location_rack == 'B')
            self.finish(count, 20)
        thirds = pool.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertTrue(thirds)
        for count in thirds:
            self.assertEqual(count.location_rack, count.origin_count_id.location_rack)
            self.assertEqual(count.ummondatory, count.location_rack == 'A')
            self.assertEqual(count.dummondatory, count.location_rack == 'B')
        self.session.action_mod_empl()
        for count in self.session._session_scan_pool().first_count_ids:
            self.assertEqual(count.ummondatory, count.location_rack == 'A')
            self.assertEqual(count.dummondatory, count.location_rack == 'B')

    def test_conflicting_rack_requirements_rejected(self):
        self.preparation(two_racks=True)
        self.zone.write({'ummondatory': True})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.other_zone.write({'rack': 'A'})

    def test_dual_team_member_can_create_for_both_teams(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        self.assertTrue(self.session.show_location_team_choice)
        teams = self.session.location_team_choice_ids.sorted('automatic_slot')
        self._create_location(self.env.user, 'A001')
        self.session.write({'manual_scan_count_ids': [(0, 0, {
            'emplacement': 'A001', 'location_rack': 'A', 'branch_id': teams[1].id})]})
        pool = self.session._session_scan_pool()
        self.assertEqual(pool.first_count_ids.branch_id, teams)
        for count in pool.first_count_ids:
            self.finish(count, 10)
        cross = pool.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 2)
        for count in cross:
            self.assertNotEqual(count.branch_id, count.origin_count_id.branch_id)
            self.finish(count, 10)
        self.assertEqual(pool.workflow_state, 'done')

    def test_blank_location_scans_headers_before_labels(self):
        from ..models.complete_flow import internal
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        for index, (um, dum) in enumerate([(False, False), (True, False), (False, True), (True, True)]):
            internal(self.zone).write({'ummondatory': um, 'dummondatory': dum})
            self.assertFalse(self.session.action_new_scan_location())
            count = self.session._session_scan_pool().first_count_ids.sorted('id')[-1]
            self.assertFalse(count.emplacement)
            self.assertTrue(count.scan_first)
            label = {'su_id': count.id, 'numEtiq': 'REF;10;LOT;HU%s' % index, 'typedegalia': 'Manuel'}
            with self.assertRaises(UserError), self.cr.savepoint():
                self.env['araymond.etiquette'].create(label)
            with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                scan.scan_input = 'X00%s' % index
            self.assertEqual(count.emplacement, 'X00%s' % index)
            self.assertTrue(count.location_scanned)
            if um or dum:
                with self.assertRaises(UserError), self.cr.savepoint():
                    self.env['araymond.etiquette'].create(label)
            if um:
                with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                    scan.scan_input = '6001234567'
                self.assertEqual(count.um, '6001234567')
            if dum:
                with self.assertRaises(UserError), self.cr.savepoint():
                    self.env['araymond.etiquette'].create(label)
                with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                    scan.scan_input = '31234567890123456'
                self.assertEqual(count.dum, '31234567890123456')
            self.env['araymond.etiquette'].create(label)
            count.action_close()

    def test_dum_survives_raypro_labels_save_and_closure(self):
        from ..models.complete_flow import internal
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        internal(self.zone).write({'dummondatory': True})
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        self.session.action_new_scan_location()
        count = self.session._session_scan_pool().first_count_ids
        dum = '31234567890123456'
        for barcode in ['A001', dum]:
            with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                scan.scan_input = barcode
        for hu in ('HU1', 'HU2'):
            with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                scan.scan_input = '{MAT:REF;QTY:10;BAT:LOT;HU:%s;PO:123}' % hu
                self.assertEqual(scan.dum, dum)
            count.invalidate_recordset()
            self.assertEqual(count.dum, dum)
        self.assertEqual(len(count.su), 2)
        self.assertEqual(set(count.su.mapped('dum')), {dum})
        count.action_close()
        self.assertTrue(count.ok_done)
        self.assertEqual(count.dum, dum)
        count.action_reset_scan()
        self.assertFalse(count.dum)
        self.assertFalse(count.su)

    def test_blank_lines_cross_scan_and_unused_line_removal(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        teams = self.session.location_team_choice_ids.sorted('automatic_slot')
        for team in (teams[0], teams[0], teams[1]):
            self.session.write({'manual_scan_count_ids': [(0, 0, {
                'scan_first': True, 'location_rack': 'A', 'branch_id': team.id})]})
        pool = self.session._session_scan_pool()
        self.assertEqual(len(pool.first_count_ids), 3)
        pool.first_count_ids[:1].unlink()
        for count in pool.first_count_ids:
            with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                scan.scan_input = 'A001'
            self.finish(count, 10)
        cross = pool.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 2)
        for count in cross:
            self.assertTrue(count.scan_first)
            self.assertFalse(count.location_scanned)
            with self.assertRaises(UserError):
                count._require_scan_headers()
            self.finish(count, 10)
        self.assertEqual(pool.workflow_state, 'done')

    def test_direct_location_button_multiple_racks_and_automatic_team(self):
        self.to_scan(start_scan=False, two_racks=True)
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        self.check('pre').line_ids.write({'answer': 'ok'})
        for check in self.check('pre'):
            check.action_validate()
        self.assertFalse(self.session.action_new_scan_location())
        self.assertFalse(self.session.action_new_scan_location())
        counts = self.session._session_scan_pool().first_count_ids.sorted('id')
        self.assertEqual(len(counts), 2)
        self.assertFalse(any(counts.mapped('emplacement')))
        self.assertFalse(any(counts.mapped('location_rack')))
        with self.assertRaises(UserError):
            counts[0].action_view_form_inv()
        team2 = self.session.location_team_choice_ids.filtered(lambda t: t.automatic_slot == '2')
        with Form(self.session.with_context(inventory_scan_menu=True),
                  view='ar_general_inventory.view_full_flow_session') as form:
            with form.manual_scan_count_ids.edit(0) as row:
                row.location_rack = 'B'
        configured = counts.filtered('location_rack')
        self.assertEqual(configured.location_rack, 'B')
        self.assertEqual(configured.branch_id.automatic_slot, '1')
        with Form(configured, view='ar_general_inventory.view_araymond_inventory_form') as scan:
            scan.scan_input = 'B123'
        with self.assertRaises(UserError):
            configured.write({'branch_id': team2.id})
        with self.assertRaises(UserError):
            configured.write({'location_rack': 'A'})

    def test_rack_table_excludes_scan_pool_and_keeps_disabled_racks(self):
        from ..models.complete_flow import internal
        self.to_scan(start_scan=False, two_racks=True)
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        self.check('pre').line_ids.write({'answer': 'ok'})
        for check in self.check('pre'):
            check.action_validate()
        self.session.action_new_scan_location()
        pool = self.session._session_scan_pool()
        self.assertTrue(pool)
        self.assertIn(pool, self.session.sess_records)
        self.assertNotIn(pool, self.session.configured_rack_ids)
        self.assertEqual(set(self.session.configured_rack_ids.ids), {self.zone.id, self.other_zone.id})
        internal(self.other_zone).write({'statusSession': False})
        self.assertIn(self.other_zone, self.session.configured_rack_ids)
        with Form(self.session, view='ar_general_inventory.view_full_flow_session') as form:
            self.assertEqual(len(form.configured_rack_ids), 2)
        internal(self.other_zone).write({'flow_archived': True})
        self.session.invalidate_recordset(['configured_rack_ids'])
        self.assertNotIn(self.other_zone, self.session.configured_rack_ids)
        self.assertTrue(pool.exists())
        self.assertTrue(pool.first_count_ids)

    def test_checklist_team_overrides_assignment_and_completed_scan_opens(self):
        from lxml import etree
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        check = self.check('pre')
        check.write({'scanner1_ids': [(6, 0, creator1.ids)],
                     'scanner2_ids': [(6, 0, self.env.user.ids)]})
        check.line_ids.write({'answer': 'ok'})
        check.action_validate()
        self.session.action_new_scan_location()
        count = self.session._session_scan_pool().first_count_ids
        self.assertEqual(count.branch_id.automatic_slot, '2')
        with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
            scan.scan_input = 'A001'
        self.finish(count, 10)
        labels = count.su
        action = count.action_open_scan_record()
        self.assertEqual(action['res_id'], count.id)
        self.assertEqual(action['target'], 'new')
        view = self.env.ref('ar_general_inventory.scan_consultation_form')
        self.assertEqual(action['views'], [(view.id, 'form')])
        self.assertEqual(etree.fromstring(view.arch_db).get('edit'), 'false')
        self.assertEqual(count.su, labels)
        with self.assertRaises(UserError):
            count.write({'dum': 'changed'})

    def test_reset_only_one_completed_scan_keeps_cross_labels(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        teams = self.session.location_team_choice_ids.sorted('automatic_slot')
        for index, team in enumerate(teams):
            self.session.write({'manual_scan_count_ids': [(0, 0, {
                'emplacement': 'A00%s' % (index + 1), 'branch_id': team.id})]})
        pool = self.session._session_scan_pool()
        for count in pool.first_count_ids:
            self.finish(count, 10)
        for count in pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 10)
        self.assertEqual(pool.workflow_state, 'done')
        target = pool.first_count_ids[:1]
        deleted_labels = target.su
        others = pool.recs_scan - target
        preserved_labels = others.su
        count_ids = pool.recs_scan.ids
        self.validate('post', {'answer': 'ok'})
        target.action_reset_scan()
        self.assertFalse(deleted_labels.exists())
        self.assertEqual(preserved_labels.exists(), preserved_labels)
        self.assertFalse(target.ok_done)
        self.assertFalse(target.final_count_id)
        self.assertFalse(self.check('post').validated_at)
        self.assertTrue(all(others.mapped('ok_done')))
        with self.assertRaises(UserError):
            target.action_reset_scan()
        self.finish(target, 10)
        self.assertEqual(pool.workflow_state, 'done')
        self.assertEqual(pool.recs_scan.ids, count_ids)
        self.assertEqual(preserved_labels.exists(), preserved_labels)
        # Reset a cross count as well: the initial team's labels stay intact.
        cross = pool.recs_scan.filtered(lambda c: c.count_phase == 'cross')[:1]
        first_labels = pool.first_count_ids.su
        cross.action_reset_scan()
        self.assertEqual(first_labels.exists(), first_labels)
        self.finish(cross, 20)
        self.assertEqual(pool.workflow_state, 'arbitration')
        for count in pool.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'):
            self.finish(count, 10)
        self.assertEqual(pool.workflow_state, 'done')
        third = pool.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')[:1]
        third.action_reset_scan()
        self.finish(third, 10)
        self.assertEqual(pool.workflow_state, 'done')

    def select_all_checklist_zones(self, session):
        action = session.action_select_checklist_scope()
        wizard = self.env['araymond.checklist.scope'].browse(action['res_id'])
        options = self.env['araymond.checklist.scope.option'].search([('wizard_id', '=', wizard.id)])
        wizard.write({kind + '_ids': [(6, 0, options.filtered(lambda o: o.kind == kind).ids)]
                      for kind in ('rack', 'level', 'warehouse')})
        wizard.action_apply()

    def approve(self):
        # The complete lifecycle must work without approving individual edits.
        pass

    def test_scan_type_is_selected_in_scan_window(self):
        from lxml import etree
        self.to_scan()
        count = self.zone.first_count_ids
        self.assertEqual(count.typedegalia, 'RayPro')
        for action, expected in [('action_receptionSAP', 'ReceptionSAP'),
                                 ('action_logistique', 'logistique'),
                                 ('action_Manuel', 'Manuel'), ('action_raypro', 'RayPro')]:
            getattr(count, action)()
            self.assertEqual(count.typedegalia, expected)
        arch = etree.fromstring(self.session.get_view(
            view_id=self.env.ref('ar_general_inventory.view_full_flow_session').id, view_type='form')['arch'])
        rack = arch.xpath("//page[@string='Racks']/field/list")[0]
        names = [f.get('name') for f in rack.xpath('./field')]
        self.assertNotIn('typedegalia', names)
        self.assertIn('ummondatory', names)
        self.assertIn('dummondatory', names)

    def test_pre_scan_requires_three_selected_teams(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        check = self.session.flow_check_ids.filtered(lambda c: c.phase == 'pre')
        check.line_ids.write({'answer': 'ok'})
        with self.assertRaises(ValidationError):
            check.action_validate()
        check.write({'scanner1_ids': [(6, 0, self.env.user.ids)],
                     'scanner2_ids': [(6, 0, self.env.user.ids)]})
        with self.assertRaises(ValidationError):
            check.action_validate()
        outsider = self.env['res.users'].create({'name': 'Non affecté', 'login': 'scanner_not_assigned',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        check.write({'scanner3_ids': [(6, 0, [self.env.uid, outsider.id])]})
        self.assertIn(outsider, check.scanner3_ids)
        self.assertFalse(self.session.assignment_ids.filtered(lambda row: row.user_id == outsider))
        check.action_validate()
        self.assertTrue(check.validated_at)
        with self.assertRaises(UserError):
            check.write({'scanner1_ids': [(5, 0, 0)]})

    def test_extra_checklist_scanner_can_create_and_scan_without_session_assignment(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        user = self.env['res.users'].create({
            'name': 'Renfort scan', 'login': 'extra_checklist_scanner',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)],
        })
        check = self.session.flow_check_ids.filtered(lambda row: row.phase == 'pre')
        with Form(check, view='ar_general_inventory.view_rack_flow_checklist') as form:
            form.scanner1_ids.add(self.env.user)
            form.scanner1_ids.add(user)
            form.scanner2_ids.add(self.env.user)
            form.scanner3_ids.add(self.env.user)
        self.assertFalse(self.session.assignment_ids.filtered(lambda row: row.user_id == user))
        check.line_ids.write({'answer': 'ok'})
        check.action_validate()
        self._create_location(user, 'A099')
        count = self.session._session_scan_pool().first_count_ids
        self.assertEqual(count.branch_id.automatic_slot, '1')
        count.with_user(user)._check_scan()
        with self.assertRaises(AccessError):
            count.with_user(user)._check_initial_team(count.rec_scan_id, self.session.team_ids.filtered(lambda row: row.automatic_slot == '2'))
        self.assertIn(self.env.user, check.scanner1_ids)
        self.assertFalse(self.session.assignment_ids.filtered(lambda row: row.user_id == user))

    def test_assigned_but_unselected_person_cannot_scan(self):
        self.to_scan()
        count = self.zone.first_count_ids
        user = self.env['res.users'].create({'name': 'Membre non sélectionné', 'login': 'scanner_not_selected',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        self.env['araymond.flow.assignment'].create({'session_id': self.session.id,
            'role': 'scan' + count.branch_id.automatic_slot, 'user_id': user.id, 'shift': 'A'})
        self.session.flow_check_ids.filtered(lambda c: c.phase == 'pre').action_validate()
        with self.assertRaises(AccessError):
            count.with_user(user)._check_scan()
        count._check_scan()

    def test_checklist_scope_starts_empty_with_unchecked_choices(self):
        self.session.action_next_flow()
        zones = self.env['araymond.invsession'].create([
            {'mainsession_id': self.session.id, 'magasinPhysique': 'M05', 'magasinSAP': 'RM00',
             'rack': rack, 'level': 'SOL', 'shift': 'A', 'supervisor_id': self.env.uid,
             'nbrEmp': 1, 'typedegalia': 'Manuel', 'statusSession': True}
            for rack in ('A', 'B')])
        self.assertFalse(self.session.rack_check_ids)
        self.assertFalse(self.session.pre_scan_ready)
        action = self.session.action_select_checklist_scope()
        wizard = self.env['araymond.checklist.scope'].browse(action['res_id'])
        self.assertFalse(wizard.rack_ids | wizard.level_ids | wizard.warehouse_ids)
        options = self.env['araymond.checklist.scope.option'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(set(options.filtered(lambda o: o.kind == 'rack').mapped('name')), {'A', 'B'})
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        wizard.write({kind + '_ids': [(6, 0, options.filtered(
            lambda o: o.kind == kind and (kind != 'rack' or o.name == 'B')).ids)]
            for kind in ('rack', 'level', 'warehouse')})
        wizard.action_apply()
        self.assertEqual(self.session.rack_check_ids.zone_id, zones.filtered(lambda z: z.rack == 'B'))

    def test_independent_checklist_scope_filters_and_validations(self):
        self.to_scan(start_scan=False, two_racks=True)
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        old = self.check('pre').filtered(lambda c: c.zone_id == self.zone)
        action = self.session.action_select_checklist_scope()
        wizard = self.env['araymond.checklist.scope'].browse(action['res_id'])
        wizard.write({'rack_ids': [(6, 0, wizard.rack_ids.filtered(lambda o: o.name == 'B').ids)]})
        wizard.action_apply()
        self.assertFalse(self.zone.checklist_selected)
        self.assertTrue(self.other_zone.checklist_selected)
        self.assertEqual(self.session.rack_check_ids.zone_id, self.other_zone)
        with self.assertRaises(UserError):
            old.filtered(lambda c: c.phase == 'pre').action_validate()
        self.validate('pre', {'answer': 'ok'})
        self.assertTrue(self.session.pre_scan_ready)
        with self.assertRaises(UserError):
            self.session.action_select_checklist_scope()

    def test_restart_preserves_sap_snapshot_without_excel_export(self):
        self.to_scan()
        first = self.zone.first_count_ids
        self.finish(first, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 10)
        self.session.action_mod_empl()
        self.assertTrue(self.zone.history_sap_snapshot)
        with self.assertRaises(AccessError):
            self.zone.write({'history_sap_snapshot': {}})
        self.assertFalse(hasattr(self.session, 'action_export_history'))

    def test_inactive_settings_lines_remain_visible_after_reload(self):
        for config in self.env['araymond.flow.configuration'].search([('phase', '!=', 'go')]):
            template = self.env['araymond.flow.template'].create({
                'name': 'Consigne à désactiver', 'configuration_id': config.id,
            })
            self.assertIn(template, config.template_ids)
            config.write({'template_ids': [(1, template.id, {'active': False})]})
            config.invalidate_recordset(['template_ids'])
            self.assertIn(template, config.template_ids)
            self.assertNotIn(template, self.env['araymond.flow.template'].search([('id', '=', template.id)]))
            config.write({'template_ids': [(1, template.id, {'active': True})]})
            self.assertTrue(template.active)
        config = self.go_role.configuration_id
        config.write({'go_responsible_ids': [(1, self.go_role.id, {'active': False})]})
        config.invalidate_recordset(['go_responsible_ids'])
        self.assertIn(self.go_role, config.go_responsible_ids)
        self.assertFalse(self.session.go_check_ids)
        config.write({'go_responsible_ids': [(1, self.go_role.id, {'active': True})]})
        self.assertTrue(self.session.go_check_ids)

    def test_template_line_responsibles_are_copied_without_changing_validators(self):
        self.to_scan(start_scan=False)
        owner = self.env['res.users'].create({'name': 'Responsable de ligne', 'login': 'template_line_owner',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        for phase in ('preparation', 'risk', 'inventory_day', 'pre', 'post'):
            template = self.env['araymond.flow.template'].create({
                'name': 'Consigne configurée ' + phase, 'phase': phase,
                'responsible_id': owner.id if phase != 'risk' else False,
                'responsible_ids': [(6, 0, owner.ids if phase == 'risk' else [])],
            })
            # New sessions copy template values; existing lines remain snapshots.
            session = self.env['araymond.maininvsession'].create({'desc': 'Modèles ' + phase})
            if phase in ('pre', 'post'):
                session.action_next_flow()
                self.env['araymond.invsession'].create({
                    'mainsession_id': session.id, 'magasinPhysique': 'M05', 'magasinSAP': 'RM00',
                    'rack': 'A', 'supervisor_id': self.env.uid, 'level': 'SOL', 'shift': 'A',
                    'nbrEmp': 1, 'typedegalia': 'Manuel', 'statusSession': True,
                })
                self.select_all_checklist_zones(session)
            line = session.flow_line_ids.filtered(lambda row: row.template_id == template)
            self.assertTrue(line)
            self.assertEqual(line.description, template.name)
            self.assertEqual(line.responsible_ids if phase == 'risk' else line.responsible_id, owner)
            self.assertNotIn(owner, line.checklist_id.validator_ids)
            template.write({'name': 'Consigne modifiée', 'responsible_id': False, 'responsible_ids': [(5, 0, 0)]})
            session._make_checklists()
            self.assertEqual(line.description, 'Consigne configurée ' + phase)
            self.assertEqual(line.responsible_ids if phase == 'risk' else line.responsible_id, owner)

    def test_preparation_groups_tabs_and_requires_simulation(self):
        from lxml import etree
        from pathlib import Path
        from runpy import run_path
        self.preparation()
        self.assertEqual(self.session.state, 'Preparation')
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        validated_at = self.check('simulation').validated_at
        arch = etree.fromstring(self.session.get_view(
            view_id=self.env.ref('ar_general_inventory.view_full_flow_session').id, view_type='form')['arch'])
        prep = arch.xpath("//sheet/notebook/page[@string='Préparation']")[0]
        self.assertEqual([page.get('string') for page in prep.xpath('./notebook/page')], [
            'Checklist de préparation', 'Racks', 'Affectation d’équipes',
            'Analyse des risques et actions', 'Simulation de stock', 'Validations'])
        self.assertEqual(set(self.session.preparation_validation_ids.mapped('phase')),
                         {'preparation', 'risk', 'simulation'})
        self.assertEqual(set(self.session.inventory_day_validation_ids.mapped('phase')),
                         {'inventory_day', 'day'})
        self.assertFalse(arch.xpath("//sheet/notebook/page[@string='Validations']"))
        self.assertTrue(prep.xpath("./notebook/page[@string='Validations']/field[@name='preparation_validation_ids']"))
        self.assertTrue(arch.xpath("//page[@string='Jour d’inventaire']/notebook/page[@string='Validations']/field[@name='inventory_day_validation_ids']"))
        self.env.flush_all()
        self.cr.execute("UPDATE araymond_maininvsession SET state = 'Simulation' WHERE id = %s", [self.session.id])
        migrate = run_path(str(Path(__file__).resolve().parents[1] / 'migrations/19.0.4.5.0/pre-migrate.py'))['migrate']
        migrate(self.cr, '19.0.4.4.0')
        self.session.invalidate_recordset(['state'])
        self.assertEqual(self.session.state, 'Preparation')
        self.assertEqual(self.check('simulation').validated_at, validated_at)
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'GoNoGo')

    def test_four_assignment_tabs_and_required_shift(self):
        self.assertEqual(set(self.session.scan_assignment_team_ids.mapped('automatic_slot')), {'1', '2', '3'})
        self.assertFalse(self.env.ref('ar_general_inventory.menu_flow_teams').active)
        self.session.action_next_flow()
        self.session.datesession = fields.Datetime.now()
        user = self.env['res.users'].create({'name': 'Affectation utilisateur', 'login': 'assignment_forms',
                                            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        for role in ('entry', 'control', 'sap'):
            with Form(self.session, view='ar_general_inventory.view_full_flow_session') as form:
                with getattr(form, role + '_assignment_ids').new() as line:
                    line.user_id = user
                    line.shift = 'B'
            assigned = self.session.assignment_ids.filtered(lambda row: row.role == role and row.user_id == user)
            self.assertEqual(assigned.shift, 'B')
            with self.assertRaises(ValidationError):
                assigned.write({'shift': False})
        for role in ('entry', 'control', 'sap', 'scan1', 'scan2', 'scan3'):
            with self.assertRaises(ValidationError):
                self.env['araymond.flow.assignment'].create({'session_id': self.session.id, 'role': role, 'user_id': user.id})

    def test_assignment_migration_preserves_people_and_known_shifts(self):
        from pathlib import Path
        from runpy import run_path
        legacy = self.env['araymond.maininvsession'].create({'desc': 'Anciennes affectations'})
        legacy._flow_store({'entry_user_ids': [(6, 0, self.env.user.ids)],
                            'control_user_ids': [(6, 0, self.env.user.ids)],
                            'entry_shift': 'B', 'control_shift': 'C'})
        migrate = run_path(str(Path(__file__).resolve().parents[1] / 'migrations/19.0.4.4.0/post-migrate.py'))['migrate']
        migrate(self.cr, '19.0.4.3.0')
        migrate(self.cr, '19.0.4.3.0')
        lines = legacy.assignment_ids
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines.filtered(lambda row: row.role == 'entry').shift, 'B')
        self.assertEqual(lines.filtered(lambda row: row.role == 'control').shift, 'C')
        scans = lines.filtered(lambda row: row.role.startswith('scan'))
        self.assertEqual(scans.user_id, self.env.user)
        self.assertFalse(any(scans.mapped('shift')))
        self.assertFalse(legacy._scan_assigned_users('1'))
        scans.write({'shift': 'A'})
        self.assertEqual(legacy._scan_assigned_users('1'), self.env.user)

    def test_scan_team_popup_normal_user_and_session_isolation(self):
        user = self.env['res.users'].create({'name': 'Opérateur affectation', 'login': 'assignment_operator',
                                            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        for team in self.session.scan_assignment_team_ids:
            with Form(team.with_user(user), view='ar_general_inventory.view_session_scan_team_assignments') as form:
                with form.assignment_ids.new() as line:
                    line.user_id = user
                    line.shift = 'admin'
            self.assertIn(user, self.session._scan_assigned_users(team.automatic_slot))
            self.assertIn(user, team.user_ids)
        other = self.env['araymond.maininvsession'].create({'desc': 'Autre session'})
        self.assertFalse(other._scan_assigned_users('1'))
        self.assertFalse(other.assignment_ids)
        with self.assertRaises(AccessError):
            self.session.scan_assignment_team_ids[:1].with_user(user).write({'name': 'Équipe détournée'})
        self.session._flow_store({'state': 'Fermeture'})
        with self.assertRaises(UserError):
            self.session.assignment_ids[:1].with_user(user).write({'shift': 'C'})

    def test_assignment_changes_audited_and_scan_authority_is_local(self):
        self.to_scan()
        count = self.zone.first_count_ids
        assignment = self.session.assignment_ids.filtered(lambda row: row.role == 'scan' + count.branch_id.automatic_slot)
        user = self.env['res.users'].create({'name': 'Nouveau scanner', 'login': 'new_assignment_scanner',
                                            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        assignment.write({'user_id': user.id, 'shift': 'C'})
        self.assertIn(self.env.user, self.session._scan_assigned_users(count.branch_id.automatic_slot))
        self.assertIn(user, count.branch_id.user_ids)
        with self.assertRaises(UserError):
            count._check_scan()
        self.validate('pre', {'answer': 'ok'})
        count.with_user(user)._check_scan()
        body = '\n'.join(str(message.body) for message in self.session.message_ids)
        self.assertIn('Nouveau scanner', body)
        self.assertIn('Équipe C', body)

    def test_rack_requires_every_validator_before_starting(self):
        self.to_scan(start_scan=False)
        reviewer = self.env['res.users'].create({'name': 'Responsable rack', 'login': 'rack_reviewer',
                                               'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        self.env['araymond.flow.configuration'].search([('phase', '=', 'pre')]).write({
            'responsible_ids': [(6, 0, [self.env.uid, reviewer.id])],
        })
        self.session.action_next_flow()
        check = self.check('pre')
        check.line_ids.write({'answer': 'ok'})
        check.action_validate()
        self.assertFalse(check.validated_at)
        self.assertEqual(self.zone.workflow_state, 'draft')
        check.with_user(reviewer).action_validate()
        self.assertTrue(check.validated_at)
        self.assertEqual(self.zone.workflow_state, 'first')
        check.line_ids[:1].write({'comment': 'Correction avant comptage'})
        with self.assertRaises(UserError):
            self.finish(self.zone.first_count_ids, 10)

    def test_migration_copies_old_checklist_content_without_signatures(self):
        from pathlib import Path
        from runpy import run_path
        from odoo.addons.ar_general_inventory.models.complete_flow import internal
        self.preparation()
        # Simulate the old session-wide checklist with a local line and a deleted line.
        old = internal(self.env['araymond.flow.checklist']).create({'session_id': self.session.id, 'phase': 'pre'})
        local = internal(self.env['araymond.flow.line']).create({'checklist_id': old.id, 'description': 'Ancien contrôle local', 'answer': 'ok'})
        attachment = self.env['ir.attachment'].create({'name': 'preuve.txt', 'raw': b'preuve', 'res_model': local._name, 'res_id': local.id})
        internal(local).write({'attachment_ids': [(6, 0, attachment.ids)]})
        # Remove the new empty rack checklist as if it had not been generated before the upgrade.
        target = self.check('pre')
        self.env.cr.execute('DELETE FROM araymond_flow_line WHERE checklist_id = %s', [target.id])
        self.env.cr.execute('DELETE FROM araymond_flow_checklist WHERE id = %s', [target.id])
        self.env.invalidate_all()
        migrate = run_path(str(Path(__file__).resolve().parents[1] / 'migrations/19.0.4.3.0/post-migrate.py'))['migrate']
        migrate(self.env.cr, '19.0.4.2.7')
        migrated = self.check('pre')
        self.assertEqual(migrated.zone_id, self.zone)
        copied = migrated.line_ids.filtered(lambda line: line.description == 'Ancien contrôle local')
        self.assertEqual(copied.answer, 'ok')
        self.assertEqual(copied.attachment_ids, attachment)
        self.assertFalse(migrated.validated_at)
        migrate(self.env.cr, '19.0.4.2.7')
        self.assertEqual(len(self.check('pre')), 1)

    def test_checklists_validated_before_location_table_is_available(self):
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False)
        self.zone.first_count_ids.unlink()
        self.session.action_next_flow()
        check = self.check('pre')
        check.line_ids.write({'answer': 'ok'})
        self.assertFalse(self.session.pre_scan_ready)
        with self.assertRaises(UserError), self.cr.savepoint():
            self.session.write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'A099'})]})
        check.action_validate()
        self.assertTrue(self.session.pre_scan_ready)
        self.assertFalse(self.session.manual_scan_count_ids)
        self._create_location(creator1, 'A099')
        self._create_location(creator2, 'B099')
        pool = self.session._session_scan_pool()
        self.assertEqual(set(pool.first_count_ids.mapped('emplacement')), {'A099', 'B099'})
        self.assertEqual(pool.first_count_ids.location_session_id, self.session)
        self.assertFalse(self.zone.first_count_ids)
        check.action_validate()
        self.assertEqual(set(pool.first_count_ids.mapped('emplacement')), {'A099', 'B099'})
        self.assertEqual(self.zone.workflow_state, 'first')
        with self.assertRaises(UserError):
            pool.first_count_ids.write({'emplacement': 'A098'})

    def test_session_locations_ignore_levels_and_complete_scan(self):
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False, two_racks=True)
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        checks = self.check('pre')
        checks.line_ids.write({'answer': 'ok'})
        checks[0].action_validate()
        self.assertFalse(self.session.pre_scan_ready)
        with self.assertRaises(UserError), self.cr.savepoint():
            self.session.write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'A099'})]})
        checks[1].action_validate()
        self.assertTrue(self.session.pre_scan_ready)
        for name in ('A001', 'A002', 'C103'):
            self._create_location(creator2 if name == 'C103' else creator1, name)
        pool = self.session._session_scan_pool()
        self.assertEqual(len(pool.first_count_ids), 3)
        self.assertFalse(pool.level)
        self.assertFalse((self.zone | self.other_zone).recs_scan)
        self.assertEqual(len(self.session.rack_check_ids), 4)
        self.assertEqual(pool.workflow_state, 'first')
        first = pool.first_count_ids
        for count in first:
            self.finish(count, 10)
        for count in pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 10)
        self.assertEqual(pool.workflow_state, 'done')
        self.assertTrue(self.session.scans_completed)
        self.assertEqual(set((self.zone | self.other_zone).mapped('workflow_state')), {'done'})
        self.assertEqual(first.filtered(lambda c: c.emplacement == 'A001').get_lxo3_pivot_data()[0]['stock_total'], 10)
        self.check('post').line_ids.write({'answer': 'ok'})
        for check in self.check('post'):
            check.action_validate()
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Fermeture')
        self.assertTrue(pool.closure_report_html)

    def test_session_locations_restart_preserves_shared_scope(self):
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False, two_racks=True)
        self.session.sess_records.recs_scan.unlink()
        self.session.action_next_flow()
        self.check('pre').line_ids.write({'answer': 'ok'})
        for check in self.check('pre'):
            check.action_validate()
        self._create_location(creator1, 'C102')
        old_pool = self.session._session_scan_pool()
        self.session.action_mod_empl()
        pool = self.session._session_scan_pool()
        self.assertNotEqual(pool, old_pool)
        self.assertTrue(old_pool.flow_archived)
        self.assertEqual(pool.first_count_ids.emplacement, 'C102')
        self.assertFalse(pool.level)
        self.assertEqual(len(self.session.rack_check_ids), 4)
        self.assertFalse(self.session.pre_scan_ready)

    def test_existing_locations_do_not_bypass_pre_scan_validations(self):
        self.to_scan(start_scan=False, two_racks=True)
        existing = self.session.sess_records.recs_scan
        self.session.action_next_flow()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.session.write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'C102', 'branch_id': self.session.team_ids.filtered(lambda t: t.automatic_slot == '1').id})]})
        self.assertEqual(self.session.sess_records.recs_scan, existing)
        self.assertFalse(self.session.pre_scan_ready)

    def test_team_members_share_locations_and_cross_automatically(self):
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        members = []
        for login, slot in [('owner_one', '1'), ('colleague_one', '1'), ('owner_two', '2')]:
            user = self.env['res.users'].create({'name': login, 'login': login,
                'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
            self.env['araymond.flow.assignment'].create({'session_id': self.session.id,
                'role': 'scan' + slot, 'user_id': user.id, 'shift': 'A'})
            members.append(user)
        owner, colleague, other = members
        self.validate('pre', {'answer': 'ok'})
        team1 = self.session.team_ids.filtered(lambda t: t.automatic_slot == '1')
        team2 = self.session.team_ids.filtered(lambda t: t.automatic_slot == '2')
        defaults = self.env['araymond.inventory'].with_user(colleague).with_context(
            manual_location_session_id=self.session.id).default_get(['branch_id'])
        self.assertEqual(defaults['branch_id'], team1.id)
        self.session.with_user(owner).write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'A001'})]})
        outsider = self.env['res.users'].create({'name': 'Sans équipe', 'login': 'location_outsider',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.session.with_user(outsider).write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'X002'})]})
        pool = self.session._session_scan_pool()
        first1 = pool.first_count_ids
        self.assertEqual(first1.branch_id, team1)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.session.with_user(colleague).write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'a001'})]})
        with self.assertRaises(AccessError):
            first1.with_user(other).write({'emplacement': 'A099'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.session.with_user(other).write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'A099', 'branch_id': team1.id})]})
        # Each team may independently include the same physical location.
        self.session.with_user(other).write({'manual_scan_count_ids': [(0, 0, {'emplacement': 'A001'})]})
        first2 = pool.first_count_ids - first1
        self.assertEqual(first2.branch_id, team2)
        first1.with_user(colleague).write({'commentaire': 'Partagé avec mon équipe'})
        self.validate('pre', {'answer': 'ok'})
        self.assertEqual(first1.branch_id, team1)
        self.assertEqual(first2.branch_id, team2)
        with self.assertRaises(AccessError):
            first1.with_user(other)._check_scan()
        def finish_as(count, user):
            self.env['araymond.etiquette'].with_user(user).create({
                'su_id': count.id, 'numEtiq': 'REF;10;LOT;HU', 'typedegalia': 'Manuel'})
            count.with_user(user).action_close()
        finish_as(first1, colleague)
        self.assertEqual(pool.workflow_state, 'first')
        self.assertFalse(pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'))
        finish_as(first2, other)
        cross = pool.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 2)
        self.assertEqual(pool.workflow_state, 'cross')
        self.assertEqual(cross.filtered(lambda c: c.origin_count_id == first1).branch_id, team2)
        self.assertEqual(cross.filtered(lambda c: c.origin_count_id == first2).branch_id, team1)
        for count in cross:
            finish_as(count, colleague if count.branch_id == team1 else other)
        self.assertEqual(pool.workflow_state, 'done')

    def test_first_team_can_scan_before_second_team_creates_locations(self):
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        teams = self.session.team_ids.filtered(lambda t: t.automatic_slot in ('1', '2')).sorted('automatic_slot')
        self._create_location(creator1, 'A001')
        pool = self.session._session_scan_pool()
        self.finish(pool.first_count_ids, 10)
        self.assertEqual(pool.workflow_state, 'first')
        self.assertFalse(pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'))
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_location(creator2, 'TOOLONG')
        self._create_location(creator2, 'B001')
        self.finish(pool.first_count_ids.filtered(lambda c: c.branch_id == teams[1]), 10)
        self.assertEqual(pool.workflow_state, 'cross')
        self.assertEqual(len(pool.recs_scan.filtered(lambda c: c.count_phase == 'cross')), 2)

    def test_shared_location_arbitration_preserves_team_origins(self):
        creator1, creator2 = self._location_creators()
        self.to_scan(start_scan=False)
        self.zone.recs_scan.unlink()
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        for creator in (creator1, creator2):
            self._create_location(creator, 'A001')
        self.validate('pre', {'answer': 'ok'})
        pool = self.session._session_scan_pool()
        for count in pool.first_count_ids:
            self.finish(count, 10)
        for count in pool.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 20)
        thirds = pool.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertEqual(len(thirds), 2)
        self.assertEqual(set(thirds.mapped('count_round')), {1, 2})
        for count in thirds:
            self.finish(count, 30)
        original = pool.first_count_ids[:1]
        original.write({'resolution_comment': 'Vérifier une nouvelle fois'})
        original.action_supervisor_rescan()
        newest = pool.recs_scan.filtered(lambda c: c.count_phase == 'arbitration') - thirds
        self.assertEqual(newest.count_round, 3)
        self.assertEqual(newest.origin_count_id, original)

    def test_each_level_of_same_rack_has_independent_checklists(self):
        self.preparation(two_racks=True)
        self.other_zone.write({'rack': self.zone.rack, 'level': 'N1'})
        self.session._make_checklists()
        self.session._make_checklists()
        checks = self.session.rack_check_ids
        self.assertEqual(len(checks), 4)
        first = checks.filtered(lambda row: row.zone_id == self.zone)
        second = checks.filtered(lambda row: row.zone_id == self.other_zone)
        self.assertEqual(set(first.mapped('phase')), {'pre', 'post'})
        self.assertEqual(set(second.mapped('phase')), {'pre', 'post'})
        self.assertIn('Niveau SOL', first[0].rack_label)
        self.assertIn('Niveau N1', second[0].rack_label)
        self.assertNotEqual(first[0].rack_label, second[0].rack_label)
        self.assertFalse(first.line_ids & second.line_ids)
        self.assertFalse(any(checks.mapped('validated_at')))
        self.assertEqual(len(checks.filtered(lambda row: row.phase == 'pre').line_ids), 20)
        self.assertEqual(len(checks.filtered(lambda row: row.phase == 'post').line_ids), 16)

    def test_rack_checklists_are_independent_and_gate_closure(self):
        self.to_scan(two_racks=True)
        self.check('pre')  # Select the configured scanneurs before signing each rack.
        checks = self.session.rack_check_ids
        self.assertEqual(len(checks), 4)
        pre_a = checks.filtered(lambda c: c.zone_id == self.zone and c.phase == 'pre')
        post_a = checks.filtered(lambda c: c.zone_id == self.zone and c.phase == 'post')
        pre_b = checks.filtered(lambda c: c.zone_id == self.other_zone and c.phase == 'pre')
        post_b = checks.filtered(lambda c: c.zone_id == self.other_zone and c.phase == 'post')
        self.assertEqual(self.zone.workflow_state, 'draft')
        self.assertEqual(self.other_zone.workflow_state, 'draft')
        with self.assertRaises(UserError):
            post_a.line_ids[:1].write({'answer': 'ok'})
        pre_a.line_ids.write({'answer': 'ok'})
        pre_a.action_validate()
        self.assertTrue(self.zone.recs_scan)
        self.assertEqual(self.other_zone.workflow_state, 'draft')
        self.assertFalse(pre_b.validated_at)
        self.finish(self.zone.first_count_ids, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 10)
        post_a.line_ids.write({'answer': 'ok'})
        post_a.action_validate()
        self.assertTrue(post_a.validated_at)
        self.assertFalse(post_b.can_edit)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        pre_b.line_ids.write({'answer': 'ok'})
        pre_b.action_validate()
        self.finish(self.other_zone.first_count_ids, 10)
        self.finish(self.other_zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 10)
        self.assertTrue(post_a.validated_at)
        post_b.line_ids.write({'answer': 'ok'})
        post_b.action_validate()
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Fermeture')

    def test_rack_popup_adds_local_lines(self):
        self.to_scan(start_scan=False)
        self.session.action_next_flow()
        check = self.check('pre')
        with Form(check, view='ar_general_inventory.view_rack_flow_checklist') as form:
            with form.line_ids.new() as line:
                line.description = 'Contrôle du rack ajouté localement'
        self.assertIn('Contrôle du rack ajouté localement', check.line_ids.mapped('description'))

    def test_racks_and_assignments_editable_during_scan(self):
        self.to_scan()
        self.zone.write({'level': 'N2', 'shift': 'B', 'nbrEmp': 3, 'magasinSAP': 'RM01', 'typedegalia': 'RayPro'})
        self.session.write({'entry_shift': 'A', 'control_shift': 'B'})
        self.session.write({'entry_shift': 'C'})
        self.assertEqual(self.zone.level, 'N2')
        self.assertEqual(self.session.entry_shift, 'C')
        messages = '\n'.join(str(message.body) for message in self.session.message_ids)
        self.assertIn('N2', messages)
        self.assertIn('Équipe C', messages)
        self.session._flow_store({'state': 'Fermeture'})
        with self.assertRaises(UserError):
            self.zone.write({'level': 'N3'})
        with self.assertRaises(UserError):
            self.session.write({'control_shift': 'C'})

    def test_transition_checks_every_signature_not_only_timestamp(self):
        from odoo.addons.ar_general_inventory.models.complete_flow import internal
        self.preparation()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        check = self.check('preparation')
        internal(check).write({'validation_votes': {}})
        self.assertTrue(check.validated_at)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        check.action_validate()
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'GoNoGo')

    def test_assigned_person_requires_shift_in_every_team(self):
        from odoo.addons.ar_general_inventory.models.complete_flow import internal
        self.preparation()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        for role in ('entry', 'control', 'sap', 'scan1', 'scan2', 'scan3'):
            line = self.session.assignment_ids.filtered(lambda row: row.role == role)
            if not line:
                line = self.env['araymond.flow.assignment'].create({
                    'session_id': self.session.id, 'role': role,
                    'user_id': self.env.uid, 'shift': 'A',
                })
            with self.assertRaises(ValidationError), self.cr.savepoint():
                line.write({'shift': False})
            internal(line).write({'shift': False})
            with self.assertRaises(ValidationError):
                self.session.action_next_flow()
            line.write({'shift': 'A'})

    def test_go_stage_blocks_inventory_day_until_approved(self):
        self.preparation()
        with self.assertRaises(UserError):
            self.check('go').action_validate()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'GoNoGo')
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        self.assertEqual(self.check('go').go_role_name, 'Responsable magasin')
        self.assertEqual(self.check('go').validator_ids, self.env.user)
        self.validate('go')
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'InventoryDay')

    def test_validation_ignores_deleted_but_requires_new_lines(self):
        self.session.action_next_flow()
        check = self.check('preparation')
        removed = check.line_ids[:1]
        (check.line_ids - removed).write({'answer': 'ok'})
        removed.unlink()
        # Archived lines may still be present through a context or a cached relation.
        check.with_context(active_test=False).action_validate()
        self.assertTrue(check.validated_at)
        line = self.env['araymond.flow.line'].create({
            'checklist_id': check.id, 'description': 'Nouveau contrôle obligatoire',
        })
        self.assertFalse(check.validated_at)
        self.assertFalse(check.validation_votes)
        with self.assertRaises(ValidationError):
            check.with_context(active_test=False).action_validate()
        line.write({'answer': 'ok'})
        check.with_context(active_test=False).action_validate()
        self.assertTrue(check.validated_at)

    def test_deleted_risk_line_is_ignored_and_empty_checklist_is_blocked(self):
        self.session.action_next_flow()
        check = self.check('risk')
        check.line_ids.unlink()
        with self.assertRaises(ValidationError):
            check.with_context(active_test=False).action_validate()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['araymond.flow.line'].create({
                'checklist_id': check.id, 'description': 'Action incomplète',
            })
        line = self.env['araymond.flow.line'].create({
            'checklist_id': check.id, 'description': 'Nouvelle action',
            'responsible_ids': [(6, 0, self.env.user.ids)], 'due_date': fields.Date.today(),
        })
        for values in ({'responsible_ids': [(5, 0, 0)]}, {'due_date': False}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                line.write(values)
        check.with_context(active_test=False).action_validate()
        self.assertTrue(check.validated_at)

    def test_all_three_validators_must_sign(self):
        users = self.env['res.users'].create([
            {'name': 'Validateur %s' % n, 'login': 'multi_validator_%s' % n,
             'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]} for n in range(3)
        ])
        config = self.env['araymond.flow.configuration'].search([('phase', '=', 'preparation')])
        config.write({'responsible_ids': [(6, 0, users.ids)]})
        self.session.action_next_flow()
        check = self.check('preparation')
        check.line_ids.write({'answer': 'ok'})
        with self.assertRaises(AccessError):
            check.action_validate()
        for user in users[:2]:
            check.with_user(user).action_validate()
            self.assertFalse(check.validated_at)
        self.assertIn('En attente', str(check.statuses_html))
        check.with_user(users[2]).action_validate()
        self.assertTrue(check.validated_at)
        self.assertEqual(len(check.validation_votes), 3)
        self.assertNotIn('En attente', str(check.statuses_html))
        check.line_ids[:1].write({'comment': 'À vérifier à nouveau'})
        self.assertFalse(check.validated_at)
        self.assertFalse(check.validation_votes)
        self.assertEqual(str(check.statuses_html).count('En attente'), 3)

    def test_one_discrepancy_recounts_entire_rack(self):
        self.to_scan(two_locations=True)
        first = self.zone.first_count_ids.filtered(lambda c: c.emplacement == 'A001')
        second = self.zone.first_count_ids.filtered(lambda c: c.emplacement == 'A002')
        self.finish(first, 10)
        self.finish(second, 10)
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.finish(cross.filtered(lambda c: c.origin_count_id == first), 10)
        self.finish(cross.filtered(lambda c: c.origin_count_id == second), 5)
        thirds = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertEqual(set(thirds.mapped('emplacement')), {'A001', 'A002'})
        self.assertFalse(first.final_count_id)
        self.finish(thirds.filtered(lambda c: c.origin_count_id == second), 10)
        self.assertEqual(self.zone.workflow_state, 'arbitration')
        self.assertFalse(self.session.scans_completed)
        self.finish(thirds.filtered(lambda c: c.origin_count_id == first), 10)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertTrue(self.session.scans_completed)

    def test_form_saves_new_day_file_in_its_checklist(self):
        self.to_scan(start_scan=False)
        self.session._flow_store({'state': 'InventoryDay'})
        with Form(self.session, view='ar_general_inventory.view_full_flow_session') as form:
            with form.flow_day_ids.new() as line:
                line.file_name = 'COGI'
                line.description = 'Extraction COGI'
        created = self.check('day').line_ids.filtered(lambda line: line.file_name == 'COGI')
        self.assertEqual(len(created), 1)
        self.assertEqual(created.session_id, self.session)
        self.assertFalse(created.template_id)
        self.assertFalse(self.check('day').validated_at)

    def test_lx03_import_finalizes_chatter_without_binary_tracking(self):
        self.to_scan()
        # Reproduce the request finalization that runs after the import button.
        self.env.cr.flush()
        self.assertTrue(self.session.file_loaded)
        self.assertTrue(self.session.lxo3_records)
        self.assertFalse(self.session._fields['lx03_file'].tracking)
        message = self.session.message_ids.filtered(lambda m: 'Chargement LX03' in str(m.body))
        self.assertTrue(message)
        self.assertIn('LX03.xlsx', str(message[:1].body))

    def test_readable_refresh_and_simulation_deletion(self):
        self.preparation()
        self.env['araymond.flow.template'].create({'phase': 'simulation', 'name': 'Contrôle <test>'})
        self.session.action_refresh_flow()
        body = str(self.session.message_ids.filtered(lambda m: 'Actualisation des modèles' in str(m.body))[:1].body)
        self.assertIn('1 ligne(s) ajoutée(s)', body)
        self.assertIn('Contrôle &lt;test&gt;', body)
        self.assertIn('Simulation de stock', body)
        self.assertNotIn('→', body)
        self.session._flow_store({'state': 'Preparation'})
        line = self.check('simulation').line_ids[:1]
        line.write({'warehouse': 'M10', 'sap_warehouse': 'RM01', 'location_count': 0})
        line.unlink()
        body = str(self.session.message_ids.filtered(lambda m: 'Suppression de ligne' in str(m.body))[:1].body)
        self.assertEqual(body.count('<li>'), 3)
        self.assertIn('M10', body)
        self.assertIn('RM01', body)
        self.assertIn('Nombre d’emplacements</strong> : 0', body)
        self.assertNotIn('Description', body)
        self.assertNotIn('→', body)

    def test_deleted_checklist_line_logged_and_not_restored(self):
        self.preparation()
        checklist = self.check('preparation')
        line = checklist.line_ids[:1]
        description, template = line.description, line.template_id
        self.session.write({'flow_preparation_ids': [(2, line.id, 0)]})
        self.assertNotIn(line, checklist.line_ids)
        self.assertFalse(checklist.validated_at)
        self.assertTrue(template.exists())
        message = self.session.message_ids.filtered(lambda m: 'Suppression de ligne' in str(m.body))[:1]
        self.assertIn(description, str(message.body))
        self.assertIn('Préparation', str(message.body))
        self.assertEqual(message.author_id, self.env.user.partner_id)
        self.session.action_refresh_flow()
        self.assertNotIn(template, checklist.line_ids.template_id)
        self.assertFalse(line.with_context(active_test=False).active)
        self.session._flow_store({'state': 'Fermeture'})
        with self.assertRaises(UserError):
            checklist.line_ids[:1].unlink()

    def test_local_line_deletion_and_stage_protection(self):
        with self.assertRaises(UserError):
            self.check('preparation').line_ids[:1].unlink()
        self.session.action_next_flow()
        line = self.env['araymond.flow.line'].create({'checklist_id': self.check('preparation').id, 'description': 'Contrôle ajouté puis retiré'})
        line.unlink()
        self.session.action_refresh_flow()
        self.assertNotIn(line, self.check('preparation').line_ids)

    def test_local_checklist_lines_and_validation_scope(self):
        self.to_scan()
        self.finish(self.zone.first_count_ids, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 10)
        templates = self.env['araymond.flow.template'].search_count([])
        local_count = len(self.session.flow_line_ids.filtered(lambda line: not line.template_id))
        for phase in ('preparation', 'risk', 'simulation', 'day', 'go', 'pre', 'post'):
            checklist = self.check(phase)
            other_validations = {c.id: c.validated_at for c in self.session.flow_check_ids if c != checklist}
            values = {'description': 'Ligne locale ' + phase}
            if phase == 'risk':
                values.update(responsible_ids=[(6, 0, self.env.user.ids)], due_date=fields.Date.today())
            if phase == 'simulation':
                values = {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 2}
            field_name = 'flow_%s_ids' % phase
            self.session.write({field_name: [(0, 0, values)]})
            line = checklist.line_ids.sorted('id')[-1]
            self.assertEqual(line.session_id, self.session)
            self.assertFalse(line.template_id)
            self.assertFalse(checklist.validated_at)
            for other in self.session.flow_check_ids.filtered(lambda c: c != checklist):
                self.assertEqual(other.validated_at, other_validations[other.id])
        self.assertEqual(self.env['araymond.flow.template'].search_count([]), templates)
        self.session.action_refresh_flow()
        self.assertEqual(len(self.session.flow_line_ids.filtered(lambda line: not line.template_id)), local_count + 7)

    def test_local_line_creation_respects_stage_and_closure(self):
        model = self.env['araymond.flow.line']
        values = {'checklist_id': self.check('preparation').id, 'description': 'Contrôle local'}
        with self.assertRaises(UserError):
            model.create(values)
        self.session.action_next_flow()
        user = self.env['res.users'].create({'name': 'Saisie checklist', 'login': 'local_checklist_writer', 'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        line = model.with_user(user).create(values)
        self.assertFalse(line.template_id)
        with self.assertRaises(AccessError):
            model.create(dict(values, is_lx03=True))
        self.session._flow_store({'state': 'Fermeture'})
        with self.assertRaises(UserError):
            model.create(values)

    def test_refresh_updates_modified_templates_and_requires_new_signatures(self):
        self.preparation()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        check = self.check('simulation')
        line = check.line_ids[:1]
        template = line.template_id
        line.write({'comment': 'Commentaire conservé'})
        self.validate('simulation')
        template.write({'warehouse': 'M10', 'sap_warehouse': 'RM01', 'location_count': 2})
        self.session.action_refresh_flow()
        self.assertEqual((line.warehouse, line.sap_warehouse, line.location_count), ('M10', 'RM01', 2))
        self.assertEqual(line.comment, 'Commentaire conservé')
        self.assertFalse(check.validated_at)
        self.assertFalse(check.validation_votes)
        self.assertTrue(self.check('preparation').validated_at)
        self.validate('simulation')
        self.session.action_refresh_flow()
        self.assertTrue(check.validated_at)
        self.assertIn('Actualisation du modèle', str(self.session.message_ids.mapped('body')))
        # A local change survives refresh when the source has not changed again.
        line.write({'location_count': 3})
        self.session.action_refresh_flow()
        self.assertEqual(line.location_count, 3)
        new = self.env['araymond.flow.template'].create({
            'phase': 'simulation', 'name': 'Nouveau contrôle', 'warehouse': 'M10',
            'sap_warehouse': 'RM01', 'location_count': 4})
        self.validate('simulation')
        self.session.action_refresh_flow()
        self.assertIn(new, check.line_ids.template_id)
        self.assertFalse(check.validated_at)

    def test_refresh_preserves_completed_checklist_validation(self):
        self.preparation()
        checklist = self.check('preparation')
        validator, validated_at = checklist.validated_by, checklist.validated_at
        messages = self.session.message_ids
        self.session.action_refresh_flow()
        self.session.action_refresh_flow()
        self.assertEqual(checklist.validated_by, validator)
        self.assertEqual(checklist.validated_at, validated_at)
        self.assertEqual(self.session.message_ids, messages)
        self.env['araymond.flow.template'].create({'phase': 'simulation', 'name': 'Contrôle supplémentaire'})
        self.session.action_refresh_flow()
        self.assertEqual(checklist.validated_by, validator)
        self.assertEqual(checklist.validated_at, validated_at)
        self.assertIn('Contrôle supplémentaire', self.check('simulation').line_ids.mapped('description'))
        checklist.line_ids[:1].write({'comment': 'Correction après validation'})
        self.assertFalse(checklist.validated_at)
        self.assertFalse(checklist.validated_by)

    def test_initial_entries_are_not_chatter_modifications(self):
        self.session.action_next_flow()
        def modifications():
            self.env.flush_all()
            return self.session.message_ids.filtered(lambda message: '<strong>Modification</strong>' in str(message.body))

        initial = modifications()
        self.session.write({'datesession': '2026-10-14 13:00:00'})
        line = self.check('preparation').line_ids[:1]
        line.write({'answer': 'ok', 'comment': 'Premier commentaire'})
        self.assertEqual(modifications(), initial)
        line.write({'answer': 'nok'})
        self.assertEqual(len(modifications() - initial), 1)
        line.write({'answer': 'wait'})
        line.write({'answer': 'ok'})
        self.assertEqual(len(modifications() - initial), 3)
        self.session.write({'datesession': '2026-10-15 13:00:00'})
        self.assertEqual(len(modifications() - initial), 4)
        line.write({'comment': False})
        line.write({'comment': 'Nouveau commentaire'})
        self.assertEqual(len(modifications() - initial), 6)
        self.assertFalse(self.env.ref('ar_general_inventory.menu_flow_approvals').active)

    def test_upgrade_adopts_existing_scan_teams(self):
        teams = self.env['araymond.flow.team.config'].search([])
        expected = {team.slot: (team.id, team.user_ids.ids) for team in teams}
        identifiers = self.env['ir.model.data'].search([
            ('module', '=', 'ar_general_inventory'),
            ('name', 'in', ['flow_config_team_1', 'flow_config_team_2', 'flow_config_team_3']),
        ])
        identifiers.unlink()
        # Reproduce a database whose teams exist without the new XML identifiers.
        from odoo.tools.convert import convert_file
        for _ in range(2):
            convert_file(self.env, 'ar_general_inventory', 'views/complete_flow.xml', {}, mode='update')
            for slot, (record_id, members) in expected.items():
                team = self.env.ref('ar_general_inventory.flow_config_team_%s' % slot)
                self.assertEqual(team.id, record_id)
                self.assertEqual(team.user_ids.ids, members)
            self.assertEqual(self.env['araymond.flow.team.config'].search_count([]), 3)

    def check(self, phase):
        checks = self.session.flow_check_ids.filtered(lambda c: c.phase == phase)
        if phase == 'pre' and self.session.state == 'Scan':
            for check in checks.filtered(lambda c: not c.validation_votes):
                check.write({'scanner%s_ids' % slot: [(6, 0, self.session._scan_assigned_users(slot).ids)]
                             for slot in ('1', '2', '3')})
        return checks

    def validate(self, phase, values=None):
        if values:
            self.check(phase).line_ids.write(values)
        self.approve()
        self.check(phase).action_validate()

    def preparation(self, two_racks=False):
        self.approve()
        self.session.action_next_flow()
        self.session.write({'datesession': fields.Datetime.now(), 'entry_user_ids': [(6, 0, self.env.user.ids)], 'control_user_ids': [(6, 0, self.env.user.ids)]})
        self.zone = self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'magasinPhysique': 'M05', 'magasinSAP': 'RM00',
            'rack': 'A', 'supervisor_id': self.env.uid, 'level': 'SOL', 'shift': 'A', 'nbrEmp': 1,
            'typedegalia': 'Manuel', 'statusSession': True,
        })
        if two_racks:
            self.other_zone = self.env['araymond.invsession'].create({
                'mainsession_id': self.session.id, 'magasinPhysique': 'M05', 'magasinSAP': 'RM00',
                'rack': 'B', 'supervisor_id': self.env.uid, 'level': 'SOL', 'shift': 'A', 'nbrEmp': 1,
                'typedegalia': 'Manuel', 'statusSession': True,
            })
        self.select_all_checklist_zones(self.session)
        self.validate('preparation', {'answer': 'ok'})
        self.validate('risk', {'responsible_id': self.env.uid, 'due_date': fields.Date.today()})

    def attach(self, line, data=b'test', name='fichier.txt'):
        attachment = self.env['ir.attachment'].create({'name': name, 'raw': data, 'res_model': line._name, 'res_id': line.id})
        line.write({'file_name': name, 'attachment_ids': [(6, 0, attachment.ids)]})
        return attachment

    def to_scan(self, start_scan=True, two_locations=False, two_racks=False):
        self.preparation(two_racks=two_racks)
        if two_locations:
            self.zone.write({'nbrEmp': 2})
            self.validate('preparation', {'answer': 'ok'})
            self.validate('risk')
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'GoNoGo')
        self.validate('go', {'answer': 'ok'})
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'InventoryDay')
        book = Workbook()
        book.active.append(['Type de magasin', 'Emplacement', 'Article', 'Lot', 'Stock total', 'Unité de qté base', 'Type unité stock', 'Magasin', 'Unité de stock'])
        book.active.append(['M05', 'A001', 'REF', 'LOT', 10, 'PC', 'BOX', 'RM00', 'HU'])
        if two_locations:
            book.active.append(['M05', 'A002', 'REF', 'LOT', 10, 'PC', 'BOX', 'RM00', 'HU2'])
        if two_racks:
            book.active.append(['M05', 'B001', 'REF', 'LOT', 10, 'PC', 'BOX', 'RM00', 'HU3'])
        stream = BytesIO()
        book.save(stream)
        line = self.env['araymond.flow.line'].create({'checklist_id': self.check('day').id, 'description': 'Extraction COGI'})
        self.attach(line, b'export', 'COGI.txt')
        self.session.write({'lx03_file': base64.b64encode(stream.getvalue()), 'lx03_filename': 'LX03.xlsx', 'lx03_document_number': 'DOC-001'})
        self.approve()
        self.session.action_load_file()
        self.approve()
        self.validate('day')
        self.validate('inventory_day', {'answer': 'ok'})
        for zone in self.session.sess_records.filtered('statusSession'):
            self.env['araymond.inventory'].create([
                {'rec_scan_id': zone.id, 'emplacement': '%s%03d' % (zone.rack, number)}
                for number in range(1, zone.nbrEmp + 1)
            ])
        if not start_scan:
            return
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Scan')
        if not two_racks:
            self.validate('pre', {'answer': 'ok'})
        self.assertEqual(self.session.state, 'Scan')

    def test_lx03_document_number_required_for_import_and_scan(self):
        self.to_scan(start_scan=False)
        for number in (False, '   '):
            self.session.write({'lx03_document_number': number})
            with self.assertRaisesRegex(ValidationError, 'numéro du document LX03'):
                self.session.action_load_file()
            self.validate('day')
            with self.assertRaisesRegex(ValidationError, 'numéro du document LX03'):
                self.session.action_next_flow()
        self.session.write({'lx03_document_number': 'DOC-001'})
        self.validate('day')
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Scan')

    def test_restart_preserves_counts_and_requires_new_validations(self):
        self.to_scan()
        old_zone = self.zone
        old_count = old_zone.first_count_ids[:1]
        self.finish(old_count, 10)
        labels = old_count.su
        previous_votes = dict(self.check('pre').validation_votes)
        self.session.action_mod_empl()
        self.assertEqual(self.session.state, 'Preparation')
        self.assertEqual(self.session.scan_cycle, 2)
        self.assertTrue(old_zone.flow_archived)
        self.assertFalse(old_zone.statusSession)
        self.assertNotIn(old_zone, self.session.sess_records)
        self.assertEqual(old_count.su, labels)
        self.assertTrue(old_count.ok_done)
        self.assertIn('Passage 1', self.session.restart_history_html)
        self.assertIn(labels.numEtiq, self.session.restart_history_html)
        old_check = self.env['araymond.flow.checklist'].search([
            ('zone_id', '=', old_zone.id), ('phase', '=', 'pre')])
        self.assertEqual(old_check.validation_votes, previous_votes)
        self.assertNotIn(old_check, self.session.flow_check_ids)
        self.assertFalse(any(self.session.flow_check_ids.mapped('validated_at')))
        self.assertFalse(any(self.session.flow_check_ids.mapped('validation_votes')))
        new_zone = self.session.sess_records.filtered('statusSession')
        self.assertEqual(new_zone.scan_cycle, 2)
        self.assertEqual(new_zone.workflow_state, 'draft')
        self.assertEqual(set(new_zone.first_count_ids.mapped('emplacement')),
                         set(old_zone.first_count_ids.mapped('emplacement')))
        self.assertFalse(new_zone.recs_scan.su)
        self.assertFalse(any(new_zone.recs_scan.mapped('ok_done')))
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        with self.assertRaises(AccessError):
            old_zone.write({'statusSession': True})
        with self.assertRaises(UserError):
            old_count.write({'commentaire': 'Modification historique'})
        with self.assertRaises(UserError):
            old_count.unlink()
        with self.assertRaises(UserError):
            self.session.action_mod_empl()
        # Redo every validation, then scan exactly the same label in the new cycle.
        self.zone = new_zone
        self.select_all_checklist_zones(self.session)
        self.validate('preparation', {'answer': 'ok'})
        self.validate('risk')
        self.validate('simulation')
        self.session.action_next_flow()
        self.validate('go')
        self.session.action_next_flow()
        self.validate('day')
        self.validate('inventory_day', {'answer': 'ok'})
        self.session.action_next_flow()
        self.validate('pre', {'answer': 'ok'})
        self.finish(new_zone.first_count_ids.filtered(lambda c: c.emplacement == old_count.emplacement), 10)
        self.assertEqual(old_count.su, labels)
        self.assertEqual(new_zone.first_count_ids.su.numEtiq, labels.numEtiq)
        self.assertNotEqual(new_zone.first_count_ids.su.resume, labels.resume)
        self.session.action_mod_empl()
        self.assertEqual(self.session.scan_cycle, 3)
        self.assertIn('Passage 1', self.session.restart_history_html)
        self.assertIn('Passage 2', self.session.restart_history_html)
        self.assertFalse(self.session.sess_records.recs_scan.su)

    def test_restart_preserves_partial_scans_and_admin_only(self):
        self.to_scan()
        count = self.zone.first_count_ids
        label = self.env['araymond.etiquette'].create({
            'su_id': count.id, 'numEtiq': 'REF;7;LOT;HU', 'typedegalia': 'Manuel'})
        user = self.env['res.users'].create({
            'name': 'Opérateur reprise', 'login': 'restart_operator',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)],
        })
        with self.assertRaises(AccessError):
            self.session.with_user(user).action_mod_empl()
        self.assertEqual(self.session.state, 'Scan')
        self.session.action_mod_empl()
        self.assertEqual(count.su, label)
        self.assertFalse(count.ok_done)
        self.assertIn('REF;7;LOT;HU', self.session.restart_history_html)
        self.assertFalse(self.session.sess_records.recs_scan.su)
        with self.assertRaises(UserError):
            label.unlink()
        with self.assertRaises(UserError):
            label.write({'numEtiq': 'REF;8;LOT;HU'})
        with self.assertRaises(AccessError):
            self.session.write({'restart_history_html': '<p>Remplacé</p>'})

    def test_scan_teams_required_only_when_starting_scan(self):
        self.session.assignment_ids.filtered(lambda line: line.role.startswith('scan')).unlink()
        self.to_scan(start_scan=False)
        self.assertEqual(self.session.state, 'InventoryDay')
        with self.assertRaisesRegex(ValidationError, 'Avant de démarrer le scan'):
            self.session.action_next_flow()
        from odoo.addons.ar_general_inventory.models.complete_flow import internal
        assignments = internal(self.env['araymond.flow.assignment'])
        assignments.create([{'session_id': self.session.id, 'role': role, 'user_id': self.env.uid, 'shift': 'A'} for role in ('scan1', 'scan2')])
        with self.assertRaises(ValidationError):
            self.session.action_next_flow()
        assignments.create({'session_id': self.session.id, 'role': 'scan3', 'user_id': self.env.uid, 'shift': 'B'})
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Scan')

    def finish(self, count, qty):
        if count.shared_scan:
            if count.scan_first and not count.location_scanned:
                with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as scan:
                    scan.scan_input = count.emplacement
            if count.ummondatory and not count.um:
                count.write({'um': '6001234567'})
            if count.dummondatory and not count.dum:
                count.write({'dum': '31234567890123456'})
        hu = {'A002': 'HU2', 'B001': 'HU3'}.get(count.emplacement, 'HU')
        self.env['araymond.etiquette'].create({'su_id': count.id, 'numEtiq': 'REF;%s;LOT;%s' % (qty, hu), 'typedegalia': 'Manuel'})
        self.approve()
        count.action_close()
        self.approve()

    def test_creation_form_without_extra_approval(self):
        self.assertEqual(self.session.state, 'Draft')
        self.assertFalse(self.session.datesession)
        self.assertEqual(len(self.session.flow_check_ids), 6)
        self.assertFalse(self.check('export'))
        self.assertFalse(self.env.ref('ar_general_inventory.menu_flow_template_export').active)
        with Form(self.session, view='ar_general_inventory.view_full_flow_session') as form:
            form.desc = 'Description modifiée'
        self.assertGreaterEqual(len(self.session.approval_ids), 2)
        self.assertTrue(self.session.message_ids)
        self.assertTrue(all(a.state == 'recorded' for a in self.session.approval_ids))
        body = str(self.session.message_ids.filtered(lambda m: 'Création de session' in str(m.body))[:1].body)
        self.assertIn('Description', body)
        self.assertNotIn('"desc"', body)
        self.assertNotIn('<pre>', body)
        self.approve()
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Preparation')

    def test_full_agreement_and_post_scan_gate(self):
        self.to_scan()
        self.finish(self.zone.first_count_ids, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 10)
        self.assertTrue(self.session.scans_completed)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        self.validate('post', {'answer': 'ok'})
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Fermeture')
        self.assertTrue(self.session.closure_report_html)

    def test_third_matches_first_automatically(self):
        self.to_scan()
        self.finish(self.zone.first_count_ids, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 12)
        third = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.finish(third, 10)
        self.assertEqual(self.zone.first_count_ids.final_count_id, third)
        self.assertFalse(self.session.unresolved_count_ids)
        self.assertEqual(len(self.session.all_count_ids), 3)

    def test_three_different_requires_supervisor(self):
        self.to_scan()
        first = self.zone.first_count_ids
        self.finish(first, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 12)
        third = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.finish(third, 11)
        self.assertTrue(first.resolution_needed)
        self.assertFalse(first.final_count_id)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        first.write({'supervisor_choice_id': third.id, 'resolution_comment': 'Quantité vérifiée sur place'})
        self.approve()
        first.action_supervisor_accept()
        self.assertEqual(first.final_count_id, third)
        self.assertEqual(first.resolved_by, self.env.user)
        self.assertEqual(self.zone.workflow_state, 'done')

    def test_rescan_keeps_all_previous_results(self):
        self.to_scan()
        first = self.zone.first_count_ids
        self.finish(first, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 12)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'), 11)
        first.write({'resolution_comment': 'Nouveau comptage supervisé'})
        self.approve()
        first.action_supervisor_rescan()
        new = self.zone.recs_scan.filtered(lambda c: c.count_round == 2)
        self.assertEqual(len(new), 1)
        self.finish(new, 10)
        self.assertEqual(first.final_count_id, new)
        self.assertEqual(len(self.zone.recs_scan), 4)

    def test_modification_invalidates_and_wait_blocks(self):
        self.approve()
        self.session.action_next_flow()
        self.validate('preparation', {'answer': 'ok'})
        line = self.check('preparation').line_ids
        line.action_wait()
        self.assertFalse(self.check('preparation').validated_at)
        self.approve()
        with self.assertRaises(ValidationError):
            self.check('preparation').action_validate()
        line.action_nok()
        self.approve()
        with self.assertRaises(ValidationError):
            self.check('preparation').action_validate()

    def test_cannot_forge_approval_or_stage(self):
        with self.assertRaises(AccessError):
            self.session.write({'state': 'Scan'})
        with self.assertRaises(AccessError):
            self.session.approval_ids.write({'state': 'approved'})
        with self.assertRaises(AccessError):
            self.session.with_context(_ar_flow_token=True).write({'state': 'Scan'})
        outsider = self.env['res.users'].create({'name': 'Sans validation', 'login': 'flow_outsider', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.session.approval_ids.with_user(outsider).action_approve()

    def test_historical_rejection_does_not_block(self):
        from ..models.complete_flow import internal
        request = self.session.approval_ids
        internal(request).write({'state': 'rejected', 'reason': 'Ancienne demande'})
        self.session.desc = 'Description précise'
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Preparation')
        self.assertTrue(all(item.state == 'rejected' for item in request))

    def test_parameter_changes_do_not_need_approval(self):
        self.approve()
        template = self.env['araymond.flow.template'].search([('phase', '=', 'risk')], limit=1)
        template.name = 'Risques à contrôler par rack'
        self.approve()
        self.session.action_next_flow()
        self.assertTrue(template.message_ids)

    def test_lx03_cannot_be_changed_after_scan_start(self):
        self.to_scan()
        with self.assertRaises(UserError):
            self.session.write({'lx03_file': base64.b64encode(b'replacement')})
        with self.assertRaises(UserError):
            self.session.write({'lx03_document_number': 'Autre document'})
        with self.assertRaises(UserError):
            self.session.action_load_file()

    def test_inventory_day_checklist_independent_all_validators(self):
        self.to_scan(start_scan=False)
        check = self.check('inventory_day')
        extraction = self.check('day')
        extraction_date = extraction.validated_at
        reviewer = self.env['res.users'].create({'name': 'Responsable du jour', 'login': 'day_reviewer',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)]})
        self.env.ref('ar_general_inventory.flow_configuration_inventory_day').write({
            'responsible_ids': [(6, 0, [self.env.uid, reviewer.id])]})
        check.line_ids.write({'answer': 'wait'})
        with self.assertRaises(ValidationError):
            check.action_validate()
        check.line_ids.write({'answer': 'ok'})
        check.action_validate()
        self.assertFalse(check.validated_at)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        self.assertEqual(extraction.validated_at, extraction_date)
        check.with_user(reviewer).action_validate()
        self.session.action_next_flow()
        self.assertEqual(self.session.state, 'Scan')

    def test_independent_lx03_replacement_and_legacy_migration(self):
        from pathlib import Path
        from runpy import run_path
        from ..models.complete_flow import internal
        self.to_scan(start_scan=False)
        self.assertFalse(self.check('day').line_ids.filtered('is_lx03'))
        self.assertEqual(self.session.lx03_document_number, 'DOC-001')
        self.assertTrue(self.session.file_loaded)
        self.assertTrue(self.session.lxo3_records)
        old_file = self.session.lx03_file
        old = internal(self.env['araymond.flow.line']).create({
            'checklist_id': self.check('day').id, 'description': 'Ancien LX03', 'is_lx03': True})
        attachment = self.env['ir.attachment'].create({'name': 'ancien.xlsx', 'raw': b'ancien',
                                                     'res_model': old._name, 'res_id': old.id})
        internal(old).write({'attachment_ids': [(6, 0, attachment.ids)]})
        migrate = run_path(str(Path(__file__).resolve().parents[1] / 'migrations/19.0.4.6.0/post-migrate.py'))['migrate']
        migrate(self.cr, '19.0.4.5.0')
        self.assertFalse(old.active)
        self.assertEqual(old.attachment_ids, attachment)
        self.assertEqual(self.session.lx03_file, old_file)
        self.session.write({'lx03_file': base64.b64encode(b'invalid workbook')})
        self.assertFalse(self.session.file_loaded)
        self.assertFalse(self.check('day').validated_at)
        self.assertTrue(self.check('preparation').validated_at)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.session.action_load_file()

    def test_operator_membership_is_enforced(self):
        self.to_scan()
        outsider = self.env['res.users'].create({'name': 'Sans équipe', 'login': 'flow_no_team', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.zone.first_count_ids.with_user(outsider).action_view_form_inv()

    def test_scan_configuration_is_independent_from_entry_and_control(self):
        self.to_scan()
        scanner = self.env['res.users'].create({'name': 'Scanner dédié', 'login': 'flow_dedicated_scanner',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        count = self.zone.first_count_ids
        assignment = self.session.assignment_ids.filtered(lambda line: line.role == 'scan' + count.branch_id.automatic_slot)
        assignment.write({'user_id': scanner.id})
        self.validate('pre')
        self.assertNotIn(scanner, self.session.entry_user_ids | self.session.control_user_ids)
        count.with_user(scanner)._check_scan()
        # The original member remains authorized by the checklist selection,
        # independently of the session assignment that was replaced.
        count._check_scan()
        count.with_user(scanner).write({'emplacementVide': True})
        count.with_user(scanner).action_close()
        self.assertTrue(count.ok_done)

    def test_checklist_configuration_controls_approval(self):
        reviewer = self.env['res.users'].create({'name': 'Validation préparation', 'login': 'flow_phase_reviewer',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        config = self.env.ref('ar_general_inventory.flow_configuration_preparation')
        config.write({'responsible_ids': [(6, 0, reviewer.ids)]})
        self.session.action_next_flow()
        self.check('preparation').line_ids.write({'answer': 'ok'})
        self.check('preparation').with_user(reviewer).action_validate()
        self.assertEqual(self.check('preparation').validated_by, reviewer)
        with self.assertRaises(AccessError):
            self.session.with_user(reviewer)._flow_check_approver('risk')

    def test_original_checklists_and_go_labels(self):
        self.assertEqual(len(self.check('preparation').line_ids), 18)
        self.to_scan()
        self.assertEqual(len(self.check('pre').line_ids), 10)
        self.assertEqual(len(self.check('post').line_ids), 8)
        self.assertFalse(self.check('go').line_ids)
        self.assertTrue(self.check('go').validated_at)

    def test_go_functions_and_validators_from_settings(self):
        self.preparation()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        self.session.action_next_flow()
        check = self.check('go')
        reviewer = self.env['res.users'].create({
            'name': 'Responsable GO', 'login': 'go_reviewer',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)],
        })
        with self.assertRaises(AccessError):
            check.write({'validator_ids': [(6, 0, reviewer.ids)]})
        with self.assertRaises(AccessError):
            self.go_role.with_user(reviewer).write({'name': 'Modification interdite'})
        with Form(self.env.ref('ar_general_inventory.flow_configuration_go'),
                  view='ar_general_inventory.view_go_responsibles_configuration_form') as form:
            with form.go_responsible_ids.edit(0) as row:
                row.name = 'Responsable logistique'
                row.validator_ids.add(reviewer)
        self.assertEqual(check.go_role_name, 'Responsable logistique')
        check.action_validate()
        self.assertFalse(check.validated_at)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        check.with_user(reviewer).action_validate()
        self.assertTrue(check.validated_at)
        second = self.env['araymond.go.responsible'].create({
            'name': 'Responsable qualité', 'configuration_id': self.go_role.configuration_id.id,
            'validator_ids': [(6, 0, reviewer.ids)],
        })
        second_check = self.session.go_check_ids - check
        self.assertEqual(len(self.session.go_check_ids), 2)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        with self.assertRaises(AccessError):
            second_check.action_validate()
        second_check.with_user(reviewer).action_validate()
        self.go_role.write({'validator_ids': [(6, 0, self.env.user.ids)]})
        self.assertFalse(check.validated_at)
        self.assertFalse(check.validation_votes)
        self.assertTrue(second_check.validated_at)
        check.action_validate()
        self.session.action_next_flow()
        self.session._flow_store({'state': 'Fermeture'})
        self.go_role.write({'name': 'Nouvelle fonction', 'validator_ids': [(6, 0, reviewer.ids)]})
        self.assertEqual(check.go_role_name, 'Responsable logistique')
        self.assertEqual(check.validator_ids, self.env.user)
        self.assertTrue(check.validated_at)
        second.active = False
        self.assertIn(second_check, self.session.go_check_ids)

    def test_go_empty_configuration_and_archiving_block_launch(self):
        self.preparation()
        self.validate('simulation', {'warehouse': 'M05', 'sap_warehouse': 'RM00', 'location_count': 1})
        self.session.action_next_flow()
        check = self.check('go')
        check.action_validate()
        self.go_role.active = False
        self.assertFalse(self.session.go_check_ids)
        with self.assertRaises(UserError):
            self.session.action_next_flow()
        with self.assertRaises(UserError):
            check.action_validate()
        self.go_role.active = True
        self.assertEqual(self.session.go_check_ids, check)
        self.assertFalse(check.validated_at)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.go_role.write({'validator_ids': [(5, 0, 0)]})

    def test_upgrade_preserves_risk_responsible(self):
        from pathlib import Path
        from runpy import run_path
        self.preparation()
        line = self.check('risk').line_ids
        line.write({'responsible_id': self.env.uid, 'due_date': fields.Date.today()})
        self.env.flush_all()
        migration = run_path(str(Path(__file__).resolve().parents[1] / 'migrations' / '19.0.4.1.0' / 'post-migrate.py'))['migrate']
        migration(self.cr, '19.0.4.0.2')
        migration(self.cr, '19.0.4.0.2')
        line.invalidate_recordset(['responsible_ids'])
        self.assertEqual(line.responsible_ids, self.env.user)

    def test_historical_session_keeps_legacy_flow(self):
        from odoo.addons.ar_general_inventory.models.session_preparation import PreparedSession
        historical = PreparedSession.create(self.env['araymond.maininvsession'], [{'desc': 'Historique', 'datesession': fields.Datetime.now(), 'full_flow': False}])
        self.assertFalse(historical.full_flow)
        self.assertTrue(historical.preparation_workflow)
        historical.checklist_ids.filtered(lambda c: c.phase == 'preparation').line_ids.write({'answer': 'ok'})
        historical.action_validate_preparation()
        self.assertEqual(historical.state, 'Racks')

    def independent_admin(self):
        return self.env['res.users'].create({
            'name': 'Administrateur sans affectation', 'login': 'flow_independent_admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_system').id])],
        })

    def test_unassigned_admin_approves_and_validates(self):
        admin = self.independent_admin()
        self.assertFalse(self.env['araymond.inventory.approver'].search_count([('user_ids', 'in', admin.ids)]))
        requests = self.env['araymond.flow.approval'].search([('state', 'in', ['pending', 'rejected'])])
        requests.with_user(admin).action_approve()
        self.assertTrue(all(r.state == 'approved' for r in requests))
        self.session.action_next_flow()
        checklist = self.check('preparation')
        with self.assertRaises(AccessError):
            checklist.with_user(admin).action_validate()
        checklist.line_ids.write({'answer': 'ok'})
        self.session.approval_ids.filtered(lambda r: r.state != 'approved').with_user(admin).action_approve()
        with self.assertRaises(AccessError):
            checklist.with_user(admin).action_validate()
        self.assertFalse(checklist.validated_at)
        self.session.with_user(admin)._flow_check_approver('preparation')

    def test_only_rack_responsible_can_resolve_alert_and_chatter_is_traced(self):
        self.to_scan()
        first = self.zone.first_count_ids
        self.finish(first, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 12)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'), 11)
        self.assertFalse(self.env.ref('ar_general_inventory.group_validate_supervisor_alert', raise_if_not_found=False))
        user = self.env['res.users'].create({
            'name': 'Responsable désigné', 'login': 'rack_alert_owner',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)],
        })
        with self.assertRaises(AccessError):
            first.with_user(user).write({'resolution_comment': 'Interdit'})
        self.zone.supervisor_id = user
        self.assertFalse(first.can_resolve_alert)
        self.assertTrue(first.with_user(user).can_resolve_alert)
        with self.assertRaises(AccessError):
            first.action_supervisor_rescan()
        with self.assertRaises(AccessError):
            first.action_supervisor_accept()
        first.with_user(user).write({'supervisor_choice_id': first.id, 'resolution_comment': 'Résultat vérifié <test>'})
        first.with_user(user).action_supervisor_accept()
        self.assertEqual(first.resolved_by, user)
        self.assertTrue(first.resolved_at)
        self.assertFalse(first.resolution_needed)
        message = self.session.message_ids.filtered(lambda row: 'Alerte validée' in str(row.body))[:1]
        self.assertEqual(message.author_id, user.partner_id)
        for value in ('Responsable désigné', 'Niveau', 'SOL', first.emplacement, 'Date', 'Résultat', 'Justification', '&lt;test&gt;'):
            self.assertIn(value, str(message.body))

    def test_unassigned_admin_cannot_arbitrate(self):
        self.to_scan()
        first = self.zone.first_count_ids
        self.finish(first, 10)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'), 12)
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'), 11)
        admin = self.independent_admin()
        first.write({'supervisor_choice_id': first.id, 'resolution_comment': 'Décision responsable'})
        with self.assertRaises(AccessError):
            first.with_user(admin).action_supervisor_accept()
        with self.assertRaises(AccessError):
            first.with_user(admin).action_supervisor_rescan()
        first.action_supervisor_accept()
        self.assertEqual(first.resolved_by, self.zone.supervisor_id)
