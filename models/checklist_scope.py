from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .complete_flow import internal, trusted


class ScopeZone(models.Model):
    _inherit = 'araymond.invsession'

    checklist_selected = fields.Boolean(default=False, copy=True)

    def write(self, values):
        if 'checklist_selected' in values and not trusted(self):
            raise AccessError('Utilisez la sélection des racks, niveaux et magasins.')
        return super().write(values)


class ScopeSession(models.Model):
    _inherit = 'araymond.maininvsession'

    def _check_scope_editable(self):
        self.ensure_one()
        self.check_access('write')
        self._lock_preparation()
        # Once scanning has started, allow only additive assignments. Existing
        # checklists and signatures must remain untouched.
        if self.state == 'Scan':
            return True
        if self.state == 'Fermeture' or self.sess_records.recs_scan.filtered(
                lambda c: c.su or c.ok_done or c.rec_scan_id.workflow_state != 'draft'):
            raise UserError('Utilisez Modifier pour changer cette affectation après le démarrage des scans.')
        checks = self.env['araymond.flow.checklist'].search([
            ('session_id', '=', self.id), ('phase', 'in', ['pre', 'post']),
            ('zone_id.flow_archived', '=', False)])
        if any(c.validated_at or c.validation_votes for c in checks):
            raise UserError('Choisissez les racks, niveaux et magasins avant les validations avant scan. Utilisez Modifier pour recommencer.')
        return False

    def action_select_checklist_scope(self):
        append_only = self._check_scope_editable()
        zones = self.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan)
        if append_only:
            zones = zones.filtered(lambda z: not z.checklist_selected)
        if not zones:
            raise UserError('Il ne reste aucun rack configuré à ajouter aux checklists.')
        wizard = self.env['araymond.checklist.scope'].create({
            'session_id': self.id, 'append_only': append_only})
        for kind, field in [('rack', 'rack'), ('level', 'level'), ('warehouse', 'magasinPhysique')]:
            options = self.env['araymond.checklist.scope.option'].create([
                {'wizard_id': wizard.id, 'kind': kind, 'name': value}
                for value in sorted(set(zones.mapped(field)) - {False})])
            selected = zones.filtered('checklist_selected').mapped(field)
            wizard[kind + '_ids'] = [(6, 0, options.filtered(lambda o: o.name in selected).ids)]
        return {'type': 'ir.actions.act_window', 'name': 'Affecter les checklists',
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}


class ScopeOption(models.TransientModel):
    _name = 'araymond.checklist.scope.option'
    _description = 'Choix indépendant de rack, niveau ou magasin'
    name = fields.Char(required=True)
    wizard_id = fields.Many2one('araymond.checklist.scope', required=True, ondelete='cascade')
    kind = fields.Selection([('rack', 'Rack'), ('level', 'Niveau'), ('warehouse', 'Magasin')], required=True)


class ScopeWizard(models.TransientModel):
    _name = 'araymond.checklist.scope'
    _description = 'Affectation des checklists par critères'
    session_id = fields.Many2one('araymond.maininvsession', required=True, readonly=True)
    append_only = fields.Boolean(readonly=True)
    rack_ids = fields.Many2many('araymond.checklist.scope.option', 'ar_scope_rack_rel', string='Racks')
    level_ids = fields.Many2many('araymond.checklist.scope.option', 'ar_scope_level_rel', string='Niveaux')
    warehouse_ids = fields.Many2many('araymond.checklist.scope.option', 'ar_scope_warehouse_rel', string='Magasins')

    def action_apply(self):
        self.ensure_one()
        append_only = self.session_id._check_scope_editable()
        if append_only != self.append_only:
            raise UserError('L’étape de la session a changé. Rouvrez le choix des racks.')
        for kind in ('rack', 'level', 'warehouse'):
            options = self[kind + '_ids']
            if not options or any(o.wizard_id != self or o.kind != kind for o in options):
                raise ValidationError('Sélectionnez au moins un rack, un niveau et un magasin parmi les choix proposés.')
        zones = self.session_id.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan)
        chosen = zones.filtered(lambda z: z.rack in self.rack_ids.mapped('name')
                                and z.level in self.level_ids.mapped('name')
                                and z.magasinPhysique in self.warehouse_ids.mapped('name'))
        if not chosen:
            raise ValidationError('Aucune zone configurée ne correspond à ces trois critères.')
        internal(chosen).write({'checklist_selected': True})
        if not append_only:
            internal(zones - chosen).write({'checklist_selected': False})
        self.session_id.invalidate_recordset()
        self.session_id._make_checklists()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
