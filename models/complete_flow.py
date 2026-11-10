"""Validated inventory lifecycle. Existing sessions keep their historical workflow."""
import json
from markupsafe import Markup, escape
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .session_preparation import PreparedSession
from .AR_inventory import ARinventorySessionMain
from .team_workflow import same_quantities

TOKEN = object()  # Not reproducible by a JSON/RPC context supplied by a client.
PHASES = [('preparation', 'Préparation'), ('risk', 'Analyse des risques et actions'),
          ('simulation', 'Simulation de stock'), ('day', 'Extraction'),
          ('inventory_day', 'Checklist jour d’inventaire'),
          ('export', 'Exportation des fichiers SAP'), ('go', 'GO/NOGO Inventaire'),
          ('pre', 'Checklist avant scan'), ('post', 'Checklist après scan')]
STAGES = [('Draft', 'Création'), ('Preparation', 'Préparation'),
          ('GoNoGo', 'GO/NOGO Inventaire'), ('InventoryDay', 'Jour d’inventaire'),
          ('Scan', 'Scan'), ('Fermeture', 'Clôturé')]
STAGE_PHASES = {'Draft': [], 'Preparation': ['preparation', 'risk', 'simulation'],
                'GoNoGo': ['go'], 'InventoryDay': ['day', 'inventory_day'],
                'Scan': ['pre', 'post']}


def internal(records):
    return records.with_context(_ar_flow_token=TOKEN)


def trusted(records):
    return records.env.context.get('_ar_flow_token') is TOKEN


def snapshot(record, names):
    result = {}
    for name in names:
        field = record._fields.get(name)
        if not field or name in ('message_ids', 'message_follower_ids', 'activity_ids'):
            continue
        value = record[name]
        if field.type in ('many2one', 'one2many', 'many2many'):
            result[name] = [(item.id, item.display_name) for item in value]
        elif field.type == 'binary':
            result[name] = '%s octets' % len(value) if value else False
        else:
            result[name] = str(value) if field.type in ('date', 'datetime') and value else value
    return result


def readable_changes(record, delta, event):
    def display(name, value):
        field = record._fields.get(name)
        if value is None or value is False or value == [] or value == '':
            return 'Non renseigné'
        if field and field.type == 'boolean':
            return 'Oui' if value else 'Non'
        if field and field.type == 'selection':
            return dict(field._description_selection(record.env)).get(value, str(value))
        if isinstance(value, list):
            return ', '.join(str(item[1]) if isinstance(item, (tuple, list)) and len(item) > 1 else str(item) for item in value) or 'Aucun'
        return str(value)

    rows = []
    for name, change in delta.items():
        field = record._fields.get(name)
        label = field.string if field else name.replace('_', ' ').capitalize()
        rows.append(Markup('<li><strong>%s</strong> : %s → %s</li>') % (
            label, display(name, change['avant']), display(name, change['après'])))
    return Markup('<p><strong>%s</strong></p><ul>%s</ul>') % (event, Markup('').join(rows))


def audit(record, session, before, after, phase='changes', event='Modification', invalidate=True, message_body=None):
    if not session or not session.full_flow or trusted(record):
        return
    delta = {key: {'avant': before.get(key), 'après': value} for key, value in after.items() if before.get(key) != value}
    if not delta and event == 'Modification':
        return
    session._lock_preparation()
    if session.state == 'Fermeture':
        raise UserError('La session clôturée est conservée en lecture seule.')
    payload = json.dumps(delta or after, ensure_ascii=False, indent=2, default=str)
    internal(record.env['araymond.flow.approval'].sudo()).create({
        'session_id': session.id, 'res_model': record._name, 'res_id': record.id,
        'name': '%s — %s #%s' % (event, record._description, record.id),
        'details': payload, 'phase': phase, 'author_id': record.env.uid, 'state': 'recorded',
    })
    visible_delta = delta
    if message_body is not None:
        session.message_post(body=message_body, subtype_xmlid='mail.mt_note')
    elif event == 'Modification':
        def filled(key, value):
            return value not in (None, False, '', []) and not (key == 'answer' and value == 'wait')

        visible_delta = {key: change for key, change in delta.items() if filled(key, change['avant'])}
        # Keep initial entries in the audit, but do not present them as edits.
        # Earlier entries also distinguish a cleared value from a never-filled field.
        history = record.env['araymond.flow.approval'].sudo().search([
            ('res_model', '=', record._name), ('res_id', '=', record.id),
        ], order='id desc')
        for previous in history[1:]:
            changes = json.loads(previous.details or '{}')
            for key, change in delta.items():
                old = changes.get(key)
                if isinstance(old, dict) and (filled(key, old.get('avant')) or filled(key, old.get('après'))):
                    visible_delta[key] = change
        if visible_delta:
            session.message_post(body=readable_changes(record, visible_delta, event), subtype_xmlid='mail.mt_note')
    else:
        session.message_post(body=readable_changes(record, delta, event), subtype_xmlid='mail.mt_note')
    if not invalidate:
        return
    checks = session.flow_check_ids
    zone = record.zone_id if record._name == 'araymond.flow.checklist' else (record.checklist_id.zone_id if record._name == 'araymond.flow.line' else False)
    if record._name == 'araymond.inventory':
        zone = record.rec_scan_id
    elif record._name == 'araymond.etiquette':
        zone = record.su_id.rec_scan_id
    elif record._name == 'araymond.invsession':
        zone = record
    if zone:
        checks = checks.filtered(lambda check: not check.zone_id or check.zone_id == zone)
    if phase != 'changes':
        checks = checks.filtered(lambda c: c.phase == phase)
    elif record._name in ('araymond.etiquette', 'araymond.inventory'):
        checks = checks.filtered(lambda c: c.phase == 'post')
    internal(checks.sudo()).write({'validated_by': False, 'validated_at': False})


def audit_setting(record, before, after):
    if trusted(record):
        return
    delta = {key: {'avant': before.get(key), 'après': value} for key, value in after.items() if before.get(key) != value}
    if not delta:
        return
    details = json.dumps(delta, ensure_ascii=False, indent=2, default=str)
    internal(record.env['araymond.flow.approval'].sudo()).create({
        'name': 'Paramètres — %s #%s' % (record._description, record.id), 'res_model': record._name,
        'res_id': record.id, 'phase': 'settings', 'author_id': record.env.uid, 'details': details, 'state': 'recorded',
    })
    record.message_post(body=readable_changes(record, delta, 'Modification des paramètres'), subtype_xmlid='mail.mt_note')


class FlowSettingsAudit(models.AbstractModel):
    _name = 'araymond.flow.settings.audit'
    _description = 'Traçabilité des paramètres inventaire'
    _inherit = 'mail.thread'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record, values in zip(records, vals_list):
            audit_setting(record, {}, snapshot(record, values))
        return records

    def write(self, values):
        before = {record.id: snapshot(record, values) for record in self}
        result = super().write(values)
        for record in self:
            audit_setting(record, before[record.id], snapshot(record, values))
        return result

    def unlink(self):
        raise UserError('Archivez les paramètres plutôt que de supprimer leur historique.')


class FlowTemplate(models.Model):
    _name = 'araymond.flow.template'
    _inherit = 'araymond.flow.settings.audit'
    _description = 'Modèle de checklist inventaire'
    _order = 'phase, sequence, id'
    name = fields.Char('Description / consigne', required=True)
    responsible_id = fields.Many2one('res.users', string='Responsable', domain=[('share', '=', False)])
    responsible_ids = fields.Many2many('res.users', string='Responsables', domain=[('share', '=', False)])
    phase = fields.Selection(PHASES, required=True, string='Checklist')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    file_name = fields.Char('Nom du fichier')
    is_lx03 = fields.Boolean('Fichier LX03 à importer')
    warehouse = fields.Selection(selection=lambda self: self.env['araymond.invsession']._fields['magasinPhysique']._description_selection(self.env), string='Magasin')
    sap_warehouse = fields.Selection(selection=lambda self: self.env['araymond.invsession']._fields['magasinSAP']._description_selection(self.env), string='Magasin SAP')
    location_count = fields.Integer('Nombre d’emplacements')

    @api.constrains('is_lx03', 'phase')
    def _check_lx03(self):
        for template in self:
            if template.is_lx03 and template.phase != 'day':
                raise ValidationError('LX03 appartient uniquement à la checklist Jour d’inventaire.')


