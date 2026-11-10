from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval
from .common import legacy_session


@tagged('post_install', '-at_install')
class TestChecklistSettings(TransactionCase):
    def setUp(self):
        super().setUp()
        self.session = legacy_session(self.env, {
            'typesession': 'InvGen', 'datesession': fields.Datetime.now(),
        })

    def zone(self):
        return self.env['araymond.invsession'].create({
            'mainsession_id': self.session.id, 'supervisor_id': self.env.uid,
            'magasinPhysique': 'M05', 'magasinSAP': 'RM00', 'rack': 'A', 'nbrEmp': 1, 'statusSession': False,
        })

    def test_scan_menu_includes_session_without_zones(self):
        action = self.env.ref('ar_general_inventory.action_araymond_scan_inventory')
        self.assertEqual(action.res_model, 'araymond.maininvsession')
        self.assertIn(self.session, self.env[action.res_model].search(safe_eval(action.domain)))
        self.assertFalse(self.session.sess_records)
        self.assertEqual(set(action.view_ids.mapped('view_mode')), {'list', 'form'})

    def test_templates_sync_pending_and_freeze_validated(self):
        zone = self.zone()
        instruction = self.env['araymond.inventory.instruction'].create({'instruction': 'Vérifier les allées'})
        line = zone.checklist_ids.filtered(lambda item: item.instruction_id == instruction)
        self.assertEqual(len(line), 1)
        line.action_answer_ok()
        instruction.write({'instruction': 'Vérifier toutes les allées'})
        self.assertEqual(line.instruction, instruction.instruction)
        self.assertFalse(line.answer)
        zone.checklist_ids.action_answer_ok()
        zone.action_validate_checklist()
        frozen = [(item.instruction, item.answer) for item in zone.checklist_ids]
        instruction.write({'instruction': 'Consigne modifiée après validation'})
        self.env['araymond.inventory.instruction'].create({'instruction': 'Nouvelle consigne'})
        zone.action_open_zone()
        self.assertEqual([(item.instruction, item.answer) for item in zone.checklist_ids], frozen)
        next_zone = self.zone()
        self.assertIn(instruction.instruction, next_zone.checklist_ids.mapped('instruction'))

    def test_ok_nok_buttons_and_required_comment(self):
        zone = self.zone()
        zone.checklist_ids.action_answer_ok()
        line = zone.checklist_ids[0]
        line.action_answer_nok()
        self.assertEqual(line.answer, 'nok')
        with self.assertRaises(ValidationError):
            zone.action_validate_checklist()
        line.write({'comment': 'Mesure compensatoire'})
        zone.action_validate_checklist()
        with self.assertRaises(UserError):
            line.action_answer_ok()

    def test_archive_only_removes_pending_templates(self):
        instruction = self.env['araymond.inventory.instruction'].create({'instruction': 'Consigne temporaire'})
        frozen_zone = self.zone()
        frozen_zone.checklist_ids.action_answer_ok()
        frozen_zone.action_validate_checklist()
        pending = self.zone()
        instruction.active = False
        self.assertNotIn(instruction, pending.checklist_ids.instruction_id)
        self.assertIn(instruction, frozen_zone.checklist_ids.instruction_id)

    def test_open_zone_does_not_duplicate_lines(self):
        zone = self.zone()
        before = zone.checklist_ids
        self.assertTrue(before)
        zone.action_open_zone()
        zone.action_open_zone()
        self.assertEqual(zone.checklist_ids, before)


    def test_settings_menu_group_only_controls_visibility(self):
        user = self.env['res.users'].create({
            'name': 'Lecture paramètres', 'login': 'test_settings_menu_visibility',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        menu = self.env.ref('ar_general_inventory.menu_inventory_settings')
        child = self.env.ref('ar_general_inventory.menu_checklist_instructions')
        self.assertNotIn(menu.id, self.env['ir.ui.menu'].with_user(user)._visible_menu_ids())
        group = self.env.ref('ar_general_inventory.group_show_settings_menu')
        self.assertFalse(group.implied_ids)
        user.write({'group_ids': [(4, group.id)]})
        visible = self.env['ir.ui.menu'].with_user(user)._visible_menu_ids()
        self.assertIn(menu.id, visible)
        self.assertFalse(child.active)
        self.assertNotIn(child.id, visible)
        self.assertNotIn(self.env.ref('ar_general_inventory.menu_inventory_approvers').id, visible)
        self.env['araymond.inventory.instruction'].with_user(user).check_access('read')
        with self.assertRaises(AccessError):
            self.env['araymond.inventory.instruction'].with_user(user).check_access('write')
        with self.assertRaises(AccessError):
            self.env['araymond.inventory.instruction'].with_user(user).check_access('create')
        with self.assertRaises(AccessError):
            self.env['araymond.inventory.instruction'].with_user(user).check_access('unlink')
