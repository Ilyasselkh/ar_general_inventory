import base64
from io import BytesIO
from openpyxl import Workbook

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
from ..models.session_preparation import PreparedSession


@tagged('post_install', '-at_install')
class TestSessionPreparation(TransactionCase):
    def test_new_form_uses_description_only_before_preparation(self):
        with Form(self.env['araymond.maininvsession'], view='ar_general_inventory.view_full_flow_session') as form:
            self.assertEqual(form.state, 'Draft')
            form.desc = 'Nouvelle session'
        self.assertFalse(form.record.datesession)
        self.assertTrue(form.record.full_flow)

    def test_unsaved_preparation_answers_survive_creation(self):
        model = self.env['araymond.maininvsession'].with_user(self.users[0])
        values = PreparedSession.default_get(model, ['preparation_workflow', 'preparation_line_ids'])
        values.update(datesession=fields.Datetime.now(), desc='Réponses avant sauvegarde')
        values['preparation_line_ids'][0][2].update(answer='nok', comment='À vérifier', responsible_id=self.users[0].id)
        session = PreparedSession.create(model, [dict(values, full_flow=False)])
        line = session.preparation_line_ids.sorted('sequence')[0]
        self.assertEqual(line.answer, 'nok')
        self.assertEqual(line.comment, 'À vérifier')
        self.assertEqual(line.responsible_id, self.users[0])
        self.assertEqual(len(session.preparation_line_ids), 18)

    def setUp(self):
        super().setUp()
        self.users = self.env['res.users'].create([
            {'name': 'Preparation %s' % n, 'login': 'preparation_test_%s' % n,
             'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]}
            for n in range(4)
        ])
        self.env['araymond.inventory.approver'].create({'user_ids': [(6, 0, self.users[0].ids)]})
        # Existing 3.x sessions retain their own lifecycle after the upgrade.
        self.session = PreparedSession.create(self.env['araymond.maininvsession'], [{
            'datesession': fields.Datetime.now(), 'desc': 'Inventaire de test', 'full_flow': False,
        }])

    def approve(self, phase):
        checklist = self.session.checklist_ids.filtered(lambda c: c.phase == phase)
        checklist.line_ids.write({'answer': 'ok'})
        checklist.with_user(self.users[0]).action_validate()

    def prepare(self, assign_third=True):
        self.approve('preparation')
        self.zone = self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'supervisor_id': self.env.uid,
            'magasinPhysique': 'M05', 'magasinSAP': 'RM00', 'rack': 'A',
            'nbrEmp': 2, 'level': 'SOL', 'typedegalia': 'Manuel',
            'scan1_user_ids': [(6, 0, self.users[1].ids)],
            'scan2_user_ids': [(6, 0, self.users[2].ids)],
            'scan3_user_ids': [(6, 0, self.users[3].ids if assign_third else [])],
        })
        if not assign_third:
            return
        self.session.action_confirm_racks()
        self.approve('day')

    def load(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Type de magasin', 'Emplacement', 'Article', 'Lot', 'Stock total',
                      'Unité de qté base', 'Type unité stock', 'Magasin', 'Unité de stock'])
        for location in ('A001', 'A002'):
            sheet.append(['M05', location, 'REF', 'LOT', 10, 'PC', '', 'RM00', 'HU' + location])
        stream = BytesIO()
        workbook.save(stream)
        self.session.write({'lx03_file': base64.b64encode(stream.getvalue()), 'lx03_filename': 'LX03.xlsx'})
        self.session.action_load_file()
        self.session.action_validate_lx03()

    def finish(self, count, quantity=10, operator=None):
        users = {'1': self.users[1], '2': self.users[2], '3': self.users[3]}
        operator = operator or users[count.branch_id.automatic_slot]
        self.env['araymond.etiquette'].with_user(operator).create({
            'su_id': count.id, 'numEtiq': 'REF;%s;LOT;HU%s' % (quantity, count.emplacement),
            'typedegalia': 'Manuel',
        })
        count.with_user(operator).action_close()

    def test_approval_permissions_order_and_locking(self):
        self.assertEqual(self.session.state, 'Preparation')
        self.assertEqual(len(self.session.preparation_line_ids), 18)
        self.assertEqual(len(self.session.day_line_ids), 10)
        self.assertEqual(len(self.session.post_line_ids), 8)
        with self.assertRaises(UserError):
            self.session.action_confirm_racks()
        self.session.preparation_line_ids.write({'answer': 'ok'})
        with self.assertRaises(AccessError):
            self.session.action_validate_preparation()
        self.session.with_user(self.users[0]).action_validate_preparation()
        with self.assertRaises(UserError):
            self.session.preparation_line_ids.write({'answer': 'nok'})
        with self.assertRaises(AccessError):
            self.session.write({'state': 'Scan'})

    def test_nok_requires_comment(self):
        self.session.preparation_line_ids.write({'answer': 'ok'})
        self.session.preparation_line_ids[:1].write({'answer': 'nok'})
        with self.assertRaises(ValidationError):
            self.session.with_user(self.users[0]).action_validate_preparation()
        self.session.preparation_line_ids[:1].write({'comment': 'Dérogation documentée'})
        self.session.with_user(self.users[0]).action_validate_preparation()
        self.assertEqual(self.session.state, 'Racks')

    def test_multiple_attachments_and_validation_lock(self):
        line = self.session.preparation_line_ids[0].with_user(self.users[0])
        attachments = self.env['ir.attachment'].with_user(self.users[0]).create([
            {'name': name, 'raw': b'Checklist evidence',
             'res_model': line._name, 'res_id': line.id}
            for name in ('preuve.txt', 'controle.txt')
        ])
        line.write({'attachment_ids': [(6, 0, attachments.ids)]})
        self.assertEqual(line.attachment_ids, attachments)
        line.write({'attachment_ids': [(3, attachments[0].id)]})
        self.assertEqual(line.attachment_ids, attachments[1])
        line.write({'attachment_ids': [(4, attachments[0].id)]})
        self.approve('preparation')
        with self.assertRaises(UserError):
            line.write({'attachment_ids': [(5, 0, 0)]})
        self.assertEqual(set(line.attachment_ids.ids), set(attachments.ids))
        self.assertEqual(line.attachment_ids[0].raw, b'Checklist evidence')

    def test_approvers_are_scoped_to_the_assigned_checklist(self):
        approver = self.env['araymond.inventory.approver'].search([('user_ids', 'in', self.users[0].ids)])
        approver.write({'phase': 'preparation'})
        self.session.with_user(self.users[0])._check_approver('preparation')
        for phase in ('day', 'post'):
            with self.assertRaises(AccessError):
                self.session.with_user(self.users[0])._check_approver(phase)
        self.approve('preparation')
        additional = self.env['araymond.inventory.approver'].create({
            'user_ids': [(6, 0, self.users[0].ids)], 'phase': 'day',
        })
        self.session.with_user(self.users[0])._check_approver('day')
        additional.active = False
        with self.assertRaises(AccessError):
            self.session.with_user(self.users[0])._check_approver('day')

    def test_wrong_checklist_assignment_blocks_validation(self):
        self.prepare()
        self.load()
        for count in self.zone.first_count_ids:
            self.finish(count)
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count)
        approver = self.env['araymond.inventory.approver'].search([('user_ids', 'in', self.users[0].ids)])
        approver.write({'phase': 'day'})
        with self.assertRaises(AccessError):
            self.session.with_user(self.users[0]).action_validate_post()
        self.assertFalse(self.session.checklist_ids.filtered(lambda c: c.phase == 'post').validated_at)

    def test_multiple_responsibles_on_one_assignment(self):
        assignment = self.env['araymond.inventory.approver'].search([
            ('user_ids', 'in', self.users[0].ids),
        ])
        assignment.write({'phase': 'preparation', 'user_ids': [(6, 0, self.users[:2].ids)]})
        for user in self.users[:2]:
            self.session.with_user(user)._check_approver('preparation')
            with self.assertRaises(AccessError):
                self.session.with_user(user)._check_approver('day')
        with self.assertRaises(AccessError):
            self.session.with_user(self.users[2])._check_approver('preparation')
        assignment.write({'user_ids': [(3, self.users[0].id)]})
        with self.assertRaises(AccessError):
            self.session.with_user(self.users[0])._check_approver('preparation')
        self.session.preparation_line_ids.write({'answer': 'ok'})
        self.session.with_user(self.users[1]).action_validate_preparation()
        self.assertEqual(self.session.state, 'Racks')
        checklist = self.session.checklist_ids.filtered(lambda c: c.phase == 'preparation')
        self.assertEqual(checklist.validated_by, self.users[1])

    def test_assignment_requires_at_least_one_responsible(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['araymond.inventory.approver'].create({'phase': 'preparation'})
        assignment = self.env['araymond.inventory.approver'].search([
            ('user_ids', 'in', self.users[0].ids),
        ])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            assignment.write({'user_ids': [(5, 0, 0)]})

    def test_full_cross_count_and_closure(self):
        self.prepare()
        self.load()
        self.assertEqual(self.session.state, 'Scan')
        self.assertFalse(self.session.scans_completed)
        with self.assertRaises(UserError):
            self.session.post_line_ids.write({'answer': 'ok'})
        with self.assertRaises(UserError):
            self.zone.write({'level': 'N1'})
        with self.assertRaises(UserError):
            self.session.lxo3_records.write({'stock_total': 20})
        first = self.zone.first_count_ids
        with self.assertRaises(AccessError):
            first[0].with_user(self.users[0])._check_scan()
        for count in first:
            self.finish(count)
        cross = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross')
        for count in cross:
            self.assertNotEqual(count.branch_id, count.origin_count_id.branch_id)
            self.finish(count)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertTrue(self.session.scans_completed)
        with self.assertRaises(UserError):
            self.session.action_close_scan()
        self.approve('post')
        self.session.action_close_scan()
        self.assertEqual(self.session.state, 'Fermeture')
        self.assertTrue(self.session.scans_completed)
        self.assertTrue(self.session.closure_report_html)
        self.assertEqual(len(self.session.scan_history_ids), 4)

    def test_third_team_must_be_assigned(self):
        self.prepare()
        self.load()
        self.assertFalse(self.session.arbitration_zone_ids)
        for count in self.zone.first_count_ids:
            self.finish(count)
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 12)
        self.assertEqual(self.zone.workflow_state, 'arbitration')
        self.assertFalse(self.session.scans_completed)
        with self.assertRaises(UserError):
            self.session.post_line_ids.write({'comment': 'Trop tôt'})
        self.assertEqual(self.session.arbitration_zone_ids, self.zone)
        third = self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration')
        self.assertEqual(len(third), 2)
        with self.assertRaises(AccessError):
            third[0].with_user(self.users[0])._check_scan()
        third[0].with_user(self.users[3])._check_scan()
        self.zone.write({'scan3_user_ids': [(6, 0, self.users[1].ids)]})
        self.session.write({'arbitration_zone_ids': [(1, self.zone.id, {
            'scan3_user_ids': [(6, 0, self.users[3].ids)],
        })]})
        for count in third:
            self.finish(count, 11)
        self.assertEqual(self.zone.workflow_state, 'done')
        self.assertFalse(self.session.arbitration_zone_ids)
        self.approve('post')
        self.session.action_close_scan()
        self.assertTrue(self.session.closure_report_html)

    def test_racks_require_third_team_before_confirmation(self):
        self.prepare(assign_third=False)
        with self.assertRaises(ValidationError):
            self.session.action_confirm_racks()
        self.assertEqual(self.session.state, 'Racks')
        self.zone.write({'scan3_user_ids': [(6, 0, self.users[3].ids)]})
        self.session.action_confirm_racks()
        self.assertEqual(self.session.state, 'InventoryDay')

    def test_same_person_can_complete_all_three_scans(self):
        self.prepare(assign_third=False)
        operator = self.users[1]
        self.zone.write({
            'scan2_user_ids': [(6, 0, operator.ids)],
            'scan3_user_ids': [(6, 0, operator.ids)],
        })
        self.session.action_confirm_racks()
        self.approve('day')
        self.load()
        for count in self.zone.first_count_ids:
            self.finish(count, 10, operator=operator)
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'cross'):
            self.finish(count, 12, operator=operator)
        self.assertEqual(self.zone.workflow_state, 'arbitration')
        for count in self.zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration'):
            self.finish(count, 11, operator=operator)
        self.assertTrue(self.session.scans_completed)
        self.approve('post')
        self.session.action_close_scan()
        self.assertEqual(self.session.state, 'Fermeture')
        self.assertEqual(len(self.session.scan_history_ids), 6)

