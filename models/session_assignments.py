from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .complete_flow import audit, internal, snapshot, trusted
from .team_workflow import InventoryTeam


ROLES = [('entry', 'Équipe de saisie'), ('control', 'Équipe de contrôle de scan'),
         ('scan1', 'Équipe de scan 1'), ('scan2', 'Équipe de scan 2'),
         ('scan3', 'Équipe de scan 3'), ('sap', 'Équipe de support')]
SHIFTS = [('A', 'Équipe A'), ('B', 'Équipe B'), ('C', 'Équipe C'), ('admin', 'Administratif')]


class SessionAssignment(models.Model):
    _name = 'araymond.flow.assignment'
    _description = 'Affectation de personne et shift'
    _rec_name = 'user_id'
    _order = 'role, id'

    session_id = fields.Many2one('araymond.maininvsession', required=True, ondelete='cascade')
    role = fields.Selection(ROLES, required=True)
    team_id = fields.Many2one('araymond.inventory.team', ondelete='restrict')
    user_id = fields.Many2one('res.users', required=True, string='Personne', domain=[('share', '=', False)])
    # Nullable only to preserve legacy assignments whose shift was never specified.
    shift = fields.Selection(SHIFTS, string='Shift')
    _person_role_unique = models.Constraint('UNIQUE(session_id, role, user_id)', 'Cette personne est déjà affectée à cette équipe.')

    def _check_edit(self):
        for session in self.session_id:
            session.check_access('write')
            session._lock_preparation()
            if session.state == 'Fermeture':
                raise UserError('Les affectations sont verrouillées après clôture.')

    @api.constrains('user_id', 'shift', 'team_id', 'session_id', 'role')
    def _check_assignment(self):
        for line in self:
            if line.user_id.share:
                raise ValidationError('Choisissez un utilisateur interne.')
            if not line.shift and not trusted(self):
                raise ValidationError('Le shift est obligatoire pour chaque personne affectée.')
            if line.role.startswith('scan'):
                if line.team_id.session_id != line.session_id or line.team_id.automatic_slot != line.role[-1]:
                    raise ValidationError('La personne doit être affectée à une équipe de scan de cette session.')
            elif line.team_id:
                raise ValidationError('Cette affectation ne concerne pas une équipe de scan.')

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for original in vals_list:
            values = dict(original)
            team_id = values.get('team_id') or self.env.context.get('default_team_id')
            if team_id:
                team = self.env['araymond.inventory.team'].browse(team_id).exists()
                if not team.automatic_slot:
                    raise ValidationError('Choisissez une des trois équipes de scan.')
                if values.get('session_id') and values['session_id'] != team.session_id.id:
                    raise ValidationError('Équipe et session incompatibles.')
                if values.get('role') and values['role'] != 'scan' + team.automatic_slot:
                    raise ValidationError('Équipe de scan incompatible.')
                values.update(team_id=team.id, session_id=team.session_id.id, role='scan' + team.automatic_slot)
            if not values.get('session_id'):
                values['session_id'] = self.env.context.get('default_session_id')
            if not values.get('role'):
                values['role'] = self.env.context.get('default_role')
            if values.get('role') not in dict(ROLES):
                raise ValidationError('Choisissez l’équipe dans Affectation d’équipes.')
            if values.get('role', '').startswith('scan') and not values.get('team_id'):
                session = self.env['araymond.maininvsession'].browse(values['session_id'])
                values['team_id'] = session._ensure_automatic_teams((values['role'][-1],)).id
            session = self.env['araymond.maininvsession'].browse(values.get('session_id')).exists()
            if not session:
                raise ValidationError('Une session est obligatoire.')
            if not trusted(self):
                session.check_access('write')
                session._lock_preparation()
                if session.state == 'Fermeture':
                    raise UserError('Les affectations sont verrouillées après clôture.')
                if not values.get('shift'):
                    raise ValidationError('Le shift est obligatoire pour chaque personne affectée.')
            prepared.append(values)
        records = super().create(prepared)
        records.session_id._sync_assignment_members()
        for line in records:
            audit(line, line.session_id, {}, snapshot(line, ['user_id', 'shift']),
                  event='Affectation — %s' % dict(ROLES)[line.role])
        return records

    def write(self, values):
        if not trusted(self):
            self._check_edit()
            if {'session_id', 'team_id', 'role'} & values.keys():
                raise AccessError('Supprimez puis recréez l’affectation pour changer d’équipe.')
            if any(not values.get('shift', line.shift) for line in self):
                raise ValidationError('Le shift est obligatoire pour chaque personne affectée.')
        before = {line.id: snapshot(line, values) for line in self}
        result = super().write(values)
        self.session_id._sync_assignment_members()
        for line in self:
            audit(line, line.session_id, before[line.id], snapshot(line, values))
        return result

    def unlink(self):
        self._check_edit()
        sessions = self.session_id
        for line in self:
            before = snapshot(line, ['user_id', 'shift'])
            audit(line, line.session_id, before, dict.fromkeys(before, False),
                  event='Suppression d’affectation — %s' % dict(ROLES)[line.role])
        result = super().unlink()
        sessions._sync_assignment_members()
        return result


