from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
from .common import legacy_session


@tagged('post_install', '-at_install')
class TestTeamWorkflow(TransactionCase):
    def setUp(self):
        super().setUp()
        self.session = legacy_session(self.env, {
            'desc': 'Test équipes', 'typesession': 'InvGen',
            'datesession': fields.Datetime.now(), 'state': 'Scan',
        })
        self.users = self.env['res.users'].create([
            {'name': 'Opérateur %s' % n, 'login': 'test_inventory_operator_%s' % n,
             'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]}
            for n in range(3)
        ])
        self.teams = self.env['araymond.inventory.team'].create([
            {'name': 'Équipe %s' % n, 'session_id': self.session.id, 'user_ids': [(6, 0, [user.id])], 'sequence': n}
            for n, user in enumerate(self.users)
        ])
        self.zone = self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'supervisor_id': self.env.uid,
            'magasinPhysique': 'M05', 'magasinSAP': 'RM00', 'rack': 'A',
            'nbrEmp': 2, 'statusSession': True, 'automatic_teams': False,
        })
        self.counts = self.env['araymond.inventory'].create([
            {'rec_scan_id': self.zone.id, 'emplacement': 'A00%s' % n,
             'branch_id': self.teams[n].id, 'typedegalia': 'Manuel'}
            for n in range(2)
        ])
        self.zone.action_prepare_checklist()

    def approve(self):
        self.zone.checklist_ids.write({'answer': 'ok'})
        self.zone.action_validate_checklist()

    def test_session_closes_only_after_all_zones_and_reopens(self):
        second = self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'supervisor_id': self.env.uid,
            'magasinPhysique': 'M05', 'magasinSAP': 'RM00', 'rack': 'B',
            'nbrEmp': 2, 'automatic_teams': False,
        })
        with self.assertRaises(UserError):
            self.zone.action_close_invsession()
        self.env['araymond.inventory'].create([
            {'rec_scan_id': second.id, 'emplacement': 'B00%s' % n,
             'branch_id': self.teams[n].id, 'typedegalia': 'Manuel'} for n in range(2)
        ])
        self.zone.checklist_ids.write({'answer': 'ok'})
        self.zone.action_validate_checklist()
        second.checklist_ids.write({'answer': 'ok'})
        second.action_validate_checklist()
        for zone in self.zone | second:
            for count in zone.first_count_ids:
                self.finish(count)
            for count in zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
                self.finish(count)
        self.zone.action_close_invsession()
        self.assertEqual(self.session.state, 'Scan')
        second.action_close_invsession()
        self.assertEqual(self.session.state, 'Fermeture')
        second.write({'state': 'Scan'})
        self.assertEqual(self.session.state, 'Scan')

    def label(self, count, quantity=10):
        return self.env['araymond.etiquette'].with_user(count.branch_id.user_ids[0]).create({
            'su_id': count.id, 'numEtiq': 'REF;%s;LOT;HU%s' % (quantity, count.emplacement),
            'typedegalia': 'Manuel',
        })

    def finish(self, count, quantity=10):
        self.label(count, quantity)
        count.with_user(count.branch_id.user_ids[0]).action_close()

    def test_checklist_gate_and_nok(self):
        with self.assertRaises(UserError):
            self.counts[0].with_user(self.users[0]).action_view_form_inv()
        with self.assertRaises(UserError):
            self.label(self.counts[0])
        with self.assertRaises(ValidationError):
            self.zone.action_validate_checklist()
        self.zone.checklist_ids.write({'answer': 'ok'})
        self.zone.checklist_ids[0].write({'answer': 'nok', 'comment': '   '})
        with self.assertRaises(ValidationError):
            self.zone.action_validate_checklist()
        self.zone.checklist_ids[0].write({'comment': 'Mesure compensatoire appliquée'})
        with self.assertRaises(AccessError):
            self.zone.with_user(self.users[0]).action_validate_checklist()
        self.zone.action_validate_checklist()
        self.assertEqual(self.zone.validated_by, self.env.user)
        self.assertTrue(self.zone.validated_at)
        with self.assertRaises(UserError):
            self.zone.checklist_ids[0].write({'answer': 'ok'})

    def test_rotation_agreement_and_sap(self):
        self.approve()
        self.finish(self.counts[0])
        self.assertEqual(self.zone.workflow_state, 'first')
        self.finish(self.counts[1])
        self.assertEqual(self.zone.workflow_state, 'cross')
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 2)
        for count in cross:
            self.assertNotEqual(count.branch_id, count.origin_count_id.branch_id)
            self.finish(count)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertEqual(self.counts.mapped('final_count_id'), self.counts)
        self.assertIn('SAP', self.zone.reconciliation)
        self.zone.action_close_invsession()
        self.assertEqual(self.zone.state, 'Fermeture')

    def test_arbitration_only_discrepancies_and_final_result(self):
        self.approve()
        for count in self.counts:
            self.finish(count)
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross').sorted('emplacement')
        self.finish(cross[0], 12)
        self.finish(cross[1], 10)
        self.assertEqual(self.zone.workflow_state, 'arbitration')
        self.assertFalse(self.counts[0].final_count_id)
        with self.assertRaises(UserError):
            self.zone.action_close_invsession()
        self.zone.arbitration_team_id = self.teams[0]
        with self.assertRaises(ValidationError):
            self.zone.action_start_arbitration()
        self.zone.arbitration_team_id = self.teams[2]
        self.zone.action_start_arbitration()
        self.zone.action_start_arbitration()
        arbitration = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertEqual(len(arbitration), 1)
        self.finish(arbitration, 11)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertEqual(self.counts[0].final_count_id, arbitration)
        self.assertEqual(arbitration._quantities(), {('REF', 'LOT'): 11.0})
        self.assertIn('final 11', self.zone.reconciliation)

    def test_team_and_completed_count_protection(self):
        self.approve()
        with self.assertRaises(AccessError):
            self.counts[0].with_user(self.users[1]).action_view_form_inv()
        with self.assertRaises(AccessError):
            self.counts[0].write({'ok_done': True})
        self.finish(self.counts[0])
        with self.assertRaises(UserError):
            self.label(self.counts[0])
        with self.assertRaises(UserError):
            self.counts[0].su.with_user(self.users[0]).unlink()

    def test_duplicate_label_and_empty_locations(self):
        self.approve()
        self.label(self.counts[0])
        with self.assertRaises(ValidationError):
            self.label(self.counts[0])
        self.counts[0].with_user(self.users[0]).action_close()
        empty = self.counts[1].with_user(self.users[1])
        with self.assertRaises(ValidationError):
            empty.action_close()
        empty.write({'emplacementVide': True})
        empty.action_close()
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        for count in cross:
            if count.origin_count_id == self.counts[1]:
                count.with_user(count.branch_id.user_ids[0]).write({'emplacementVide': True})
                count.with_user(count.branch_id.user_ids[0]).action_close()
            else:
                self.finish(count)
        self.assertEqual(self.zone.workflow_state, 'done')

    def test_three_initial_teams_rotate(self):
        self.zone.write({'nbrEmp': 3})
        self.env['araymond.inventory'].create({'rec_scan_id': self.zone.id, 'emplacement': 'A002', 'branch_id': self.teams[2].id, 'typedegalia': 'Manuel'})
        self.approve()
        for count in self.zone.first_count_ids:
            self.finish(count)
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 3)
        self.assertEqual(len(cross.branch_id), 3)
        for count in cross:
            self.assertNotEqual(count.branch_id, count.origin_count_id.branch_id)

    def test_cannot_forge_validation(self):
        with self.assertRaises(AccessError):
            self.zone.write({'checklist_validated': True})
        with self.assertRaises(AccessError):
            self.counts[0].write({'final_count_id': self.counts[0].id})

    def test_manual_scan_form(self):
        self.approve()
        self.env['araymond.ref'].create({'reference': 'REF'})
        count = self.counts[0].with_user(self.users[0])
        with Form(count, view='ar_general_inventory.view_araymond_inventory_form') as form:
            form.ref = 'REF'
            form.qty = '10'
            form.lot = 'LOT'
            form.numEtiquet = 'HU_FORM'
        count.action_manuel_save()
        self.assertEqual(count._quantities(), {('REF', 'LOT'): 10.0})
        count.action_close()
        self.assertTrue(count.ok_done)

    def test_sap_final_uses_arbitration(self):
        self.env['araymond.lxo3'].create({'session_id': self.session.id, 'type_magasin': 'M05', 'emplacement': 'A000', 'article': 'REF', 'lot': 'LOT', 'stock_total': 11})
        self.approve()
        for count in self.counts:
            self.finish(count)
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 12 if count.emplacement == 'A000' else 10)
        self.zone.arbitration_team_id = self.teams[2]
        self.zone.action_start_arbitration()
        self.finish(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'), 11)
        with self.assertRaises(UserError) as caught:
            self.counts[0].action_show_ecarts_sapvs1scan()
        self.assertIn("11.0", str(caught.exception))
        self.assertIn('Non scanné: []', str(caught.exception))
