from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from .common import legacy_session


@tagged('post_install', '-at_install')
class TestAutomaticTeams(TransactionCase):
    def setUp(self):
        super().setUp()
        self.session = legacy_session(self.env, {'typesession': 'InvGen', 'datesession': fields.Datetime.now()})
        self.zone = self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'supervisor_id': self.env.uid,
            'magasinPhysique': 'M05', 'magasinSAP': 'RM00', 'rack': 'A',
            'nbrEmp': 3, 'typedegalia': 'Manuel',
        })
        self.counts = self.env['araymond.inventory'].create([
            {'rec_scan_id': self.zone.id, 'emplacement': 'A00%s' % number} for number in range(1, 4)
        ])
        self.operator = self.env['res.users'].create({'name': 'Opérateur sans affectation', 'login': 'auto_inventory_operator', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})

    def start(self):
        self.zone.checklist_ids.action_answer_ok()
        self.zone.action_validate_checklist()

    def finish(self, count, qty=10):
        self.env['araymond.etiquette'].with_user(self.operator).create({
            'su_id': count.id, 'numEtiq': 'REF;%s;LOT;HU%s' % (qty, count.emplacement), 'typedegalia': 'Manuel',
        })
        count.with_user(self.operator).action_close()

    def test_defaults_and_balanced_rotation_without_members(self):
        self.assertEqual(self.session.team_ids.mapped('automatic_slot'), ['1', '2'])
        self.assertFalse(self.session.team_ids.user_ids)
        self.assertFalse(self.counts.branch_id)
        self.assertEqual(set(self.counts.mapped('typedegalia')), {'Manuel'})
        self.start()
        self.assertEqual(self.counts.mapped('branch_id.automatic_slot'), ['1', '2'])
        self.assertEqual(len(self.counts.filtered(lambda c: c.branch_id.automatic_slot == '1')), 2)
        for count in self.counts:
            self.finish(count)
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(len(cross), 3)
        for count in cross:
            self.assertNotEqual(count.branch_id, count.origin_count_id.branch_id)
            self.finish(count)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertEqual(len(self.session.team_ids), 2)

    def test_automatic_third_team_only_for_discrepancy(self):
        self.start()
        for count in self.counts:
            self.finish(count)
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 12 if count.emplacement == 'A001' else 10)
        self.assertEqual(self.zone.workflow_state, 'arbitration')
        self.assertEqual(len(self.session.team_ids), 3)
        arbitration = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertEqual(len(arbitration), 3)
        self.assertEqual(arbitration.branch_id.automatic_slot, '3')
        self.zone.action_start_arbitration()
        self.assertEqual(len(self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')), 3)
        for count in arbitration:
            self.finish(count, 11)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertEqual(arbitration.origin_count_id.final_count_id, arbitration)

    def test_galia_is_common_and_locked_after_start(self):
        self.zone.write({'typedegalia': 'RayPro'})
        self.assertEqual(set(self.counts.mapped('typedegalia')), {'RayPro'})
        self.start()
        with self.assertRaises(UserError):
            self.zone.write({'typedegalia': 'Manuel'})
        with self.assertRaises(UserError):
            self.counts[0].with_user(self.operator).write({'typedegalia': 'Manuel'})

    def test_single_location_still_has_independent_cross_team(self):
        self.counts[1:].unlink()
        self.zone.write({'nbrEmp': 1})
        self.start()
        self.finish(self.counts[0])
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        self.assertEqual(cross.branch_id.automatic_slot, '2')
        self.finish(cross)
        self.assertEqual(self.zone.workflow_state, 'done')
