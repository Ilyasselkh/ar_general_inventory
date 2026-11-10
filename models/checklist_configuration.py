from odoo import api, fields, models
from odoo.exceptions import ValidationError
from .complete_flow import PHASES, internal
from .session_checklists import PREPARATION, INVENTORY_DAY


class ChecklistConfiguration(models.Model):
    _name = 'araymond.flow.configuration'
    _description = 'Configuration de checklist'
    _rec_name = 'phase'

    phase = fields.Selection(PHASES, required=True, string='Checklist')
    responsible_ids = fields.Many2many('res.users', string='Responsables de validation', domain=[('share', '=', False)])
    template_ids = fields.One2many('araymond.flow.template', 'configuration_id', string='Éléments de la checklist', domain=[('is_lx03', '=', False)], context={'active_test': False})
    go_responsible_ids = fields.One2many('araymond.go.responsible', 'configuration_id', string='Responsables GO/NOGO Inventaire', context={'active_test': False})
    initialized = fields.Boolean(default=False)
    _phase_unique = models.Constraint('UNIQUE(phase)', 'Une configuration existe déjà pour cette checklist.')

    def write(self, values):
        previous = {config.id: set(config.responsible_ids.ids) for config in self}
        result = super().write(values)
        if 'responsible_ids' in values:
            for config in self:
                if config.phase == 'go' or previous[config.id] == set(config.responsible_ids.ids):
                    continue
                checks = self.env['araymond.flow.checklist'].sudo().search([
                    ('phase', '=', config.phase), ('session_id.state', '!=', 'Fermeture'),
                ])
                internal(checks).write({'validator_ids': [(6, 0, config.responsible_ids.ids)], 'validated_by': False, 'validated_at': False})
        return result

    @api.constrains('responsible_ids')
    def _check_internal(self):
        if any(self.responsible_ids.mapped('share')):
            raise ValidationError('Choisissez des utilisateurs internes pour valider les checklists.')

    @api.model
    def initialize_checklists(self):
        templates = self.env['araymond.flow.template'].with_context(active_test=False)
        for config in self.search([]):
            if not config.initialized:
                previous = self.env['araymond.inventory.approver'].search([('phase', 'in', [config.phase, 'all'])])
                config.write({'responsible_ids': [(6, 0, previous.user_ids.ids)], 'initialized': True})
            templates.search([('phase', '=', config.phase), ('configuration_id', '=', False)]).write({'configuration_id': config.id})
        # Replace only untouched generic defaults; preserve customized templates
        # and all checklist snapshots already attached to existing sessions.
        defaults = {
            'preparation': (PREPARATION, 'Préparer les zones, équipes et moyens d’inventaire'),
            'pre': (INVENTORY_DAY[:10], 'Confirmer la disponibilité des zones et l’arrêt des mouvements'),
            'post': (INVENTORY_DAY[10:], 'Contrôler les résultats et le rapprochement avant clôture'),
        }
        for phase, (instructions, generic) in defaults.items():
            config = self.search([('phase', '=', phase)], limit=1)
            old = self.env.ref('ar_general_inventory.flow_default_' + phase, raise_if_not_found=False)
            if old and old.name == generic:
                old.active = False
            for number, instruction in enumerate(instructions, 1):
                if not templates.search_count([('phase', '=', phase), ('name', '=', instruction)]):
                    templates.create({'phase': phase, 'configuration_id': config.id, 'name': instruction, 'sequence': number * 10})


class ConfiguredTemplate(models.Model):
    _inherit = 'araymond.flow.template'

    configuration_id = fields.Many2one('araymond.flow.configuration', ondelete='restrict', string='Configuration')

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            config_id = values.get('configuration_id') or self.env.context.get('default_configuration_id')
            config = self.env['araymond.flow.configuration'].browse(config_id).exists() if config_id else self.env['araymond.flow.configuration']
            if config:
                values.update(configuration_id=config.id, phase=config.phase)
            elif values.get('phase'):
                config = self.env['araymond.flow.configuration'].search([('phase', '=', values['phase'])], limit=1)
                if config:
                    values['configuration_id'] = config.id
        return super().create(vals_list)

    @api.constrains('phase', 'configuration_id')
    def _check_configuration_phase(self):
        for template in self:
            if template.configuration_id and template.phase != template.configuration_id.phase:
                raise ValidationError('Le modèle doit appartenir à la checklist sélectionnée.')


class ConfiguredSession(models.Model):
    _inherit = 'araymond.maininvsession'

    def _flow_check_approver(self, phase):
        if phase not in dict(PHASES):
            return super()._flow_check_approver(phase)
        if self.env.user.has_group('base.group_system'):
            return
        from odoo.exceptions import AccessError
        config = self.env['araymond.flow.configuration'].search([('phase', '=', phase)], limit=1)
        if self.env.user not in config.responsible_ids:
            raise AccessError('Vous devez être désigné dans Paramètres → %s pour valider cette checklist.' % dict(PHASES)[phase])


class GoResponsible(models.Model):
    _name = 'araymond.go.responsible'
    _description = 'Fonction responsable GO/NOGO Inventaire'
    _inherit = 'araymond.flow.settings.audit'
    _order = 'sequence, id'

    name = fields.Char('Responsables', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    configuration_id = fields.Many2one('araymond.flow.configuration', required=True, ondelete='restrict')
    validator_ids = fields.Many2many('res.users', string='Validateurs', required=True, domain=[('share', '=', False)])

    @api.constrains('name', 'validator_ids', 'configuration_id')
    def _check_role(self):
        for role in self:
            if not (role.name or '').strip() or not role.validator_ids:
                raise ValidationError('Renseignez la fonction du responsable et au moins un validateur.')
            if role.configuration_id.phase != 'go' or any(role.validator_ids.mapped('share')):
                raise ValidationError('Choisissez une configuration GO/NOGO et des utilisateurs internes.')

    def _sync_sessions(self):
        self.env['araymond.maininvsession'].sudo().search([
            ('full_flow', '=', True), ('state', '!=', 'Fermeture'),
        ])._sync_go_roles()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_sessions()
        return records

    def write(self, values):
        result = super().write(values)
        self._sync_sessions()
        return result


class GoRoleSession(models.Model):
    _inherit = 'araymond.maininvsession'

    def _sync_go_roles(self):
        roles = self.env['araymond.go.responsible'].search([('active', '=', True)])
        Check = self.env['araymond.flow.checklist'].sudo()
        for session in self.filtered(lambda row: row.full_flow and row.state != 'Fermeture'):
            session._lock_preparation()
            checks = Check.search([('session_id', '=', session.id), ('phase', '=', 'go')])
            obsolete = checks.filtered(lambda row: row.go_current and row.go_role_id not in roles)
            internal(obsolete).write({'go_current': False})
            for role in roles:
                check = checks.filtered(lambda row: row.go_role_id == role)
                if not check:
                    internal(Check).create({
                        'session_id': session.id, 'phase': 'go', 'go_role_id': role.id,
                        'go_role_name': role.name, 'validator_ids': [(6, 0, role.validator_ids.ids)],
                    })
                    continue
                values = {'go_role_name': role.name, 'go_current': True}
                if not check.go_current or set(check.validator_ids.ids) != set(role.validator_ids.ids):
                    values.update(validator_ids=[(6, 0, role.validator_ids.ids)], validated_at=False, validated_by=False)
                internal(check).write(values)