class FlowTeamConfig(models.Model):
    _name = 'araymond.flow.team.config'
    _inherit = 'araymond.flow.settings.audit'
    _description = 'Configuration des équipes de scan'
    _rec_name = 'slot'
    slot = fields.Selection([('1', 'Équipe 1'), ('2', 'Équipe 2'), ('3', 'Équipe 3')], required=True, string='Équipe')
    user_ids = fields.Many2many('res.users', string='Membres', domain=[('share', '=', False)])
    _slot_unique = models.Constraint('UNIQUE(slot)', 'Chaque équipe ne doit être configurée qu’une fois.')

    @api.model
    def _bind_existing_default_teams(self):
        """Adopt existing slots before XML defaults are loaded, without editing members."""
        identifiers = self.env['ir.model.data'].sudo()
        for slot in ('1', '2', '3'):
            team = self.search([('slot', '=', slot)], limit=1)
            if not team:
                continue
            name = 'flow_config_team_%s' % slot
            identifier = identifiers.search([
                ('module', '=', 'ar_general_inventory'), ('name', '=', name),
            ], limit=1)
            values = {'model': self._name, 'res_id': team.id, 'noupdate': True}
            if identifier:
                identifier.write(values)
            else:
                identifiers.create(dict(values, module='ar_general_inventory', name=name))

    @api.constrains('user_ids')
    def _internal_members(self):
        if any(self.user_ids.mapped('share')):
            raise ValidationError('Choisissez des utilisateurs internes.')


class FlowApprover(models.Model):
    _name = 'araymond.inventory.approver'
    _inherit = ['araymond.inventory.approver', 'araymond.flow.settings.audit']
    phase = fields.Selection(PHASES + [('changes', 'Approbation des saisies et modifications'), ('all', 'Toutes les validations')])


class FlowApproval(models.Model):
    _name = 'araymond.flow.approval'
    _description = 'Validation de saisie ou modification'
    _order = 'id desc'
    name = fields.Char(readonly=True)
    session_id = fields.Many2one('araymond.maininvsession', ondelete='restrict', readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    phase = fields.Char(readonly=True)
    details = fields.Text('Changements', readonly=True)
    author_id = fields.Many2one('res.users', string='Saisi par', readonly=True)
    state = fields.Selection([('recorded', 'Enregistré — sans approbation'), ('pending', 'En attente'), ('approved', 'Approuvé'), ('rejected', 'Refusé')], default='recorded', readonly=True)
    reviewer_id = fields.Many2one('res.users', string='Décision par', readonly=True)
    reviewed_at = fields.Datetime('Date de décision', readonly=True)
    reason = fields.Text('Motif / commentaire')

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self):
            raise AccessError('Les demandes sont générées automatiquement par les saisies.')
        return super().create(vals_list)

    def write(self, values):
        if not trusted(self):
            if set(values) != {'reason'}:
                raise AccessError('Utilisez les boutons Approuver ou Refuser.')
            self.env['araymond.maininvsession']._flow_check_approver('changes')
        return super().write(values)

    def unlink(self):
        raise AccessError('Les demandes de validation font partie de la traçabilité.')

    def _decision(self, state):
        self.session_id._lock_preparation()
        self.env['araymond.maininvsession']._flow_check_approver('changes')
        for approval in self:
            if approval.state == 'recorded':
                continue
            if state == 'rejected' and not (approval.reason or '').strip():
                raise ValidationError('Indiquez le motif du refus.')
            if approval.state == state:
                continue
            internal(approval).write({'state': state, 'reviewer_id': self.env.uid, 'reviewed_at': fields.Datetime.now()})
            approval.session_id._flow_message('%s : %s. %s' % (approval.name, dict(self._fields['state'].selection)[state], approval.reason or ''))
            if not approval.session_id:
                target = self.env[approval.res_model].browse(approval.res_id).exists()
                if target:
                    target.sudo().message_post(body='Validation des paramètres : %s. %s' % (state, approval.reason or ''), subtype_xmlid='mail.mt_note')
        if state == 'approved':
            for session in self.session_id.filtered(lambda s: s.state == 'Scan'):
                for zone in session.sess_records.filtered('statusSession'):
                    zone._advance_counts()

    def action_approve(self):
        self._decision('approved')

    def action_reject(self):
        self._decision('rejected')


