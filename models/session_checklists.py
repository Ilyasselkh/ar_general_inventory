"""Session checklist snapshots, retained independently from zone scan missions."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


PREPARATION = [
    'Créer la base de données du premier comptage',
    'Créer la base de données du deuxième et troisième comptage',
    'Préparer les sessions d’utilisateurs pour les personnes qui vont faire le comptage',
    'Créer les sessions d’inventaire sur le système interne d’inventaire',
    'Activer la sauvegarde automatique des bases de données d’inventaire pour chaque heure',
    'Créer le dossier archive sur le réseau (procédure d’inventaire générale, partie 10)',
    'Injecter la liste de toutes les références existantes sur SAP',
    'Vider les emplacements PACK-901, PACK-902, PACK-903, TRANSFER et TR-ZONE (LX03)',
    'Vider la liste COGI',
    'Déclarer tout le SCRAP',
    'Débloquer le stock « En contrôle qualité » (MB52)',
    'Débloquer le stock « Stock non libre » (MB52)',
    'Débloquer le stock « Bloqué » (MB52)',
    'Débloquer le stock « Retours » (MB52)',
    'Vérifier les accès aux transactions pour la saisie et le posting',
    'Corriger le master data pour toutes les références actives',
    'Former les utilisateurs sur l’utilisation du système interne d’inventaire',
    'Former les utilisateurs sur la saisie des données sur SAP',
]
INVENTORY_DAY = [
    'Assurer que les mouvements de stocks et de production sont arrêtés : aucune réception, expédition ou sortie de produits (procédure, partie 6)',
    'Relever les derniers numéros des documents relatifs aux mouvements de stocks avant le début de l’opération',
    'Lancer l’extraction de données de tous les magasins par lots (MB52)',
    'Lancer une extraction de tous les magasins avec la transaction LX03',
    'Lancer une extraction de tous les magasins avec la transaction LX17',
    'Faire une extraction COGI, prendre une capture d’image si vide',
    'Faire une extraction BOM',
    'Faire une extraction des prix d’articles (MM60)',
    'Créer les documents d’inventaire',
    'Vérifier que tous les emplacements ont un document d’inventaire',
    'Lancer l’extraction des scans depuis le système interne d’inventaire',
    'Vérifier que l’ensemble des emplacements SAP est égal aux emplacements scannés',
    'Vérifier que les lots scannés sont bien créés dans SAP',
    'Vérifier que les étiquettes palettes scannées sont bien créées dans SAP',
    'Faire le rapprochement du stock SAP vs stock physique et stock physique vs stock SAP',
    'Analyser les résultats (procédure d’inventaire générale, partie 7)',
    'Vérifier les TOP 10 écarts positifs ou négatifs par magasin',
    'Lancer les recomptages des références avec un grand écart',
]
PHASES = [('preparation', 'Préparation'), ('day', 'Jour d’inventaire — avant scan'),
          ('post', 'Jour d’inventaire — après scan')]


class ChecklistApprover(models.Model):
    _name = 'araymond.inventory.approver'
    _description = 'Responsable de validation des checklists'
    _rec_name = 'phase'

    # Retained only to migrate previous single-user assignments.
    user_id = fields.Many2one('res.users', ondelete='set null', readonly=True,
                             string='Ancien responsable')
    user_ids = fields.Many2many(
        'res.users', 'ar_inventory_approver_user_rel', 'approver_id', 'user_id',
        string='Responsables', required=True, domain=[('share', '=', False)],
    )
    active = fields.Boolean(default=True)
    phase = fields.Selection(PHASES + [('all', 'Toutes les checklists')],
                             string='Checklist à valider', required=True, default='all')
    @api.model_create_multi
    def create(self, vals_list):
        assignments = super().create(vals_list)
        assignments._check_internal()
        return assignments

    @api.constrains('user_ids')
    def _check_internal(self):
        for assignment in self:
            if not assignment.user_ids:
                raise ValidationError('Sélectionnez au moins un responsable pour cette checklist.')
            if any(assignment.user_ids.mapped('share')):
                raise ValidationError('Choisissez uniquement des utilisateurs internes.')


class SessionChecklist(models.Model):
    _name = 'araymond.session.checklist'
    _description = 'Checklist de session d’inventaire'
    _order = 'id'

    session_id = fields.Many2one('araymond.maininvsession', required=True, ondelete='cascade')
    phase = fields.Selection(PHASES, required=True)
    line_ids = fields.One2many('araymond.session.checklist.line', 'checklist_id')
    validated_by = fields.Many2one('res.users', readonly=True, string='Validée par', copy=False)
    validated_at = fields.Datetime(readonly=True, string='Validée le', copy=False)
    _phase_unique = models.Constraint('UNIQUE(session_id, phase)', 'Cette checklist existe déjà.')

    def _check_edit(self):
        for checklist in self:
            checklist.session_id._lock_preparation()
            checklist.invalidate_recordset()
            expected = {'preparation': 'Preparation', 'day': 'InventoryDay', 'post': 'Scan'}[checklist.phase]
            if checklist.validated_at or checklist.session_id.state != expected:
                raise UserError('Cette checklist est verrouillée ou son étape n’est pas encore ouverte.')
            if checklist.phase == 'post' and not checklist.session_id.scans_completed:
                raise UserError('Terminez les scans et les recomptages de tous les racks actifs avant de remplir les contrôles après scan.')

    def action_validate(self):
        self.ensure_one()
        self._check_edit()
        session = self.session_id
        session._check_approver(self.phase)
        if not self.line_ids or any(not line.answer for line in self.line_ids):
            raise ValidationError('Répondez OK ou NOK à tous les points.')
        if any(line.answer == 'nok' and not (line.comment or '').strip() for line in self.line_ids):
            raise ValidationError('Expliquez chaque réponse NOK dans les commentaires.')
        # Private ORM path: audit fields cannot be supplied through public write RPC.
        super(SessionChecklist, self).write({'validated_by': self.env.uid, 'validated_at': fields.Datetime.now()})
        if self.phase != 'post':
            session._set_preparation_state('Racks' if self.phase == 'preparation' else 'LX03')
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def write(self, values):
        raise AccessError('Utilisez les lignes et les boutons de validation de la checklist.')


class SessionChecklistLine(models.Model):
    _name = 'araymond.session.checklist.line'
    _description = 'Point de checklist de session'
    _order = 'sequence, id'

    checklist_id = fields.Many2one('araymond.session.checklist', required=True, ondelete='cascade')
    sequence = fields.Integer(string='N°')
    instruction = fields.Text(string='Éléments à vérifier', required=True)
    responsible_role = fields.Char(string='Responsable prévu')
    responsible_id = fields.Many2one('res.users', string='Responsable', domain=[('share', '=', False)])
    answer = fields.Selection([('ok', 'OK'), ('nok', 'NOK')], string='Statut')
    comment = fields.Text(string='Commentaires')
    attachment_ids = fields.Many2many(
        'ir.attachment', 'ar_checklist_line_attachment_rel', 'line_id', 'attachment_id',
        string='Attachement', copy=False,
    )
    validated_at = fields.Datetime(related='checklist_id.validated_at')

    def write(self, values):
        if set(values) - {'responsible_id', 'answer', 'comment', 'attachment_ids'}:
            raise AccessError('Les points de la checklist sont figés pour cette session.')
        self.checklist_id._check_edit()
        return super().write(values)