class AssignmentSession(models.Model):
    _inherit = 'araymond.maininvsession'

    assignment_ids = fields.One2many('araymond.flow.assignment', 'session_id')
    entry_assignment_ids = fields.One2many('araymond.flow.assignment', 'session_id', domain=[('role', '=', 'entry')])
    control_assignment_ids = fields.One2many('araymond.flow.assignment', 'session_id', domain=[('role', '=', 'control')])
    sap_assignment_ids = fields.One2many('araymond.flow.assignment', 'session_id', domain=[('role', '=', 'sap')])
    scan_assignment_team_ids = fields.One2many('araymond.inventory.team', 'session_id', domain=[('automatic_slot', 'in', ['1', '2', '3'])])

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        for session in sessions.filtered('full_flow'):
            session._ensure_automatic_teams(('1', '2', '3'))
        return sessions

    def write(self, values):
        if trusted(self):
            return super().write(values)
        result = True
        for session in self:
            prepared = dict(values)
            for role in ('entry', 'control', 'sap'):
                name = role + '_assignment_ids'
                if name in prepared:
                    prepared[name] = [(0, 0, dict(command[2], role=role, session_id=session.id))
                                      if command[0] == 0 else command for command in prepared[name]]
            result = super(AssignmentSession, session).write(prepared)
        return result

    def _scan_assigned_users(self, slot):
        self.ensure_one()
        return self.assignment_ids.filtered(lambda line: line.role == 'scan' + str(slot) and line.shift).user_id

    def _sync_assignment_members(self):
        for session in self:
            if not session.full_flow or session.state == 'Fermeture':
                continue
            lines = session.assignment_ids
            session.with_context(mail_notrack=True)._flow_store({
                'entry_user_ids': [(6, 0, lines.filtered(lambda l: l.role == 'entry').user_id.ids)],
                'control_user_ids': [(6, 0, lines.filtered(lambda l: l.role == 'control').user_id.ids)],
            })
            for team in session.team_ids.filtered('automatic_slot'):
                members = lines.filtered(lambda l: l.role == 'scan' + team.automatic_slot and l.shift).user_id
                super(InventoryTeam, team.sudo()).write({'user_ids': [(6, 0, members.ids)]})


class AssignmentTeam(models.Model):
    _inherit = 'araymond.inventory.team'

    assignment_ids = fields.One2many('araymond.flow.assignment', 'team_id', string='Personnes et shifts')
    session_closed = fields.Boolean(compute='_compute_session_closed')
    member_summary = fields.Text(compute='_compute_member_summary', string='Personnes et shifts')

    @api.depends('session_id.state')
    def _compute_session_closed(self):
        for team in self:
            team.session_closed = team.session_id.state == 'Fermeture'

    @api.depends('assignment_ids.user_id.name', 'assignment_ids.shift')
    def _compute_member_summary(self):
        for team in self:
            team.member_summary = '\n'.join('%s — %s' % (line.user_id.name, dict(SHIFTS).get(line.shift, 'Shift à renseigner')) for line in team.assignment_ids)

    def action_open_assignments(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'views': [(self.env.ref('ar_general_inventory.view_session_scan_team_assignments').id, 'form')], 'target': 'new'}

    def write(self, values):
        if set(values) == {'assignment_ids'} and all(team.session_id.full_flow for team in self):
            for team in self:
                team.session_id.check_access('write')
                team.session_id._lock_preparation()
                if team.session_id.state == 'Fermeture':
                    raise UserError('Les affectations sont verrouillées après clôture.')
            result = True
            for team in self:
                commands = []
                for command in values['assignment_ids']:
                    if command[0] == 0:
                        line_values = dict(command[2])
                        if line_values.get('team_id') and line_values['team_id'] != team.id:
                            raise ValidationError('La personne doit appartenir à cette équipe.')
                        line_values['team_id'] = team.id
                        commands.append((0, 0, line_values))
                    else:
                        commands.append(command)
                result = super(InventoryTeam, team).write({'assignment_ids': commands})
            return result
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Seules les personnes et leurs shifts sont modifiables depuis ce tableau.')
        return super().write(values)