class FlowChecklist(models.Model):
    _name = 'araymond.flow.checklist'
    _description = 'Checklist validée de session'
    _rec_name = 'phase'
    session_id = fields.Many2one('araymond.maininvsession', required=True, ondelete='restrict')
    phase = fields.Selection(PHASES, required=True, string='Checklist')
    line_ids = fields.One2many('araymond.flow.line', 'checklist_id')
    zone_id = fields.Many2one('araymond.invsession', string='Rack', ondelete='restrict', readonly=True)
    rack_label = fields.Char(compute='_compute_rack_label', string='Niveau / rack / magasins')
    can_edit = fields.Boolean(compute='_compute_can_edit')

    @api.depends('session_id.state', 'phase', 'zone_id.statusSession', 'zone_id.workflow_state')
    def _compute_can_edit(self):
        for check in self:
            check.can_edit = (check.session_id.state == 'Scan' and bool(check.zone_id.statusSession)
                              and (check.phase == 'pre' or (check.phase == 'post' and check.zone_id.workflow_state == 'done')))

    @api.constrains('zone_id', 'session_id', 'phase')
    def _check_rack_session(self):
        for check in self:
            if check.zone_id and (check.zone_id.mainsession_id != check.session_id or check.phase not in ('pre', 'post')):
                raise ValidationError('La checklist doit appartenir au rack et à la session concernés.')

    @api.depends('zone_id.rack', 'zone_id.level', 'zone_id.magasinPhysique', 'zone_id.magasinSAP')
    def _compute_rack_label(self):
        for check in self:
            check.rack_label = '%s — Rack %s — Niveau %s — %s' % (check.zone_id.magasinPhysique, check.zone_id.rack, check.zone_id.level or 'à renseigner', check.zone_id.magasinSAP) if check.zone_id else 'Session'

    def action_open_rack_checklist(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'views': [(self.env.ref('ar_general_inventory.view_rack_flow_checklist').id, 'form')], 'target': 'new'}
    validated_by = fields.Many2one('res.users', readonly=True, string='Validée par')
    validated_at = fields.Datetime(readonly=True, string='Validée le')
    validator_ids = fields.Many2many('res.users', string='Responsables requis', readonly=True)
    validation_votes = fields.Json(default=dict, readonly=True)
    validators_html = fields.Html(compute='_compute_validation_display', string='Responsables')
    statuses_html = fields.Html(compute='_compute_validation_display', string='Statut par responsable')
    dates_html = fields.Html(compute='_compute_validation_display', string='Validée le')

    go_role_id = fields.Many2one('araymond.go.responsible', readonly=True, ondelete='restrict')
    go_role_name = fields.Char('Responsables', readonly=True)
    go_current = fields.Boolean(default=True, readonly=True)

    @api.depends('validator_ids', 'validation_votes')
    def _compute_validation_display(self):
        for check in self:
            names, statuses, dates = [], [], []
            for user in check.validator_ids.sorted('id'):
                date = (check.validation_votes or {}).get(str(user.id))
                names.append(Markup('<div>%s</div>') % user.name)
                statuses.append(Markup('<div style="color: %s; font-weight: 600;">%s</div>') %
                                ('#198754' if date else '#b86b00', 'Validé' if date else 'En attente'))
                shown = fields.Datetime.context_timestamp(check, fields.Datetime.to_datetime(date)).strftime('%d/%m/%Y %H:%M') if date else '—'
                dates.append(Markup('<div>%s</div>') % shown)
            check.validators_html = Markup('').join(names) or 'Aucun responsable configuré'
            check.statuses_html = Markup('').join(statuses)
            check.dates_html = Markup('').join(dates)
    _go_role_unique = models.Constraint('UNIQUE(session_id, go_role_id)', 'Cette fonction GO/NOGO existe déjà dans cette session.')
    _phase_unique = models.Constraint('UNIQUE(session_id, phase, zone_id)', 'Checklist déjà présente pour ce niveau de rack.')

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self):
            raise AccessError('Les checklists sont générées depuis les paramètres.')
        for values in vals_list:
            config = self.env['araymond.flow.configuration'].search([('phase', '=', values.get('phase'))], limit=1)
            values.setdefault('validator_ids', [(6, 0, [] if values.get('phase') == 'go' else config.responsible_ids.ids)])
        return super().create(vals_list)

    def write(self, values):
        if not trusted(self):
            if set(values) != {'line_ids'}:
                raise AccessError('Utilisez les lignes et les boutons de validation.')
            for check in self:
                check.session_id._lock_preparation()
                check._check_stage()
        if 'validated_at' in values and not values['validated_at']:
            values = dict(values, validation_votes={})
        return super().write(values)

    def unlink(self):
        raise AccessError('Conservez l’historique de la checklist.')

    def _all_validators_signed(self):
        self.ensure_one()
        return bool(self.validated_at and self.validator_ids) and all(
            (self.validation_votes or {}).get(str(user.id)) for user in self.validator_ids
        )

    def action_validate(self):
        self.ensure_one()
        session = self.session_id
        session._lock_preparation()
        if self.phase == 'go' and not self.go_current:
            raise UserError('Cette fonction GO/NOGO n’est plus active.')
        if self.env.user not in self.validator_ids:
            raise AccessError('Seuls les responsables affectés à cette checklist peuvent la valider.')
        session._require_approved()
        self._check_stage()
        lines = self.line_ids.filtered('active') if self.phase != 'go' else self.env['araymond.flow.line']
        if not lines and self.phase != 'go':
            raise ValidationError('Configurez puis actualisez au moins une ligne pour cette checklist.')
        for line in lines:
            if self.phase in ('preparation', 'inventory_day', 'pre', 'post'):
                if line.answer == 'wait' or not line.answer:
                    raise ValidationError('Une réponse EN ATTENTE ne permet pas de valider.')
                if line.answer == 'nok' and not (line.comment or '').strip():
                    raise ValidationError('Chaque NOK nécessite un commentaire.')
            if self.phase == 'risk' and (not (line.responsible_id or line.responsible_ids) or not line.due_date):
                raise ValidationError('Chaque action doit avoir un responsable et une échéance.')
            if self.phase == 'simulation' and (not line.warehouse or not line.sap_warehouse or line.location_count < 0):
                raise ValidationError('Complétez les magasins et le nombre d’emplacements de simulation.')
            if self.phase in ('day', 'export') and (not line.file_name or not line.attachment_ids):
                raise ValidationError('Chaque fichier attendu doit être nommé et joint.')
        if self.phase == 'post' and (not self.zone_id or self.zone_id.workflow_state != 'done'):
            raise UserError('Terminez les scans et les arbitrages avant cette validation.')
        votes = dict(self.validation_votes or {})
        if str(self.env.uid) in votes:
            return
        votes[str(self.env.uid)] = fields.Datetime.to_string(fields.Datetime.now())
        values = {'validation_votes': votes}
        complete = all(str(user.id) in votes for user in self.validator_ids)
        if complete:
            values.update(validated_by=self.env.uid, validated_at=fields.Datetime.now())
        internal(self).write(values)
        if complete and self.phase == 'pre' and self.zone_id.workflow_state == 'draft':
            zone = self.zone_id
            if session._session_scan_pool():
                session._start_session_scan_pool()
            elif not zone.first_count_ids:
                zone._set_workflow({'checklist_validated': True, 'validated_by': self.env.uid, 'validated_at': fields.Datetime.now()})
            elif len(zone.first_count_ids) != zone.nbrEmp:
                raise ValidationError('Saisissez manuellement les %s emplacements prévus pour ce niveau avant de valider.' % zone.nbrEmp)
            else:
                zone._set_workflow({'checklist_validated': True, 'validated_by': self.env.uid, 'validated_at': fields.Datetime.now()})
                internal(zone.sudo()).action_start_teams()
        label = '%s — %s' % (dict(PHASES)[self.phase], self.rack_label) if self.zone_id else dict(PHASES)[self.phase]
        session._flow_message('%s : %s — %s.' % (label, self.env.user.name, 'checklist entièrement validée' if complete else 'validation enregistrée, autres responsables en attente'))

    def _check_stage(self):
        self.ensure_one()
        if self.phase == 'export':
            raise UserError('Cette ancienne checklist est conservée uniquement pour son historique.')
        if self.phase in ('pre', 'post') and (not self.zone_id or not self.zone_id.statusSession or not self.zone_id.checklist_selected):
            raise UserError('Choisissez la checklist d’un rack actif.')
        if self.phase == 'post' and self.zone_id.workflow_state != 'done':
            raise UserError('Terminez les scans et les arbitrages avant de remplir cette checklist.')
        stage = next(key for key, phases in STAGE_PHASES.items() if self.phase in phases)
        order = dict((key, n) for n, (key, _) in enumerate(STAGES))
        if self.session_id.state == 'Fermeture' or order.get(self.session_id.state, -1) < order[stage]:
            raise UserError('Cette checklist ne peut pas encore être remplie ou la session est clôturée.')


class FlowLine(models.Model):
    _name = 'araymond.flow.line'
    _description = 'Ligne de checklist inventaire'
    _order = 'sequence, id'
    _rec_name = 'description'
    active = fields.Boolean(default=True)
    checklist_id = fields.Many2one('araymond.flow.checklist', required=True, ondelete='restrict')
    session_id = fields.Many2one(related='checklist_id.session_id', store=True)
    phase = fields.Selection(related='checklist_id.phase')
    template_id = fields.Many2one('araymond.flow.template', ondelete='set null', readonly=True)
    template_snapshot = fields.Json(readonly=True, copy=False)
    sequence = fields.Integer(default=10)
    description = fields.Text('Description', required=True)
    responsible_id = fields.Many2one('res.users', string='Responsable', domain=[('share', '=', False)])
    responsible_ids = fields.Many2many('res.users', 'ar_flow_line_responsible_rel', 'line_id', 'user_id', string='Responsables', domain=[('share', '=', False)])
    due_date = fields.Date('Date d’échéance')
    risk_closure = fields.Selection([('done', 'Done'), ('not_done', 'Not Done')],
                                    string='Clôturer', default='not_done')
    risk_level = fields.Selection([('high', 'High'), ('medium', 'Medium'), ('low', 'Low')],
                                  string='Risque')
    comment = fields.Text('Commentaire')
    answer = fields.Selection([('ok', 'OK'), ('wait', 'EN ATTENTE'), ('nok', 'NOK')], default='wait', required=True, string='Statut')
    go_decision = fields.Selection([('go', 'GO'), ('nogo', 'NOGO')], compute='_compute_go_decision', inverse='_inverse_go_decision', string='Décision GO/NOGO Inventaire')

    @api.depends('answer')
    def _compute_go_decision(self):
        for line in self:
            line.go_decision = {'ok': 'go', 'nok': 'nogo'}.get(line.answer, False)

    def _inverse_go_decision(self):
        for line in self:
            line.write({'answer': {'go': 'ok', 'nogo': 'nok'}.get(line.go_decision, 'wait')})
    attachment_ids = fields.Many2many('ir.attachment', 'ar_flow_line_attachment_rel', 'line_id', 'attachment_id', string='Pièces jointes')
    file_name = fields.Char('Nom du fichier')
    is_lx03 = fields.Boolean(readonly=True)
    warehouse = fields.Selection(selection=lambda self: self.env['araymond.invsession']._fields['magasinPhysique']._description_selection(self.env), string='Magasin')
    sap_warehouse = fields.Selection(selection=lambda self: self.env['araymond.invsession']._fields['magasinSAP']._description_selection(self.env), string='Magasin SAP')
    location_count = fields.Integer('Nombre d’emplacements')

    def _check_risk_required_fields(self):
        for line in self.filtered(lambda row: row.active and row.phase == 'risk'):
            if not (line.responsible_id or line.responsible_ids) or not line.due_date:
                raise ValidationError('Chaque action doit avoir au moins un responsable et une date d’échéance.')

    @api.model_create_multi
    def create(self, vals_list):
        if trusted(self):
            return super().create(vals_list)
        prepared = []
        for original in vals_list:
            values = dict(original)
            if values.get('template_id') or values.get('is_lx03') or 'active' in values or 'template_snapshot' in values:
                raise AccessError('Les lignes locales ne peuvent pas changer la source des modèles ou du LX03.')
            session_id = values.pop('session_id', None) or self.env.context.get('default_session_id')
            phase = values.pop('phase', None) or self.env.context.get('default_phase')
            checklist = self.env['araymond.flow.checklist'].browse(values.get('checklist_id')).exists()
            if not checklist and session_id and phase:
                checklist = self.env['araymond.flow.checklist'].search([
                    ('session_id', '=', session_id), ('phase', '=', phase),
                ], limit=1)
            if not checklist or (session_id and checklist.session_id.id != session_id) or (phase and checklist.phase != phase):
                raise ValidationError('Sélectionnez une checklist de la session.')
            checklist.session_id.check_access('write')
            checklist.session_id._lock_preparation()
            checklist._check_stage()
            values['checklist_id'] = checklist.id
            if checklist.phase == 'simulation' and not values.get('description'):
                values['description'] = 'Simulation de stock'
            prepared.append(values)
        records = super().create(prepared)
        records._check_risk_required_fields()
        for line, values in zip(records, prepared):
            audit(line, line.session_id, {}, snapshot(line, values), line.phase, 'Ajout de ligne de checklist')
        return records

    def write(self, values):
        if trusted(self):
            return super().write(values)
        if set(values) & {'checklist_id', 'session_id', 'template_id', 'phase', 'is_lx03', 'active', 'template_snapshot'}:
            raise AccessError('La checklist et la source sont fixes.')
        before = {}
        for line in self:
            line.session_id._lock_preparation()
            line.checklist_id._check_stage()
            if line.is_lx03 and 'attachment_ids' in values and line.session_id.state != 'InventoryDay':
                raise UserError('Le fichier LX03 est figé après le Jour d’inventaire pour préserver la base de comparaison.')
            before[line.id] = snapshot(line, values)
        result = super().write(values)
        self._check_risk_required_fields()
        for line in self:
            audit(line, line.session_id, before[line.id], snapshot(line, values), line.phase)
            if line.is_lx03 and 'attachment_ids' in values:
                line.session_id._flow_store({'file_loaded': False})
        return result

    def unlink(self):
        self.check_access('unlink')
        for line in self.filtered('active'):
            line.session_id.check_access('write')
            line.session_id._lock_preparation()
            line.checklist_id._check_stage()
            if line.is_lx03 and line.session_id.state != 'InventoryDay':
                raise UserError('Le fichier LX03 est figé après le Jour d’inventaire.')
            details = snapshot(line, ['description', 'warehouse', 'sap_warehouse', 'location_count',
                                     'responsible_id', 'responsible_ids', 'due_date', 'answer', 'risk_closure', 'risk_level',
                                     'comment', 'file_name', 'attachment_ids'])
            event = 'Suppression de ligne — %s' % dict(PHASES)[line.phase]
            body = None
            if line.phase == 'simulation':
                rows = [Markup('<li><strong>%s</strong> : %s</li>') % (label, value)
                        for label, value in [('Magasin', line.warehouse or 'Non renseigné'),
                                             ('Magasin SAP', line.sap_warehouse or 'Non renseigné'),
                                             ('Nombre d’emplacements', line.location_count)]]
                body = Markup('<p><strong>%s</strong></p><ul>%s</ul>') % (event, Markup('').join(rows))
            audit(line, line.session_id, details, dict.fromkeys(details, False), line.phase,
                  event, message_body=body)
            if line.is_lx03:
                line.session_id._flow_store({'file_loaded': False})
        # Preserve the snapshot and its source so refresh cannot recreate it.
        internal(self).write({'active': False})
        return True

    def action_ok(self):
        self.write({'answer': 'ok'})

    def action_wait(self):
        self.write({'answer': 'wait'})

    def action_nok(self):
        self.write({'answer': 'nok'})

    def action_load_lx03(self):
        raise UserError('Utilisez le champ Fichier LX03 et le bouton Charger dans Extraction.')


class FullSession(models.Model):
    _name = 'araymond.maininvsession'
    _inherit = ['araymond.maininvsession', 'mail.thread', 'mail.activity.mixin']
    full_flow = fields.Boolean(default=False, readonly=True, copy=False)
    state = fields.Selection(STAGES + [('Racks', 'Racks historique'), ('LX03', 'LX03 historique'), ('CreationMain', 'Création historique'), ('Creation', 'Configuration historique'), ('Checklist', 'Checklist historique')], default='Draft', tracking=True)
    datesession = fields.Datetime(string='Date d’inventaire', required=False, tracking=True)
    entry_user_ids = fields.Many2many('res.users', 'ar_flow_entry_users', string='Équipe de saisie', domain=[('share', '=', False)], tracking=True)
    control_user_ids = fields.Many2many('res.users', 'ar_flow_control_users', string='Équipe de contrôle', domain=[('share', '=', False)], tracking=True)
    entry_shift = fields.Selection([('A', 'Équipe A'), ('B', 'Équipe B'), ('C', 'Équipe C'), ('admin', 'Administratif')], string='Shift — équipe de saisie')
    control_shift = fields.Selection([('A', 'Équipe A'), ('B', 'Équipe B'), ('C', 'Équipe C'), ('admin', 'Administratif')], string='Shift — équipe de contrôle')
    flow_check_ids = fields.One2many('araymond.flow.checklist', 'session_id', domain=[('go_current', '=', True), ('phase', '!=', 'export'), '|', ('phase', 'not in', ['pre', 'post']), ('zone_id', '!=', False)], readonly=True)
    visible_flow_check_ids = fields.Many2many('araymond.flow.checklist', compute='_compute_visible_flow_checks', string='Validations')
    preparation_validation_ids = fields.Many2many('araymond.flow.checklist', compute='_compute_stage_validations', string='Validations de préparation')
    inventory_day_validation_ids = fields.Many2many('araymond.flow.checklist', compute='_compute_stage_validations', string='Validations du jour d’inventaire')
    go_check_ids = fields.One2many('araymond.flow.checklist', 'session_id', domain=[('phase', '=', 'go'), ('go_current', '=', True)], readonly=True)
    rack_check_ids = fields.One2many('araymond.flow.checklist', 'session_id', domain=[('phase', 'in', ['pre', 'post']), ('zone_id.statusSession', '=', True), ('zone_id.checklist_selected', '=', True)], readonly=True)
    visible_rack_check_ids = fields.Many2many('araymond.flow.checklist', compute='_compute_visible_rack_checks', string='Checklists par niveau')
    flow_line_ids = fields.One2many('araymond.flow.line', 'session_id', readonly=False)
    flow_preparation_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'preparation')])
    flow_risk_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'risk')])
    flow_simulation_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'simulation')])
    flow_day_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'day'), ('is_lx03', '=', False)])
    lx03_document_number = fields.Char('Numéro du document')
    flow_inventory_day_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'inventory_day')])
    flow_export_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'export')])
    flow_go_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'go')])
    flow_pre_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'pre')])
    flow_post_ids = fields.One2many('araymond.flow.line', 'session_id', domain=[('checklist_id.phase', '=', 'post')])
    approval_ids = fields.One2many('araymond.flow.approval', 'session_id', readonly=True)
    pending_approval_count = fields.Integer(compute='_compute_pending')
    unresolved_count_ids = fields.Many2many('araymond.inventory', compute='_compute_unresolved', inverse='_inverse_full_counts')
    manual_scan_count_ids = fields.One2many('araymond.inventory', 'location_session_id', domain=[('rec_scan_id.statusSession', '=', True)], string='Emplacements de scan')
    all_count_ids = fields.Many2many('araymond.inventory', compute='_compute_unresolved')

    @api.depends('rack_check_ids', 'rack_check_ids.phase', 'rack_check_ids.zone_id.workflow_state', 'rack_check_ids.zone_id.statusSession')
    def _compute_visible_rack_checks(self):
        for session in self:
            session.visible_rack_check_ids = session.rack_check_ids.filtered(
                lambda check: check.zone_id.statusSession
                and (check.phase == 'pre' or check.zone_id.workflow_state == 'done')
            )

    @api.depends('flow_check_ids', 'flow_check_ids.phase')
    def _compute_stage_validations(self):
        for session in self:
            session.preparation_validation_ids = session.flow_check_ids.filtered(
                lambda check: check.phase in ('preparation', 'risk', 'simulation'))
            session.inventory_day_validation_ids = session.flow_check_ids.filtered(
                lambda check: check.phase in ('inventory_day', 'day'))

    @api.depends('state', 'flow_check_ids', 'flow_check_ids.phase', 'flow_check_ids.zone_id.workflow_state')
    def _compute_visible_flow_checks(self):
        for session in self:
            # GO/NOGO validations are displayed in their dedicated tab.
            checks = session.flow_check_ids.filtered(lambda check: check.phase != 'go')
            if session.state not in ('Scan', 'Fermeture'):
                checks = checks.filtered(lambda check: check.phase not in ('pre', 'post'))
            checks = checks.filtered(lambda check: check.phase != 'post' or check.zone_id.workflow_state == 'done')
            session.visible_flow_check_ids = checks

    @api.depends('approval_ids.state')
    def _compute_pending(self):
        for session in self:
            session.pending_approval_count = 0

    def _inverse_full_counts(self):
        pass

    @api.depends('sess_records.statusSession', 'sess_records.recs_scan', 'sess_records.recs_scan.resolution_needed')
    def _compute_unresolved(self):
        for session in self:
            counts = session.sess_records.filtered('statusSession').recs_scan
            session.unresolved_count_ids = counts.filtered('resolution_needed')
            session.all_count_ids = counts

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        values.pop('preparation_line_ids', None)
        if 'preparation_workflow' in names:
            values['preparation_workflow'] = False
        if 'full_flow' in names:
            values['full_flow'] = True
        return values

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get('state', 'Draft') != 'Draft':
                raise UserError('Une nouvelle session commence à Création.')
            if not (values.get('desc') or '').strip():
                raise ValidationError('La description est obligatoire.')
            if {'flow_check_ids', 'go_check_ids', 'approval_ids', 'flow_line_ids'} & values.keys():
                raise AccessError('Les checklists sont générées automatiquement.')
            values.update(full_flow=True, preparation_workflow=False, state='Draft', typesession='InvGen')
        records = super(PreparedSession, self).create(vals_list)
        for session in records:
            session._make_checklists()
            audit(session, session, {}, snapshot(session, ['desc', 'datesession', 'entry_user_ids', 'control_user_ids']), event='Création de session')
        return records

    def _template_line_snapshot(self, template):
        values = {'description': template.name, 'sequence': template.sequence}
        if template.phase in ('preparation', 'inventory_day', 'pre', 'post'):
            values['responsible_id'] = template.responsible_id.id or False
        if template.phase == 'risk':
            values['responsible_ids'] = sorted(template.responsible_ids.ids)
        if template.phase in ('day', 'export'):
            values['file_name'] = template.file_name or False
        if template.phase == 'simulation':
            values.update(warehouse=template.warehouse or False,
                          sap_warehouse=template.sap_warehouse or False,
                          location_count=template.location_count)
        return values

    def _make_checklists(self, refresh_templates=False):
        self.ensure_one()
        for phase, _ in PHASES:
            if phase == 'export':
                continue
            if phase == 'go':
                self._sync_go_roles()
                continue
            zones = self.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan and z.checklist_selected) if phase in ('pre', 'post') else [False]
            for zone in zones:
                checklist = self.flow_check_ids.filtered(lambda c: c.phase == phase and c.zone_id == (zone or self.env['araymond.invsession']))
                if not checklist:
                    checklist = internal(self.env['araymond.flow.checklist'].sudo()).create({'session_id': self.id, 'phase': phase, 'zone_id': zone.id if zone else False})
                if checklist.validated_at and not refresh_templates:
                    continue
                for template in self.env['araymond.flow.template'].search([('phase', '=', phase), ('active', '=', True)]):
                    if template.is_lx03:
                        continue
                    line = self.env['araymond.flow.line'].with_context(active_test=False).search([
                        ('checklist_id', '=', checklist.id), ('template_id', '=', template.id),
                    ], limit=1)
                    source = self._template_line_snapshot(template)
                    if line:
                        # Deleted local snapshots stay archived. Ordinary lifecycle
                        # calls only add missing lines; explicit refresh updates them.
                        if not refresh_templates or not line.active:
                            continue
                        changed = {name: value for name, value in source.items()
                                   if not line.template_snapshot or line.template_snapshot.get(name) != value}
                        updates = {}
                        for name, value in changed.items():
                            current = sorted(line[name].ids) if name == 'responsible_ids' else line[name]
                            if current != value:
                                updates[name] = [(6, 0, value)] if name == 'responsible_ids' else value
                        before = snapshot(line, updates)
                        internal(line).write(dict(updates, template_snapshot=source))
                        if updates:
                            audit(line, self, before, snapshot(line, updates), phase,
                                  'Actualisation du modèle — %s' % template.name)
                        continue
                    values = dict(source)
                    if 'responsible_ids' in values:
                        values['responsible_ids'] = [(6, 0, values['responsible_ids'])]
                    internal(self.env['araymond.flow.line'].sudo()).create(dict(
                        values, checklist_id=checklist.id, template_id=template.id,
                        is_lx03=template.is_lx03, template_snapshot=source))
                    internal(checklist).write({'validated_by': False, 'validated_at': False})

    def action_refresh_flow(self):
        self.ensure_one()
        self._lock_preparation()
        if self.state == 'Fermeture':
            raise UserError('La session est clôturée.')
        before = self.flow_line_ids.ids
        self._make_checklists(refresh_templates=True)
        after = self.flow_line_ids.ids
        if set(before) != set(after):
            added = self.flow_line_ids.filtered(lambda line: line.id not in before)
            rows = [Markup('<li><strong>%s</strong> : %s</li>') %
                    (dict(PHASES)[line.phase], line.description) for line in added]
            body = Markup('<p><strong>Actualisation des modèles</strong></p><p>%s ligne(s) ajoutée(s).</p><ul>%s</ul>') % (len(added), Markup('').join(rows))
            audit(self, self, {'lignes': before}, {'lignes': after}, event='Actualisation des modèles', invalidate=False, message_body=body)

    def _flow_message(self, text):
        for session in self:
            session.message_post(body=Markup('<p>%s</p>') % text, subtype_xmlid='mail.mt_note')

    def _flow_check_approver(self, phase):
        if self.env.user.has_group('base.group_system'):
            return
        if not self.env['araymond.inventory.approver'].search_count([('phase', 'in', [phase, 'all']), ('user_ids', 'in', [self.env.uid])]):
            raise AccessError('Vous devez être désigné dans Paramètres → Responsables pour cette validation.')

    def _flow_store(self, values):
        return super(PreparedSession, internal(self)).write(values)

    def _require_approved(self, model=None, record_id=None):
        # Compatibility with existing callers: only checklist approvals gate
        # the workflow. Historical pending/rejected audit records are retained.
        return

    def write(self, values):
        if trusted(self):
            return super(PreparedSession, self).write(values)
        full = self.filtered('full_flow')
        legacy = self - full
        result = True
        if legacy:
            result = super(FullSession, legacy).write(values)
        for session in full:
            session._lock_preparation()
            if set(values) & {'state', 'full_flow', 'preparation_workflow', 'flow_check_ids', 'approval_ids', 'file_loaded', 'lxo3_records'}:
                raise AccessError('Utilisez les actions de validation et le bouton Charger LX03.')
            if set(values) & {'lx03_file', 'lx03_filename', 'lx03_document_number'} and session.state != 'InventoryDay':
                raise UserError('Le fichier LX03 et son numéro de document sont modifiables uniquement au Jour d’inventaire.')
            if session.state == 'Fermeture':
                raise UserError('La session est clôturée.')
            session_values = dict(values)
            if 'manual_scan_count_ids' in values:
                if session.state != 'Scan':
                    raise UserError('La saisie des emplacements est disponible à l’état Scan.')
                if not session.pre_scan_ready and any(command[0] == 0 for command in values['manual_scan_count_ids']):
                    raise UserError('Validez toutes les checklists avant scan avant de créer les emplacements.')
                commands = []
                for command in values['manual_scan_count_ids']:
                    if command[0] == 0:
                        location_values = dict(command[2])
                        zone_id = location_values.get('rec_scan_id')
                        zone = self.env['araymond.invsession'].browse(zone_id).exists()
                        if not zone:
                            zone = session._get_or_create_session_scan_pool()
                        location_values['rec_scan_id'] = zone.id if zone else False
                        command = (0, 0, location_values)
                        if not zone or zone.mainsession_id != session or not zone.statusSession:
                            raise ValidationError('Cet emplacement doit appartenir à la session en cours et être actif.')
                    elif command[0] not in (1, 2) or command[1] not in session.manual_scan_count_ids.ids:
                        raise AccessError('Modifiez uniquement les emplacements de cette session.')
                    commands.append(command)
                session_values['manual_scan_count_ids'] = commands

            if 'go_check_ids' in values:
                raise AccessError('Configurez les fonctions et validateurs depuis Paramètres → Responsables GO/NOGO Inventaire.')
            file_changed = 'lx03_file' in values and session.with_context(bin_size=False).lx03_file != values['lx03_file']
            previous_filename = session.lx03_filename
            if 'lx03_file' in values:
                session_values['file_loaded'] = False
            # One2many contexts are not forwarded by the form's parent write.
            # Resolve the checklist from the actual table receiving each line.
            for phase, _label in PHASES:
                field_name = 'flow_%s_ids' % phase
                if field_name not in values:
                    continue
                checklist = session.flow_check_ids.filtered(lambda check: check.phase == phase)
                if len(checklist) > 1:
                    raise UserError('Ouvrez la checklist du rack concerné dans l’onglet Scan.')
                commands = []
                for command in values[field_name]:
                    if command[0] == 0:
                        line_values = dict(command[2])
                        if line_values.get('checklist_id') and line_values['checklist_id'] != checklist.id:
                            raise ValidationError('La ligne doit appartenir à la checklist de ce tableau.')
                        line_values['checklist_id'] = checklist.id
                        commands.append((0, 0, line_values))
                    else:
                        commands.append(command)
                session_values[field_name] = commands
            # Checklist lines audit their own phase; do not invalidate all phases
            # again through the parent one2many command.
            audited_values = [name for name in values if name not in ('go_check_ids', 'manual_scan_count_ids') and not name.startswith('flow_') and 'assignment' not in name and not name.startswith('lx03_')]
            import_fields = [name for name in ('lx03_filename', 'lx03_document_number') if name in values]
            import_before = snapshot(session, import_fields)
            before = snapshot(session, audited_values)
            super(PreparedSession, session.with_context(mail_notrack=True)).write(session_values)
            audit(session, session, before, snapshot(session, audited_values))
            audit(session, session, import_before, snapshot(session, import_fields), 'day')
            if file_changed:
                audit(session, session, {'fichier': previous_filename}, {'fichier': session.lx03_filename or 'Fichier LX03'},
                      'day', 'Remplacement du fichier LX03' if previous_filename else 'Ajout du fichier LX03')
        return result

    def unlink(self):
        if self.filtered('full_flow'):
            raise AccessError('Conservez la session et ses validations dans l’historique.')
        return super().unlink()

    def action_next_flow(self):
        self.ensure_one()
        self._lock_preparation()
        self._require_approved()
        if self.state == 'Fermeture':
            return
        if any(line.user_id and not line.shift for line in self.assignment_ids):
            raise ValidationError('Renseignez le shift de chaque personne dans Affectation d’équipes avant de continuer.')
        index = [key for key, _ in STAGES].index(self.state)
        required = [phase for key, _ in STAGES[:index + 1] for phase in STAGE_PHASES.get(key, [])]
        for phase in required:
            checks = self.flow_check_ids.filtered(lambda c: c.phase == phase and (not c.zone_id or c.zone_id.statusSession))
            if not checks or any(not check._all_validators_signed() for check in checks):
                raise UserError('Tous les responsables affectés doivent valider la checklist %s avant de continuer.' % dict(PHASES)[phase])
        if self.state == 'Preparation':
            if not self.datesession or not self.entry_user_ids or not self.control_user_ids:
                raise ValidationError('Renseignez la date et les équipes de saisie et de contrôle de la session.')
            zones = self.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan)
            if not zones or any(not z.supervisor_id or not z.level or not z.shift or z.nbrEmp < 1 for z in zones):
                raise ValidationError('Complétez les racks, responsables, niveaux, shifts et nombres d’emplacements.')
        if self.state == 'InventoryDay':
            if any(not self._scan_assigned_users(slot) for slot in ('1', '2', '3')) or any(not line.shift for line in self.assignment_ids):
                raise ValidationError('Avant de démarrer le scan, configurez les trois équipes et le shift de chaque personne dans Affectation d’équipes → Équipe de scan.')
        if self.state == 'InventoryDay' and not (self.lx03_document_number or '').strip():
            raise ValidationError('Renseignez le numéro du document LX03 dans Extraction avant de démarrer le scan.')
        if self.state == 'InventoryDay' and (not self.lx03_file or not self.file_loaded or not self.lxo3_records):
            raise UserError('Chargez le fichier depuis le champ Fichier LX03 dans Extraction.')
        next_state = STAGES[index + 1][0]
        if self.state == 'InventoryDay':
            self._make_checklists()
            self._flow_store({'state': next_state})
        elif self.state == 'Scan':
            if self.unresolved_count_ids or not self.scans_completed:
                raise UserError('Terminez les scans et faites valider tous les arbitrages avant clôture.')
            for zone in self.sess_records.filtered('statusSession'):
                internal(zone.sudo()).action_close_invsession()
            self._flow_store({'state': 'Fermeture'})
        else:
            self._flow_store({'state': next_state})
        self._flow_message('Passage à l’état : %s' % dict(STAGES)[next_state])
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.constrains('entry_user_ids', 'control_user_ids')
    def _check_internal_flow_users(self):
        if any((self.entry_user_ids | self.control_user_ids).mapped('share')):
            raise ValidationError('Les équipes doivent être composées d’utilisateurs internes.')

    def action_load_file(self):
        if self.filtered('full_flow'):
            self.ensure_one()
            self.check_access('write')
            self._lock_preparation()
            if self.state != 'InventoryDay':
                raise UserError('LX03 se charge uniquement au Jour d’inventaire.')
            if not self.lx03_file:
                raise ValidationError('Sélectionnez un fichier LX03 avant de le charger.')
            if not (self.lx03_document_number or '').strip():
                raise ValidationError('Renseignez le numéro du document LX03 avant de le charger.')
            ARinventorySessionMain.action_load_file(internal(self))
            audit(self, self, {}, {'fichier': self.lx03_filename, 'lignes_importées': len(self.lxo3_records)}, 'day', 'Chargement LX03')
            # The form button refreshes its model when no action is returned.
            # Keep the existing notebook instead of rebuilding it on page one.
            return False
        return super().action_load_file()

    def action_open_configuration(self):
        self.ensure_one()
        if self.full_flow:
            return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'views': [(self.env.ref('ar_general_inventory.view_full_flow_session').id, 'form')]}
        return super().action_open_configuration()

    def action_open_scan(self):
        if self.full_flow:
            action = self.action_open_configuration()
            action['context'] = dict(self.env.context, inventory_scan_menu=True)
            return action
        return super().action_open_scan()

    def action_close_scan(self):
        return self.action_next_flow() if self.full_flow else super().action_close_scan()

    def _sync_zone_closure(self):
        return super(FullSession, self.filtered(lambda s: not s.full_flow))._sync_zone_closure()

    def action_creation(self):
        return self.action_next_flow() if self.full_flow else super().action_creation()

    def action_scan_ok(self):
        return self.action_next_flow() if self.full_flow else super().action_scan_ok()

    def action_start_scan(self):
        return self.action_next_flow() if self.full_flow else super().action_start_scan()

    def action_mod_empl(self):
        if self.full_flow:
            raise UserError('Utilisez les étapes du nouveau parcours.')
        return super().action_mod_empl()


class FullZone(models.Model):
    _inherit = 'araymond.invsession'
    shift = fields.Selection([('A', 'Équipe A'), ('B', 'Équipe B'), ('C', 'Équipe C'), ('admin', 'Administratif')], string='Shift')

    @api.depends('resume', 'level', 'mainsession_id.full_flow')
    def _compute_display_name(self):
        super()._compute_display_name()
        for zone in self.filtered(lambda row: row.mainsession_id.full_flow):
            zone.display_name = '%s — Niveau %s' % (zone.resume or zone.rack, zone.level or 'à renseigner')

    def _check_supervisor(self):
        # Automatic startup is already authorized by every checklist validator.
        # The private token cannot be supplied by a browser/RPC caller.
        if trusted(self) and all(zone.mainsession_id.full_flow for zone in self):
            return
        return super()._check_supervisor()

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            session = self.env['araymond.maininvsession'].browse(values.get('mainsession_id'))
            if session.full_flow and session.state != 'Preparation' and not (trusted(self) and values.get('is_session_scan')):
                raise UserError('Les racks se créent à l’étape Préparation.')
        records = super().create(vals_list)
        for record, values in zip(records, vals_list):
            audit(record, record.mainsession_id, {}, snapshot(record, values), event='Création de rack')
            if record.mainsession_id.full_flow:
                record.mainsession_id._make_checklists()
        return records

    def write(self, values):
        before = {record.id: snapshot(record, values) for record in self}
        for zone in self:
            if zone.mainsession_id.full_flow and not trusted(self) and zone.mainsession_id.state == 'Fermeture':
                raise UserError('Les racks sont verrouillés après clôture.')
            if zone.mainsession_id.full_flow and 'mainsession_id' in values and values['mainsession_id'] != zone.mainsession_id.id:
                raise UserError('Un rack ne peut pas être déplacé vers une autre session.')
        result = super().write(values)
        for record in self:
            audit(record, record.mainsession_id, before[record.id], snapshot(record, values))
        return result

    def unlink(self):
        if self.filtered(lambda z: z.mainsession_id.full_flow):
            raise UserError('Désactivez le rack plutôt que de supprimer son historique.')
        return super().unlink()

    def _sync_checklist(self):
        return super(FullZone, self.filtered(lambda z: not z.mainsession_id.full_flow))._sync_checklist()

    def action_close_invsession(self):
        for zone in self.filtered(lambda z: z.mainsession_id.full_flow):
            session = zone.mainsession_id
            session._require_approved()
            if not session.flow_check_ids.filtered(lambda c: c.phase == 'post' and c.zone_id == zone).validated_at or zone.recs_scan.filtered('resolution_needed'):
                raise UserError('Validez les arbitrages et la checklist après scan avant clôture.')
        return super().action_close_invsession()

    def _assign_automatic_arbitration(self):
        if not self.mainsession_id.full_flow:
            return super()._assign_automatic_arbitration()
        self.ensure_one()
        conflicts = self.first_count_ids.filtered(lambda c: c.needs_arbitration and not c.final_count_id)
        if not conflicts:
            return
        team = self.mainsession_id._ensure_automatic_teams(('3',))
        self._set_workflow({'arbitration_team_id': team.id})
        for original in self.first_count_ids:
            if not self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'arbitration'):
                original._set_count({'needs_arbitration': True, 'final_count_id': False})
                self._new_count(original, team, 'arbitration')
        self.mainsession_id._flow_message('Écart détecté : recomptage de tous les emplacements du rack %s par Équipe 3.' % self.rack)

    def _advance_counts(self):
        self.ensure_one()
        if not self.mainsession_id.full_flow:
            return super()._advance_counts()
        self.mainsession_id._lock_preparation()
        if self.workflow_state != 'arbitration':
            previous = self.workflow_state
            result = super()._advance_counts()
            if self.workflow_state != previous:
                self.mainsession_id._flow_message('Rack %s : %s → %s' % (self.rack, previous, self.workflow_state))
            return result
        for original in self.first_count_ids.filtered(lambda c: c.needs_arbitration and not c.final_count_id):
            third = self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'arbitration').sorted('count_round')[-1:]
            if not third or not third.ok_done:
                continue
            cross = self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'cross')
            matches = (original | cross).filtered(lambda c: c.emplacementVide == third.emplacementVide and same_quantities(c._quantities(), third._quantities()))
            if matches:
                original._set_count({'final_count_id': third.id, 'resolution_needed': False})
                self.mainsession_id._flow_message('Emplacement %s : résultat commun retenu après le troisième scan.' % original.emplacement)
            elif not original.resolution_needed:
                original._set_count({'resolution_needed': True})
                self.mainsession_id._flow_message('ALERTE : trois résultats différents pour %s. Validation du superviseur ou nouveau scan obligatoire.' % original.emplacement)
        if self.first_count_ids and all(count.final_count_id for count in self.first_count_ids):
            self._set_workflow({'workflow_state': 'done'})


class FullCount(models.Model):
    _inherit = 'araymond.inventory'
    location_session_id = fields.Many2one(related='rec_scan_id.mainsession_id', store=True, index=True)
    location_workflow_state = fields.Selection(related='rec_scan_id.workflow_state')
    location_level = fields.Selection(related='rec_scan_id.level', string='Niveau')
    count_round = fields.Integer(default=1, readonly=True)
    resolution_needed = fields.Boolean('Validation du superviseur requise', readonly=True)
    supervisor_choice_id = fields.Many2one('araymond.inventory', string='Résultat retenu')
    resolution_comment = fields.Text('Justification du superviseur')
    resolved_by = fields.Many2one('res.users', readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    _team_location_unique = models.Constraint('UNIQUE(rec_scan_id, emplacement, count_phase, count_round)', 'Cette mission existe déjà.')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        session_id = self.env.context.get('manual_location_session_id')
        if session_id and 'rec_scan_id' in names and not values.get('rec_scan_id'):
            # The session form resolves new locations to its shared scan pool on save.
            values['rec_scan_id'] = False
        return values

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self) and any(set(values) & {'count_round', 'resolution_needed', 'resolved_by', 'resolved_at'} for values in vals_list):
            raise AccessError('Les recomptages sont créés par les actions du superviseur.')
        records = super().create(vals_list)
        for record, values in zip(records, vals_list):
            audit(record, record.rec_scan_id.mainsession_id, {}, snapshot(record, values), event='Création d’emplacement')
        return records

    def _check_scan(self):
        for count in self:
            session = count.rec_scan_id.mainsession_id
            if session.full_flow:
                checks = session.flow_check_ids
                if not count.rec_scan_id.is_session_scan:
                    checks = checks.filtered(lambda c: c.zone_id == count.rec_scan_id)
                if self.env.user not in session._scan_assigned_users(count.branch_id.automatic_slot):
                    raise AccessError('Vous ne faites pas partie de cette équipe de scan.')
                pre = checks.filtered(lambda c: c.phase == 'pre')
                if not pre or any(not c._all_validators_signed() for c in pre):
                    raise UserError('Validez la checklist avant scan de ce rack.')
                if any(checks.filtered(lambda c: c.phase == 'post').mapped('validated_at')):
                    raise UserError('La checklist après scan est déjà validée.')
        return super()._check_scan()

    def write(self, values):
        if set(values) & {'count_round', 'resolution_needed', 'resolved_by', 'resolved_at'} and not trusted(self):
            raise AccessError('Utilisez les actions du superviseur.')
        before = {record.id: snapshot(record, values) for record in self}
        if set(values) & {'supervisor_choice_id', 'resolution_comment'}:
            for record in self:
                record._require_supervisor()
        if set(values) <= {'supervisor_choice_id', 'resolution_comment'}:
            result = self._set_count(values)
        else:
            result = super().write(values)
        for record in self:
            audit(record, record.rec_scan_id.mainsession_id, before[record.id], snapshot(record, values))
        return result

    def action_close(self):
        self.ensure_one()
        session = self.rec_scan_id.mainsession_id
        if not session.full_flow:
            return super().action_close()
        self._check_scan()
        session._require_approved()
        if not self.su and not self.emplacementVide:
            raise ValidationError('Scannez les étiquettes ou déclarez l’emplacement vide.')
        if self.su and self.emplacementVide or any(not line.ref for line in self.su):
            raise ValidationError('Vérifiez les étiquettes et la déclaration d’emplacement vide.')
        self._quantities()
        self._set_count({'ok_done': True})
        audit(self, session, {'ok_done': False}, {'ok_done': True}, event='Fin de comptage')
        self.rec_scan_id._advance_counts()
        return {'type': 'ir.actions.act_window_close'}

    can_resolve_alert = fields.Boolean(compute='_compute_can_resolve_alert')

    @api.depends_context('uid')
    @api.depends('rec_scan_id.supervisor_id', 'resolution_needed', 'rec_scan_id.mainsession_id.state')
    def _compute_can_resolve_alert(self):
        for count in self:
            count.can_resolve_alert = (count.resolution_needed
                                       and count.rec_scan_id.mainsession_id.state == 'Scan'
                                       and count.rec_scan_id.supervisor_id == self.env.user)

    def _log_alert_decision(self, decision, count, date):
        zone = self.rec_scan_id
        shown_date = fields.Datetime.context_timestamp(self, date).strftime('%d/%m/%Y %H:%M:%S')
        quantities = '; '.join('%s / Lot %s : %s' % (key[0], key[1] or '—', value)
                               for key, value in sorted(count._quantities().items())) or 'Aucune quantité'
        body = Markup('<p><strong>%s</strong></p><ul>'
                      '<li>Responsable : %s</li><li>Date : %s</li>'
                      '<li>Rack : %s — Niveau : %s — Magasins : %s / %s</li>'
                      '<li>Emplacement : %s</li><li>Relevé : #%s — Équipe : %s — Mission : %s — Tour : %s</li>'
                      '<li>Résultat : %s</li><li>Justification : %s</li></ul>') % (
                          decision, self.env.user.name, shown_date, zone.rack, zone.level or '—',
                          zone.magasinPhysique, zone.magasinSAP, self.emplacement, count.id,
                          count.branch_id.display_name, count.count_phase, count.count_round,
                          'Emplacement vide' if count.emplacementVide else quantities, self.resolution_comment)
        zone.mainsession_id.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _require_supervisor(self):
        self.ensure_one()
        session = self.rec_scan_id.mainsession_id
        session._lock_preparation()
        if not session.full_flow or session.state != 'Scan' or not self.resolution_needed:
            raise UserError('Cet emplacement ne nécessite pas de décision du superviseur.')
        if self.env.user != self.rec_scan_id.supervisor_id:
            raise AccessError('Seul le responsable affecté à ce rack peut décider de cette alerte.')

    def action_supervisor_accept(self):
        self._require_supervisor()
        self.rec_scan_id.mainsession_id._require_approved()
        choice = self.supervisor_choice_id
        if not choice.ok_done or (choice != self and choice.origin_count_id != self) or not (self.resolution_comment or '').strip():
            raise ValidationError('Sélectionnez un relevé terminé de cet emplacement et justifiez la décision.')
        date = fields.Datetime.now()
        self._set_count({'final_count_id': choice.id, 'resolution_needed': False, 'resolved_by': self.env.uid, 'resolved_at': date})
        self._log_alert_decision('Alerte validée — résultat retenu', choice, date)
        self.rec_scan_id._advance_counts()

    def action_supervisor_rescan(self):
        self._require_supervisor()
        self.rec_scan_id.mainsession_id._require_approved()
        if not (self.resolution_comment or '').strip():
            raise ValidationError('Justifiez le nouveau scan.')
        zone = self.rec_scan_id
        rounds = zone.recs_scan.filtered(lambda c: c.origin_count_id == self and c.count_phase == 'arbitration')
        if zone.is_session_scan:
            rounds = zone.recs_scan.filtered(lambda c: c.emplacement == self.emplacement
                                             and c.count_phase == 'arbitration'
                                             and c.branch_id == zone.arbitration_team_id)
        # Same branch, new immutable mission; prior counts and labels remain visible.
        from .team_workflow import TeamCount
        new = super(TeamCount, internal(self.env['araymond.inventory'])).create({
            'rec_scan_id': zone.id, 'mainsession_id': str(zone.mainsession_id.id), 'emplacement': self.emplacement,
            'branch_id': zone.arbitration_team_id.id, 'count_phase': 'arbitration', 'origin_count_id': self.id,
            'location_rack': self.location_rack, 'scan_first': self.scan_first,
            'count_round': max(rounds.mapped('count_round') or [0]) + 1, 'typedegalia': self.typedegalia,
        })
        self._set_count({'resolution_needed': False, 'supervisor_choice_id': False})
        self._log_alert_decision('Nouveau scan demandé par le responsable du rack', new, fields.Datetime.now())


class FullLabel(models.Model):
    _inherit = 'araymond.etiquette'
    count_round = fields.Integer(related='su_id.count_round', store=True)
    _team_label_unique = models.Constraint('UNIQUE(branch_id, count_phase, count_round, resume)', 'Unité déjà scannée pour cette mission.')

    def _duplicate_domain(self, vals):
        return super()._duplicate_domain(vals) + [('count_round', '=', self.env['araymond.inventory'].browse(vals.get('su_id')).count_round)]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            audit(record, record.su_id.rec_scan_id.mainsession_id, {}, snapshot(record, ['numEtiq', 'ref', 'lot', 'qty', 'su_id']), event='Saisie de scan')
        return records

    def write(self, values):
        before = {record.id: snapshot(record, values) for record in self}
        result = super().write(values)
        for record in self:
            audit(record, record.su_id.rec_scan_id.mainsession_id, before[record.id], snapshot(record, values))
        return result

    def unlink(self):
        for record in self:
            audit(record, record.su_id.rec_scan_id.mainsession_id, {}, {'suppression': snapshot(record, ['numEtiq', 'ref', 'lot', 'qty'])}, event='Suppression de saisie')
        return super().unlink()


class FullLX03(models.Model):
    _inherit = 'araymond.lxo3'

    def _check_import_stage(self, sessions):
        if sessions.filtered('full_flow') and not trusted(self):
            raise AccessError('Importez LX03 depuis la checklist Jour d’inventaire.')
        return super()._check_import_stage(sessions)


class FlowAttachment(models.Model):
    _inherit = 'ir.attachment'

    def write(self, values):
        lines = self.env['araymond.flow.line'].sudo().search([('attachment_ids', 'in', self.ids)]) if self.ids and {'raw', 'datas', 'name', 'mimetype'} & values.keys() else self.env['araymond.flow.line']
        if any(line.is_lx03 and line.session_id.state != 'InventoryDay' for line in lines):
            raise UserError('Le fichier LX03 utilisé est figé après le Jour d’inventaire.')
        before = {line.id: [(a.id, a.name, a.checksum) for a in line.attachment_ids] for line in lines}
        result = super().write(values)
        for line in lines.with_user(self.env.user):
            audit(line, line.session_id, {'pièces_jointes': before[line.id]}, {'pièces_jointes': [(a.id, a.name, a.checksum) for a in line.attachment_ids]}, line.phase, 'Modification de pièce jointe')
            if line.is_lx03:
                line.session_id._flow_store({'file_loaded': False})
        return result

    def unlink(self):
        lines = self.env['araymond.flow.line'].sudo().search([('attachment_ids', 'in', self.ids)])
        if lines:
            raise UserError('Retirez la pièce jointe de sa checklist pour conserver la traçabilité de la modification.')
        return super().unlink()
