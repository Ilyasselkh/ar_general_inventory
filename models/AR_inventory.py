# -*- coding: utf-8 -*-
import json
from odoo import models, fields, api, exceptions
from io import BytesIO
import base64
import re  # Regular expression module for cleansing


class ARinventory(models.Model):
    _name = 'araymond.inventory'
    _description = 'Araymond inventory'


    scanraypro = fields.Char(string='Scannez', help="Scannez", default="")
    scanrecp = fields.Char(string='Scannez', help="Scannez", default="")
    scanlog = fields.Char(string='Scannez', help="Scannez", default="")
    #scanman = fields.Char(string='Scannez', help="Scannez", default="")

    ref = fields.Char(string='Reference (manuel = Z)', help="reference (manuel = Z)", default="")
    qty = fields.Char(string='Quatité (manuel = K)', help="Quatité (manuel = K)", default="")
    lot = fields.Char(string='Lot (manuel = L)', help="Lot (manuel = L)", default="")
    hu = fields.Char(string='HU(man=N)', help="HU  (manuel = B)", default="")

    #TODO : changer le champs QTY... to float
    scanNbr = fields.Integer(string='Numero de scan :', required=True, default=1, tracking=True)


    EmpAnomaly = fields.Boolean(string="Emplacement en anomalie", default=False, tracking=True)

    active = fields.Boolean(string="active", default="True", tracking=True)
    ok_done = fields.Boolean(string="Scan terminé", default=False, tracking=True)
    
    numEtiquet = fields.Char(string='Num Etiquette', help="Num Etiquette", default="", tracking=True)

    emplacement = fields.Char(string='Emplacement', tracking=True)
    um = fields.Char(string='UM', tracking=True)
    um2 = fields.Char(string='UM 2eme scan', tracking=True)
    um2Active = fields.Boolean(string="need UM 2eme scan", default=False)
    EmpAnomaly = fields.Boolean(string="Emplacement en anomalie", default=False, tracking=True)
    ummondatory = fields.Boolean(string="UM obligatoire",compute="get_um_data", store="True", tracking=True)
    dummondatory = fields.Boolean(string="DUM obligatoire",compute="get_dum_data", store="True", tracking=True)

    mainsession_id = fields.Char(string='mainsession_id', tracking=True)
    commentaire = fields.Char(string='commentaire', tracking=True)

    is_admin = fields.Boolean(compute="_compute_is_admin", store=False)
    def _compute_is_admin(self):
        for record in self:
            record.is_admin = self.env.user.has_group('base.group_system')
    filtered_su = fields.One2many('araymond.etiquette', 'su_id', compute="_compute_filtered_su", store=False)

    @api.depends('scanraypro' , 'su', 'scanlog', 'scanrecp', 'scanNbr')
    def _compute_filtered_su(self):
        for rec in self:
            rec.filtered_su = self.env['araymond.etiquette'].search([('su_id', '=', rec._origin.id), ('scanNbr', '=', rec.scanNbr)])


    #def write(self, vals):
        # Call the super class write method
        #result = super().write(vals)

        # Check if ok_done was updated
        #for record in self:
        #    if 'ok_done' in vals:
        #        action_mod_empl_flag = self.env.context.get('action_mod_empl')
        #        
        #        # If the action is from action_mod_empl, ignore the state update and remove the flag
        #        if action_mod_empl_flag:
        #            # Remove the context flag if it exists
        #            del self.env.context['action_mod_empl']
        #        else:
        #            session = record.rec_scan_id
        #            if session:
        #                # Check if there's at least one related inventory record and if all are ok_done
        #                recs = session.recs_scan
        #                if recs and len(recs) > 0:  # Ensuring there are related records
        #                    all_done = all(rec.ok_done for rec in recs)
        #                    if all_done:
        #                        session.state = 'Fermeture'
        
       # return result



    count_box_scanne = fields.Integer(string="nbr box", compute='_count_nbr_bx', default=0, store=True)

    @api.depends('scanraypro' , 'su', 'scanlog', 'scanrecp')
    def _count_nbr_bx(self):
        for rec in self:
            
            rec.count_box_scanne = self.env['araymond.etiquette'].search_count([('su_id', '=', self._origin.id ), ('scanNbr', '=', rec.scanNbr)])
            #rec.su = self.env['araymond.etiquette'].search([('su_id', '=', self._origin.id )])
            inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id )])
            #inv = self.env['araymond.inventory'].browse(self._origin.id )
            rec.emplacement = inv.emplacement
            rec.um = inv.um
            rec.emplacementVide = inv.emplacementVide
            rec.su = inv.su
            

    emplacementVide = fields.Boolean(string='Emplacement vide', tracking=True)

    @api.onchange('emplacementVide')
    def ch_affect_db(self):
        for rec in self:

            if rec.count_box_scanne>0:
                rec.emplacementVide = False
                return {'warning': {
                    'title': 'des etiquettes déja scannées',
                    'message': ("des etiquettes déja scannées"),
                }}
                #raise exceptions.ValidationError("des etiquettes déja scannées")
            #forcer l'ecriture dans la BD
            inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
            inv.write({'emplacementVide':rec.emplacementVide})     

      
    emplacementVidefieldCheck = fields.Boolean(string='Field Emplacement vide or not', compute="ch_emplacement_vide", store=True)
    

    @api.depends('rec_scan_id.ummondatory')
    def get_um_data(self):
        for record in self:
            record.ummondatory = record.rec_scan_id.ummondatory

    @api.depends('rec_scan_id.dummondatory')
    def get_dum_data(self):
        for record in self:
            record.dummondatory = record.rec_scan_id.dummondatory


    typedegalia = fields.Selection([('RayPro', 'RayPro'),('ReceptionSAP', 'Reception SAP'),('logistique', 'logistique'),('Manuel', 'Manuel')] , string='Type de galia', default='RayPro' )
    dum = fields.Char(string ='DUM', default="")
    modeetiquette = fields.Char(string='modeetiquette', help="modeetiquette", default="")
    modeetiquette1 = fields.Char(string='modeetiquette1', help="modeetiquette1", default="")
    

    su= fields.One2many('araymond.etiquette', 'su_id', tracking=True)

    rec_scan_id = fields.Many2one('araymond.invsession', ondelete ='cascade', tracking=True, string="inv session ID")

    



    #TODO : ne pas depacer le nmbr des emplacement defini vs scané
    #TODO : controle UC
    #TODO : ajouter dans l'interface le nmbr scanné total 
    
    #TODO : is digit to check qty .... 
    #TODO : authorisation des suppression des lignes ( scan )
    #TODO : corriger manuel pour la reception

    #TODO : ERREUR ETIQUETTE DEJA SCANNE A AJUSTER AVEC RETURN
    #TODO : ajouter prefix de UM qui commence par 600
    #TODO : ajouter le filter UM mondatory pour le FG 


    #TODO ==> done: autosave dans la fenetre scan pour ne pas perdre 
    #TODO ==> done : ajuster et corriger les messages d'erreur ==> DONE
    #TODO ==> done : AJOUTER MSG EN CAS DACCEPTATION BOX
    #TODO ==> done : UM est obligatoire pour FG & not for MP
    #TODO ==> done: ajouter le QR code V1 pour raypro
    #TODO ==> done : replace raise by return

    #note : 
    #       raypro ==> manuel sans + 
    #       reception ==> manuel avec prefix Z
    #       logistique ==> voir String 
    #       manuel ==> remove read only for all fields 


    #@api.model
    #def create(self, vals_list):
    #    raise exceptions.ValidationError("XXX")
    #    result = super(ARinventory, self).create(vals_list)
    #    empl = vals_list.get('emplacement', None)
    #    if empl == False or empl == "":
    #            raise exceptions.ValidationError("Merci de rensigner l'emplacement")
    #    return super(ARinventory, self).create(vals_list)

    #def write(self, values):
    #    raise exceptions.ValidationError("ZZZ")
    
    @api.depends('emplacement')
    def ch_emplacement_vide(self):
        for rec in self:
            if rec.emplacement == False or rec.emplacement == "" or len(rec.emplacement) != 4:
                rec.emplacementVidefieldCheck = False
            else:
                rec.emplacementVidefieldCheck = True 

    #TODO : add confirmation box
    def unlinkinv(self):
        inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
        inv.write({'active':False})
        
        return {'warning': {
            'title': 'Erreur',
            'message': ("Emplacement bien supprimé"),
        }}
        #raise exceptions.ValidationError("Emplacement bien supprimé")

    #TODO: delete popup on click on the tree
    def action_view_form_inv(self):
        rec_id = inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)]).id
        #raise exceptions.ValidationError(self.env.ref("ar_general_inventory.view_araymond_inventory_form"))
        view = self.env.ref('ar_general_inventory.view_araymond_inventory_form')
        return {
            'type' :  'ir.actions.act_window',
            'view_type' : 'form',
            'view_mode' : 'form',
            'res_model' : 'araymond.inventory',
            'name': 'title',
            'views': [(view.id,'form')],    
            'view_id' : view.id,
            'res_id': rec_id,
            'context': self.env.context,
            'flags': {'initial_mode': 'edit'},
            'target': 'current',
        }
        #return {

        #    'type' : 'ir.actions.act_window',

        #    'view_type' : 'form',

        #    'view_mode' : 'form',

        #    'res_model' : 'araymond.inventory',

        #    'views': [(view.id,'form')],

        #    'view_id':view.id ,

        #    'res_id': self.id,

        #    'context': self.env.context

            #'flags': {'initial_mode': 'edit'},
            #'target': 'current',

        #}

        #return {
        #    'name': _('test'),
        #    'view_type': 'tree',
        #    'view_mode': 'tree',
        #    'view_id': self.env.ref('view_araymond_inventory_form').id,
        #    'res_model': 'account.invoice',
        #    'context': "{'type':'out_invoice'}",
        #    'type': 'ir.actions.act_window',
        #    'target': 'new',
        #}  

    def action_raypro(self):
        self.typedegalia = "RayPro"

        self.scanraypro = ''
        self.scanrecp = ''
        self.scanlog = ''
        self.ref =''
        self.hu = ''
        self.qty = ''
        self.lot = ''
        self.numEtiquet = ''
        self.um = ''



    def action_receptionSAP(self):
        self.typedegalia = "ReceptionSAP"
 
        self.scanraypro = ''
        self.scanrecp = ''
        self.scanlog = ''
        self.ref =''
        self.hu = ''
        self.qty = ''
        self.lot = ''
        self.numEtiquet = ''
        self.um = ''
    
    def action_logistique(self):
        self.typedegalia = "logistique"
        self.scanraypro = ''
        self.scanrecp = ''
        self.scanlog = ''
        self.ref =''
        self.hu = ''
        self.qty = ''
        self.lot = ''
        self.numEtiquet = ''
        self.um = ''
    def action_Manuel(self):
        self.typedegalia = "Manuel"
        self.scanraypro = ''
        self.scanrecp = ''
        self.scanlog = ''
        self.ref =''
        self.hu = ''
        self.qty = ''
        self.lot = ''
        self.numEtiquet = ''
        self.um = ''
        self.dum = ''


    def get_lxo3_pivot_data(self):
        lxo3_model = self.env['araymond.lxo3']
        main_session_id = self.rec_scan_id.mainsession_id.id
        # Example filtering; adjust as necessary for your specific use case
        lxo3_records = lxo3_model.search([
            ('session_id', '=', main_session_id),
            ('type_magasin', '=', self.rec_scan_id.magasinPhysique),
            ('emplacement', '=', self.emplacement)
        ])

        pivot_data_lx03 = {}

        for record in lxo3_records:
            article = self._normalize_compare_text(record.article)
            lot = self._normalize_compare_text(record.lot)
            key = ( record.session_id, article, lot)
            if key not in pivot_data_lx03:
                pivot_data_lx03[key] = {
                    'session_id' : record.session_id,
                    'article': article,
                    'lot': lot,
                    'stock_total': 0.0
                }
            pivot_data_lx03[key]['stock_total'] += record.stock_total

        return list(pivot_data_lx03.values())

    def get_lxo3_su(self):
        lxo3_model = self.env['araymond.lxo3']
        main_session_id = self.rec_scan_id.mainsession_id.id
        # Example filtering; adjust as necessary for your specific use case
        lxo3_records = lxo3_model.search([
            ('session_id', '=', main_session_id),
            ('type_magasin', '=', self.rec_scan_id.magasinPhysique),
            ('emplacement', '=', self.emplacement)
        ])

        my_list = []

        for record in lxo3_records:
            if record.su: 
                my_list.append(record.su)

        return my_list

    def action_show_su_lxo3(self):
        raise exceptions.UserError(self.get_lxo3_su())

    def _normalize_compare_text(self, value):
        return str(value or '').strip()

    def _qty_to_float(self, value):
        qty_str = str(value or '').strip().replace(' ', '')
        if ',' in qty_str and '.' in qty_str:
            if qty_str.rfind(',') > qty_str.rfind('.'):
                qty_str = qty_str.replace('.', '').replace(',', '.')
            else:
                qty_str = qty_str.replace(',', '')
        else:
            qty_str = qty_str.replace(',', '.')
        cleaned_qty_str = re.sub(r'[^\d.-]', '', qty_str)
        try:
            return float(cleaned_qty_str) if cleaned_qty_str else 0.0
        except ValueError:
            return 0.0

    def get_scan_pivot_data(self,xx):
        pivot_data = {}
        etiquette_records = self.env['araymond.etiquette'].search([('su_id', '=', self.id), ('scanNbr', '=', xx)])  # Filter by ID

        for record in etiquette_records:
            qty_value = self._qty_to_float(record.qty)

            key = (self._normalize_compare_text(record.ref), self._normalize_compare_text(record.lot))

            if key not in pivot_data:
                pivot_data[key] = {
                    'ref': key[0],
                    'lot': key[1],
                    'qty': 0.0
                }
            
            pivot_data[key]['qty'] += qty_value
        
        return list(pivot_data.values())

    def compare_pivot_data_returnTxt(self):
        current_pivot_data_lxo3 = self.get_lxo3_pivot_data()

        current_pivot_data_scan = self.get_scan_pivot_data(1)

        # Convert to sets for comparison
        current_set_lxo3 = set((data['article'], data['lot'], data['stock_total']) for data in current_pivot_data_lxo3)
        current_set_scan = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_scan)

        # Find differences
        added = current_set_lxo3 - current_set_scan
        removed = current_set_scan - current_set_lxo3
        common = current_set_lxo3 & current_set_scan

        added = [entry for entry in list(added) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        removed= [entry for entry in list(removed) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        common =[entry for entry in list(common) if entry[0] not in ('', None) and entry != ('', '', 0.0)]

        # Prepare results for better readability
        # Prepare results for better readability and remove empty entries
        result = {
            'added': [entry for entry in list(added) if entry[2] != ('', '', 0.0)],
            'removed': [entry for entry in list(removed) if entry != ('', '', 0.0)],
            'common': [entry for entry in list(common) if entry != ('', '', 0.0)]
        }


        # You can log the result, return it, or raise it in a ValidationError for display
        if added or removed or common:
            message = f"Non scanné: {added}\n"
            message += f"Non existant dans SAP: {removed}\n"
            message += f"OK: {common}\n"
            raise exceptions.UserError(message)

        return result

    def compare_pivot_data_returnTxtScan(self,yy,zz):
        current_pivot_data_lxo3 = self.get_scan_pivot_data(yy)
        current_pivot_data_scan = self.get_scan_pivot_data(zz)

        # Convert to sets for comparison
        current_set_lxo3 = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_lxo3)
        current_set_scan = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_scan)

        # Find differences
        added = current_set_lxo3 - current_set_scan
        removed = current_set_scan - current_set_lxo3
        common = current_set_lxo3 & current_set_scan

        added = [entry for entry in list(added) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        removed= [entry for entry in list(removed) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        common =[entry for entry in list(common) if entry[0] not in ('', None) and entry != ('', '', 0.0)]

        # Prepare results for better readability
        # Prepare results for better readability and remove empty entries
        result = {
            'added': [entry for entry in list(added) if entry[2] != ('', '', 0.0)],
            'removed': [entry for entry in list(removed) if entry != ('', '', 0.0)],
            'common': [entry for entry in list(common) if entry != ('', '', 0.0)]
        }


        # You can log the result, return it, or raise it in a ValidationError for display
        if added or removed or common:
            message = f"Non scanné: {added}\n"
            message += f"Non dans Scan precedent: {removed}\n"
            message += f"OK: {common}\n"
            raise exceptions.UserError(message)

        return result

    def compare_pivot_data_returnList(self):
        current_pivot_data_lxo3 = self.get_lxo3_pivot_data()
        current_pivot_data_scan = self.get_scan_pivot_data(1)

        # Convert to sets for comparison
        current_set_lxo3 = set((data['article'], data['lot'], data['stock_total']) for data in current_pivot_data_lxo3)
        current_set_scan = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_scan)

        # Find differences
        added = current_set_lxo3 - current_set_scan
        removed = current_set_scan - current_set_lxo3
        common = current_set_lxo3 & current_set_scan

        # Filter out any unwanted entries
        added = [entry for entry in list(added) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        removed = [entry for entry in list(removed) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        common = [entry for entry in list(common) if entry[0] not in ('', None) and entry != ('', '', 0.0)]

        # Prepare the result as a dictionary to return
        result = {
            'added': added,
            'removed': removed,
            'common': common
        }

        return result  # Return the structured result
    
    def compare_pivot_data_returnListScan(self,yy,zz):
        current_pivot_data_lxo3 = self.get_scan_pivot_data(yy)
        current_pivot_data_scan = self.get_scan_pivot_data(zz)

        # Convert to sets for comparison
        current_set_lxo3 = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_lxo3)
        current_set_scan = set((data['ref'], data['lot'], data['qty']) for data in current_pivot_data_scan)

        # Find differences
        added = current_set_lxo3 - current_set_scan
        removed = current_set_scan - current_set_lxo3
        common = current_set_lxo3 & current_set_scan

        # Filter out any unwanted entries
        added = [entry for entry in list(added) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        removed = [entry for entry in list(removed) if entry[0] not in ('', None) and entry != ('', '', 0.0)]
        common = [entry for entry in list(common) if entry[0] not in ('', None) and entry != ('', '', 0.0)]

        # Prepare the result as a dictionary to return
        result = {
            'added': added,
            'removed': removed,
            'common': common
        }

        return result  # Return the structured result


    def action_close(self):
        action_return_to_session = False
        for rec in self:
            if rec.emplacementVide:
                rec.ok_done = True
                action_return_to_session = True
            elif rec.scanNbr == 1:
                firstScanVsSAP=self.compare_pivot_data_returnList()
                # Check if added and removed lists are empty
                if not firstScanVsSAP['added'] and not firstScanVsSAP['removed']:
                    for rec in self:
                        rec.ok_done = True
                    action_return_to_session = True
                else:
                    for rec in self:
                        rec.scanNbr += 1
            elif rec.scanNbr == 2:
                firstScanVsSAP=self.compare_pivot_data_returnListScan(1,2)
                # Check if added and removed lists are empty
                if not firstScanVsSAP['added'] and not firstScanVsSAP['removed']:
                    rec.ok_done = True
                    action_return_to_session = True
                else:
                    for rec in self:
                            rec.scanNbr += 1
            elif rec.scanNbr == 3:
                rec.ok_done = True
                action_return_to_session = True

        current_rec = self[:1]
        if action_return_to_session and current_rec.rec_scan_id:
            view = self.env.ref('ar_general_inventory.view_araymond_inventory_session_form')
            return {
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'araymond.invsession',
                'name': '1er Scan',
                'views': [(view.id, 'form')],
                'view_id': view.id,
                'res_id': current_rec.rec_scan_id.id,
                'target': 'current',
            }




    def action_show_ecarts_sapvs1scan(self):
        raise exceptions.ValidationError(self.compare_pivot_data_returnTxt())
    def action_show_ecarts_1vs2scan(self):
        raise exceptions.ValidationError(self.compare_pivot_data_returnTxtScan(1,2))
    def action_show_ecarts_2vs3scan(self):
        raise exceptions.ValidationError(self.compare_pivot_data_returnTxtScan(2,3))

    def action_manuel_save(self):
        self._check_scan()
        if self.emplacementVidefieldCheck == False:
            raise exceptions.UserError("L'emplacement est marquée vide")
            return {'warning': {
                'title': 'Erreur',
                'message': ("L'emplacement est marquée vide"),
            }}  
                 
        if  self.emplacement == False or self.emplacement == "" or len(self.emplacement) != 4:
            raise exceptions.UserError("Merci de saisir l'emplacement/ emplacement incorrecte")
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement/ emplacement incorrecte"),
                }}


        if  self.ref == False or self.ref == "":
            raise exceptions.UserError("Merci de saisir la reference")
            return {'warning': {
                'title': 'Erreur',
                'message': ("Merci de saisir la reference"),
            }}
        else:
            checkRefexist = self.env['araymond.ref'].search([('reference', '=', self.ref )] , limit=1)
            
            if not checkRefexist:
                raise exceptions.UserError("La reference n'existe pas dans la base de donnée")
                return {'warning': {
                        'title': 'Erreur',
                        'message': ("La reference n'existe pas dans la base de donnée"),
                    }}



        if  self.qty == False or self.qty == "":
            raise exceptions.UserError("Merci de saisir la qty")
            return {'warning': {
                'title': 'Erreur',
                'message': ("Merci de saisir la qty"),
            }}
        else:
            if self.qty.isdigit() == False:
                raise exceptions.UserError("Qty contient des caracteres")
                return {'warning': {
                        'title': 'Erreur',
                        'message': ("Qty contient des caracteres"),
                    }}
        

        if  self.lot == False or self.lot == "":
            raise exceptions.UserError("Merci de saisir le lot")
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir le lot"),
                }}
        
        
        if  self.numEtiquet == False or self.numEtiquet == "":
            raise exceptions.UserError("Merci de saisir le numero de l'etiquette")
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir le numero de l'etiquette"),
                }}
        
        if self.dummondatory == True and (self.dum == False or self.dum == "") : 
            raise exceptions.UserError("Merci de saisir le numero DUM")
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir le DUM"),
                }}

        else :

            self.env['araymond.etiquette'].create({
                    'numEtiq': self.ref +";"+ self.qty +";"+ self.lot +";"+ self.numEtiquet,
                    'su_id' : self._origin.id,
                    'mode' :  'manuel',
                    'typedegalia' : self.typedegalia,
                    'mainsession_id': self.mainsession_id,
                    'scanNbr': self.scanNbr,
                    'dum': self.dum
                    
            })
            self.ref =''
            self.hu = ''
            self.numEtiquet = ''
            self.qty = ''
            self.lot = ''
            if not self.rec_scan_id.is_session_scan:
                self.dum = ''
            
            return {'warning': {
                'title': 'l\'etiquette est bien scannée',
                'message': ("l'etiquette est bien scannée"),
            }}
        
        




    @api.onchange('scanraypro')
    def onchange_scan(self):
        if self.scanraypro and self._origin.id:
            self._origin._check_scan()
        self.scanraypro = self.scanraypro or ''
        #todo : AJUSTER et ajouter la codnition nbr de caracteres ==> Done
        #prefix_galia_raypro_manuel = 'Z'
        Nbr_Caractere_Empl = 4
        Nbr_Caractere_um = 10
        Nbr_Caractere_su = 39
        #Nbr_Caractere_su_qr = 148
        #Nbr_Caractere_gallia_raypro = 20
        prefix_galia_raypro_qr = ['{', '!']
        Nbr_Caractere_dum = 17
        #prefix_galia_um = ['G', 'J']


        if  self.scanraypro != "" and not len(self.scanraypro)== Nbr_Caractere_Empl and (self.emplacement == False or self.emplacement == ""): 
                self.scanraypro = ''
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement en premier1"),
                }}

                #raise exceptions.UserError("Merci de saisir l'emplacement en premier1")
        


        #todo : add prefix list ==> done
        #todo : check if galia is doublure ==> done ( unique )


        #if  self.scanraypro != "" and not self.scanraypro.startswith('G') and not len(self.scanraypro)== #Nbr_Caractere_Empl and self.um == False : 
        #    raise exceptions.ValidationError("Merci de saisir l'UM")       

        if len(self.scanraypro)== Nbr_Caractere_Empl: 
            #object_strng = str(self._origin.rec_scan_id)
            #raise exceptions.ValidationError(object_strng)
            #checkifemplexist = self.env['araymond.invsession'].search([('id', '=', self._origin.id)])
            #if checkifemplexist:
            #    for a in checkifemplexist:

            #        if a.emplacement == self.scan.upper():
            #            raise exceptions.ValidationError("empl existe")  
            self.emplacement = self.scanraypro.upper()
            for rec in self:
                rk = rec.rec_scan_id.rack
                jjtx =self.scanraypro.upper()
                if jjtx.startswith(rk):
                    inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
                    inv.write({'emplacement':self.emplacement})
                    self.scanraypro = ''
                else :
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("l'emplacement Scanné est erroné"),
                    }}
            

            #vals_list = {
            #    'emplacement' : self.emplacement
            #}
            #return super(ARinventory, self).create()

        
        if self.scanraypro != "" and (self.emplacement == False or self.emplacement == ""):
            for rec in self:
                rec.scanraypro = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement en premier"),
                }}

        if len(self.scanraypro)== Nbr_Caractere_dum and (self.scanraypro.startswith('3') or self.scanraypro.startswith('4')):
            self.dum = self.scanraypro
            self.scanraypro = ''



        if len(self.scanraypro)== Nbr_Caractere_um and self.scanraypro.startswith('600'):

            
            if self.scanraypro.upper().isdigit():
                
                #forcer l'ecriture dans la BD
                if self.um:
                    self.um = self.um+","+ self.scanraypro.upper()
                    inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
                    inv.write({'um':self.um}) 
                else:
                    self.um = self.scanraypro.upper()
                    inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
                    inv.write({'um':self.um})                      
               
                self.scanraypro = ''
            
            else:
                self.scanraypro = ''
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("l'etiquette scannée est erronée"),
                }}


        if self.scanraypro != "" and (self.dum == False or self.dum == "") and self.dummondatory == True:
            for rec in self:
                rec.scanraypro = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir le DUM"),
                }}
            
        
                        
        if self.scanraypro != "" and (self.um == False or self.um == "") and self.dum !="" and self.ummondatory == True:
            for rec in self:
                rec.scanraypro = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'UM"),
                }}
        
        #if self.scanraypro != "" and (self.dum == False or self.dum == "") and self.dummondatory == True:
        #    for rec in self:
        #        rec.scanraypro = ''
        #    return {'warning': {
        #            'title': 'Erreur',
        #            'message': ("Merci de scanner le DUM"),
        #        }}
            
        if self.scanraypro.upper()[0:1] in prefix_galia_raypro_qr or (len(self.scanraypro)== Nbr_Caractere_su and self.scanraypro[38:39]=="+") or len(self.scanraypro)== Nbr_Caractere_su-1: 
            confirmed_barcode = self.scanraypro
            
            if len(self.scanraypro)== Nbr_Caractere_su-1 :
                modeetiquette = "Manuel"
            else:
                modeetiquette = "Scan"
            if len(self.scanraypro)== Nbr_Caractere_su-1:
                self.scanraypro = self.scanraypro.upper() + "+"
                
            self.env['araymond.etiquette'].create({
                'numEtiq': self.scanraypro.upper(),
                'su_id' : self._origin.id,
                'mode' :  modeetiquette,
                'typedegalia' : self.typedegalia,
                'mainsession_id': self.mainsession_id,
                'scanNbr': self.scanNbr,
                'dum':self.dum
            })

            self.scanraypro = ''
            if not self.rec_scan_id.is_session_scan:
                self.dum = ''

            return {'warning': {
                'title': 'l\'etiquette est bien scannée',
                'message': ("l'etiquette est bien scannée"),
                'type': 'ar_inventory_scan_success',
                'barcode': confirmed_barcode,
            }}
            

        elif self.scanraypro != "" and (self.dum != "" and len(self.scanraypro) != Nbr_Caractere_dum) :
            self.scanraypro = ''
            return {'warning': {
                'title': 'Erreur',
                'message': ("Etiquette scannée est erroné1"),
            }}            


    #todo : if only 1 field manuel put all manuel
    @api.onchange('scanlog')
    def onchange_scanlog(self):
        if self.scanlog and self._origin.id:
            self._origin._check_scan()
        self.scanlog = self.scanlog or ''
        confirmed_barcode = self.scanlog
        #TODO : AJUSTER et ajouter la codnition nbr de caracteres 
        #TODO : hide OF 
        prefix_galia_log_manuel = 'MA'
        Nbr_Caractere_Empl = 4
        Nbr_Caractere_um = 10
        Nbr_Caractere_su = 10
        Nbr_Caractere_gallia_log = 20
        Nbr_Caractere_dum = 17
        prefix_galia_log = ['E', '{', prefix_galia_log_manuel]
        
        prefix_galia_ref = ['P']
        prefix_galia_lot = ['H']
        prefix_galia_qty = ['Q']
        prefix_galia_hu = ['S', 'M', 'G', 'N']

        prefix_galia_ref_man = 'Z'
        prefix_galia_lot_man = 'L'
        prefix_galia_qty_man = 'K'
        prefix_galia_hu_man = 'B'

        prefix_galia_um = ['G', 'J']
        
        if len(self.scanlog)== Nbr_Caractere_dum and (self.scanlog.startswith('3') or self.scanlog.startswith('4')):
            self.dum = self.scanlog
            self.scanlog = ''

        if  self.scanlog != "" and not len(self.scanlog) == Nbr_Caractere_Empl and (self.emplacement == False or self.emplacement == "") : 
            self.scanlog = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement en premier"),
            }}
        else:
            inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
            inv.write({'emplacement':self.emplacement})



        #if len(self.scanlog) == Nbr_Caractere_Empl: 
            #object_strng = str(self._origin.rec_scan_id)
            #raise exceptions.ValidationError(object_strng)
            #checkifemplexist = self.env['araymond.invsession'].search([('id', '=', self._origin.id)])
            #if checkifemplexist:
            #    for a in checkifemplexist:

            #        if a.emplacement == self.scan.upper():
            #            raise exceptions.ValidationError("empl existe")  
        #    self.emplacement = self.scanlog.upper()
        #    self.scanlog = ''
            
         #todo : trouver une solution pour le nombre de caracteres pour l'empcalement
        if self.scanlog != "" and not len(self.scanlog)== Nbr_Caractere_Empl and (self.emplacement == False or self.emplacement == ""):
            self.scanlog = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement en premier"),
                }}
        
        
        
        if len(self.scanlog)== Nbr_Caractere_Empl:   
            self.emplacement = self.scanlog.upper()
            for rec in self:
                rk = rec.rec_scan_id.rack
                jjtx =self.scanlog.upper()
                if jjtx.startswith(rk):
                    inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
                    inv.write({'emplacement':self.emplacement})
                    self.scanlog = ''
                else :
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("l'emplacement Scanné est erroné"),
                    }}
                
        if (self.emplacement != False or self.emplacement != "") and self.scanlog != "" and (self.dum == False or self.dum == "") and self.dummondatory == True :
            for rec in self:
                rec.scanlog = ''
            return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir le DUM en premier"),
                }}
        
        if self.scanlog.upper()[0:1] in prefix_galia_ref or self.scanlog.upper()[0:1] == prefix_galia_ref_man:
            checkRefexist = self.env['araymond.ref'].search([('reference', '=', self.scanlog.upper()[1:] )], limit=1)

            if checkRefexist:
                self.ref = self.scanlog.upper()[1:]
                self.scanlog = ''
            else :
                return {'warning': {
                        'title': 'Erreur',
                        'message': ("La reference n'existe pas dans la base de donnée"),
                    }}  
        elif self.scanlog.upper()[0:1] in prefix_galia_lot or self.scanlog.upper()[0:1] == prefix_galia_lot_man:
            self.lot = self.scanlog.upper()[1:]
            self.scanlog = ''
        elif self.scanlog.upper()[0:1] in prefix_galia_qty or self.scanlog.upper()[0:1] == prefix_galia_qty_man:
            if self.scanlog.upper()[1:].isdigit():
                self.qty = self.scanlog.upper()[1:]
                self.scanlog = ''
            else :
                return {'warning': {
                        'title': 'Erreur',
                        'message': ("Qty contient des caracteres"),
                    }}  
                self.scanlog = ''
        elif self.scanlog.upper()[0:1] in prefix_galia_hu or self.scanlog.upper()[0:1] == prefix_galia_hu_man:
            self.hu = self.scanlog.upper()[1:]
            self.scanlog = ''
        elif self.scanlog != "":
            self.scanlog = ''
            return {'warning': {
                'title': 'Erreur',
                'message': ("Etiquette scannée est erroné"),
            }}     

        
        if not self.ref == "" and not self.lot == "" and not self.hu == "" and not self.qty == "" and not self.emplacement == "" :
             
           # if self.scanraypro.upper()[0:1] == prefix_galia_raypro_manuel:
            #    modeetiquette = "Manuel"
           # else:
            #todo : corriger mode etiquette
            self.modeetiquette = "Scan"
                
            self.env['araymond.etiquette'].create({
                'numEtiq': self.ref +";"+ self.qty +";"+ self.lot +";"+ self.hu,
                'su_id' : self._origin.id,
                'mode' :  self.modeetiquette,
                'typedegalia' : self.typedegalia,
                'mainsession_id': self.mainsession_id,
                'scanNbr': self.scanNbr,
                'dum': self.dum
            })
            self.scanlog = ''
            self.ref =''
            self.hu = ''
            self.qty = ''
            self.lot = ''
            if not self.rec_scan_id.is_session_scan:
                self.dum = ''
            return {'warning': {
                'title': 'l\'etiquette est bien scannée',
                'message': ("l'etiquette est bien scannée"),
                'type': 'ar_inventory_scan_success',
                'barcode': confirmed_barcode,
            }}


    @api.onchange('scanrecp')
    def onchange_scanrecp(self):
        if self.scanrecp and self._origin.id:
            self._origin._check_scan()
        self.scanrecp = self.scanrecp or ''
        confirmed_barcode = self.scanrecp
        #TODO : AJUSTER et ajouter la codnition nbr de caracteres 
        #TODO : hide OF 
        prefix_galia_recp_manuel = 'Z'
        prefix_galia_matdoc_manuel = 'D'
        Nbr_Caractere_Empl = 4
        Nbr_Caractere_matdoc = 14
        #Nbr_Caractere_su = 10
        Nbr_Caractere_gallia_recp = 16
        prefix_galia_recp = ['!', prefix_galia_recp_manuel]
        prefix_galia_matdoc = ['D', '{', prefix_galia_matdoc_manuel]
        prefix_galia_hu = ['S', 'M', 'G', 'N']

        
        if  self.scanrecp != "" and not len(self.scanrecp)== Nbr_Caractere_Empl and (self.emplacement == False or self.emplacement == ""): 
                self.scanrecp = ''
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("Merci de saisir l'emplacement en premier"),
                }}
        else:

            inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
            inv.write({'emplacement':self.emplacement})

        if len(self.scanrecp)== Nbr_Caractere_Empl:   
            self.emplacement = self.scanrecp.upper()
            for rec in self:
                rk = rec.rec_scan_id.rack
                jjtx =self.scanrecp.upper()
                if jjtx.startswith(rk):
                    inv = self.env['araymond.inventory'].search([('id', '=', self._origin.id)])
                    inv.write({'emplacement':self.emplacement})
                    self.scanrecp = ''
                else :
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("l'emplacement Scanné est erroné"),
                    }}


        #TODO : add prefix list
        #if  self.scanraypro != "" and not self.scanraypro.startswith('G') and not len(self.scanraypro)== #Nbr_Caractere_Empl and self.um == False : 
        #    raise exceptions.ValidationError("Merci de saisir l'UM")       


           
        

        #TODO : if only 1 field manuel put all manuel

        

        if self.scanrecp.upper()[0:1] == 'Q'  or self.scanrecp.upper()[0:1] in prefix_galia_hu:

            
           
            #TODO : corriger le mat doc manuel
            if self.scanrecp.upper()[0:1] == 'Q':

                if self.scanrecp.upper()[1:].isdigit():
                    self.hu = self.scanrecp.upper()[1:]
                    self.qty = self.scanrecp.upper()[1:]

                    self.scanrecp = ''

                else:
                    self.scanrecp = ''
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("la qty contient des caracteres"),
                    }}

            else:  
                
                self.hu = self.scanrecp.upper()[1:]
                self.scanrecp = ''



        elif (self.qty != "") and (self.scanrecp.upper()[0:1] in prefix_galia_recp): 
            self.numEtiquet = self.scanrecp.upper()   
            self.modeetiquette = "Scan"         
            #self.scanrecp = self.scanrecp.upper()[1:]
            
            
            try:
                
                qr = self.scanrecp.upper().split(";")
                self.ref = qr[1]

            except:
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("QR V1 code l'etiquette scannée est erroné"),
                }}
            checkRefexist = self.env['araymond.ref'].search([('reference', '=', self.ref )], limit=1)

            if checkRefexist:
                UnitMesure = ""
                for a in checkRefexist:
                    UnitMesure = a.UniteM
                self.lot = qr[2]
                self.hu = qr[5]
                

                
            else:
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("Etiquette scannée est erroné : Reference erronée"),
                }}

        elif (self.hu != "") and (self.scanrecp.upper()[0:1] == prefix_galia_recp_manuel or len(self.scanrecp)>= Nbr_Caractere_gallia_recp): 
            
            if self.scanrecp.upper()[0:1] == prefix_galia_recp_manuel or self.modeetiquette1 == "Manuel":
                
                self.scanrecp = self.scanrecp.upper()[1:]
                self.numEtiquet = self.scanrecp.upper()
                self.modeetiquette1 = "Manuel"
            else:
                self.numEtiquet = self.scanrecp.upper()
                self.modeetiquette1 = "Scan"
            
            
            checkRefexist = self.env['araymond.ref'].search([('reference', '=', self.scanrecp.upper()[0:9] )], limit=1)

            if checkRefexist:
                UnitMesure = ""
                for a in checkRefexist:
                    UnitMesure = a.UniteM
                self.ref = self.scanrecp.upper()[0:9]
                z = len(self.scanrecp)-7
                self.lot = self.scanrecp.upper()[9:z]
                
                if UnitMesure in ('PCE', 'PC'):
                    self.qty = self.scanrecp.upper()[-7:]
                    #raise exceptions.ValidationError(self.qty)
                elif UnitMesure in ('KG'):
                    self.qty = self.scanrecp.upper()[-7:-3] +","+self.scanrecp.upper()[-3:]
                    #raise exceptions.ValidationError(self.qty)
                else:
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("Etiquette scannée est erroné : Unite de mesure erronée"),
                    }}


                
            else:
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("Etiquette scannée est erroné : Reference erronée"),
                }}


        elif self.scanrecp != "":
                self.scanrecp = ''
                return {'warning': {
                    'title': 'Erreur',
                    'message': ("Etiquette scannée est erronée2"),
                }}


        #else:
        #    self.scanrecp = ''
        #    raise exceptions.ValidationError("Etiquette scannée est erroné")  


        if not self.ref == "" and not self.lot == "" and not self.hu == "" and not self.qty == "" and not self.emplacement == "" :
            self.env['araymond.etiquette'].create({
                'numEtiq': self.ref +"_"+ self.qty +"_"+ self.lot +"_"+ self.hu+"_"+ self.numEtiquet,
                'su_id' : self._origin.id,
                'mode' :  self.modeetiquette1,
                'typedegalia' : self.typedegalia,
                'mainsession_id': self.mainsession_id,
                'scanNbr': self.scanNbr
            })
            self.scanrecp = ''
            self.ref =''
            self.hu = ''
            self.qty = ''
            self.lot = ''

            return {'warning': {
                'title': 'l\'etiquette est bien scannée',
                'message': ("l'etiquette est bien scannée"),
                'type': 'ar_inventory_scan_success',
                'barcode': confirmed_barcode,
            }}




class ARinventoryEtiquette(models.Model):
    _name = 'araymond.etiquette'
    _description = 'Araymond inventory'
    
    #TODO : make all fields mondatory 
    #TODO : check num etiq if format is OK (nbr de chiffres, contient des caracteres spec ...)
    #TODO : check Qty if format is OK (nbr de chiffres, contient des caracteres spec ...)

    numEtiq = fields.Char(string='scan', help="scan", tracking=True)
    su = fields.Char(string='SU', help="SU", compute="ch_numEt", store=True, tracking=True)
  
    typedegalia = fields.Char(string='typedegalia', help="typedegalia", default="", tracking=True)
    dum = fields.Char(string='DUM', store=True)
    lot = fields.Char(string='lot' , compute="ch_numEt", store=True)
    hu = fields.Char(string='hu/mat doc'  , compute="ch_numEt", store=True)
    of= fields.Char(string='of' , compute="ch_numEt", store=True)
    ref= fields.Char(string='ref' , compute="ch_numEt", store=True)
    qty = fields.Char(string='qty' , compute="ch_numEt", store=True)
    #prefix  = fields.Char(string='prefix' , compute="ch_numEt", store=True)
    mainsession_id = fields.Char()
    scanNbr = fields.Integer(string='Nbr Scan', required=True, tracking=True)
    scanNbrParent = fields.Integer(string='Scan number parent', required=True, compute='_compute_scan_nbr_for_context')

    def _compute_scan_nbr_for_context(self):
        for record in self:
            record.scanNbrParent = record.su_id.scanNbr


    mode = fields.Char(string='mode', tracking=True)
    
    su_id = fields.Many2one('araymond.inventory', ondelete ='cascade', tracking=True)

    resume = fields.Char(string='Info Session', compute="ch_resume", store=True)
    @api.depends( 'su', 'mainsession_id', 'scanNbr')
    def ch_resume(self):
        for rec in self:
            rec.resume = str(rec.ref)+"_"+str(rec.hu)+"_"+str(rec.lot)+"_"+str(rec.mainsession_id)+"_"+str(rec.scanNbr)
    _sql_constraints = [('su_sess_unique', 'unique(resume)', 'l\'etiquette déja scannée')]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            num_etiq = vals.get('numEtiq')
            mainsession_id = vals.get('mainsession_id')
            scan_nbr = vals.get('scanNbr')

            if not num_etiq or not mainsession_id or not scan_nbr:
                continue

            existing = self.search_count(self._duplicate_domain(vals))
            if existing:
                raise exceptions.ValidationError("L'étiquette est déjà scannée")

        return super().create(vals_list)
        

    #def write(self, values):
    #    raise exceptions.ValidationError("ZZZ")

    def unlinkEt(self):
        self.env['araymond.etiquette'].search([('id', '=', self.id )]).unlink()
        return {'warning': {
            'title': 'Erreur',
            'message': ("etiquette bien supprimée"),
        }}
        raise exceptions.ValidationError("etiquette bien supprimée")

    def extract_keys(self,data_string):
        # Define the keys you want to extract
        keys = ['BAT', 'QTY', 'HU', 'MAT', 'POMAT','PO']
        
        # Initialize a dictionary to store the results
        result = {}

        # Use regex to find the key-value pairs in the string
        for key in keys:
            # Create a regex pattern to find the key and its corresponding value
            pattern = rf"{key}:(.+?)(?=;|$)"
            match = re.search(pattern, data_string)
            
            if match:
                result[key] = match.group(1).strip()  # Store the value, stripping whitespace

        # Check for MAT and POMAT logic
        if 'MAT' not in result and 'POMAT' in result:
            result['MAT'] = result['POMAT']  # If MAT doesn't exist, use the value of POMAT
        if 'PO' not in result:
            result['PO'] = result['BAT']

        return result
        
    @api.depends('numEtiq', 'typedegalia')
    def ch_numEt(self):
        if len(self) > 1:
            for rec in self:
                rec.ch_numEt()
            return

        if not self.numEtiq:
            self.su = self.ref = self.qty = self.of = self.hu = self.lot = ""
            return

        if self.typedegalia == "RayPro":
            for rec in self: 
                #TODO : ajouter condition QR code 
                #TODO : Ajouter verification de l'etiquette QR vs 2D
                
                    if rec.numEtiq[0:1] == "{" :
                        try:
                                                        # Remove curly brackets if present
                            cleaned_string = rec.numEtiq.strip('{}')

                            # Extract values
                            extracted_values = self.extract_keys(cleaned_string)

                            rec.su= rec.numEtiq
                            rec.ref = extracted_values['MAT']
                            rec.qty = extracted_values['QTY']
                            rec.of = extracted_values['PO']
                            rec.hu = extracted_values['HU']
                            rec.lot = extracted_values['BAT']
                            #qr = rec.numEtiq.split(";")
                            #rec.ref = qr[2].split(":")[1]
                            
                            #rec.qty = qr[8].split(":")[1]
                            #rec.of = qr[5].split(":")[1]
                            #rec.hu = qr[11].split(":")[1]
                            #if qr[12].split(":")[0].startswith('BSD'):
                            #    rec.lot = qr[13].split(":")[1]
                            #elif qr[12].split(":")[0].startswith('BAT'):
                            #    rec.lot = qr[12].split(":")[1]

                        except Exception as e:
                            # You can log the exception or take appropriate action
                            raise exceptions.ValidationError(f"Error processing record ID {rec.id}: {str(e)}")
                            _logger.error(f"Error processing record {rec.id}: {str(e)}")
                            # Optional: assign default values or handle fallback logic if necessary
                            rec.ref = rec.qty = rec.of = rec.hu = rec.lot = None  # or some default value
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("QR code l'etiquette scannée est erroné"),
                            }}
                    elif rec.numEtiq[0].isdigit() and rec.numEtiq.endswith('+'):
                        try:
                            rec.su=rec.numEtiq
                            rec.ref = rec.numEtiq[0:9]
                            rec.lot = rec.numEtiq[9:19]
                            rec.qty = rec.numEtiq[19:26]
                            rec.of = rec.numEtiq[26:34]
                            rec.hu = rec.numEtiq[34:38]
                        except Exception as e:
                            # You can log the exception or take appropriate action
                            raise exceptions.ValidationError(f"Error processing record ID {rec.id}: {str(e)}")
                            _logger.error(f"Error processing record {rec.id}: {str(e)}")
                            # Optional: assign default values or handle fallback logic if necessary
                            rec.ref = rec.qty = rec.of = rec.hu = rec.lot = None  # or some default value
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("QR code l'etiquette scannée est erroné"),
                            }}
                    elif  len(rec.numEtiq) > 1 and rec.numEtiq[0] == '!' and rec.numEtiq[1].isdigit() and rec.numEtiq.endswith('+'):
                        try:
                            modified_string = rec.numEtiq[1:-1]
                            # Split the modified string by ';'
                            split_result = modified_string.split(';')
                            rec.su=rec.numEtiq
                            rec.ref = split_result[0]
                            rec.lot = split_result[1]
                            rec.qty = split_result[2]
                            rec.of = split_result[3]
                            rec.hu = split_result[4]
                        except Exception as e:
                            # You can log the exception or take appropriate action
                            raise exceptions.ValidationError(f"Error processing record ID {rec.id}: {str(e)}")
                            _logger.error(f"Error processing record {rec.id}: {str(e)}")
                            # Optional: assign default values or handle fallback logic if necessary
                            rec.ref = rec.qty = rec.of = rec.hu = rec.lot = None  # or some default value
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("QR code l'etiquette scannée est erroné"),
                            }}
                    elif  len(rec.numEtiq) > 1 and rec.numEtiq.startswith('!;!;') and rec.numEtiq[4].isdigit() and rec.numEtiq.endswith('+'):
                        try:
                            modified_string = rec.numEtiq[4:-1]
                            # Split the modified string by ';'
                            split_result = modified_string.split(';')
                            rec.su=rec.numEtiq
                            rec.ref = split_result[0]
                            rec.lot = split_result[1]
                            rec.qty = split_result[2]
                            rec.of = split_result[3]
                            rec.hu = split_result[4]
                        except Exception as e:
                            # You can log the exception or take appropriate action
                            raise exceptions.ValidationError(f"Error processing record ID {rec.id}: {str(e)}")
                            _logger.error(f"Error processing record {rec.id}: {str(e)}")
                            # Optional: assign default values or handle fallback logic if necessary
                            rec.ref = rec.qty = rec.of = rec.hu = rec.lot = None  # or some default value
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("QR code l'etiquette scannée est erroné"),
                            }}
                    elif rec.numEtiq[0:1] == "!" and not rec.numEtiq[1].isdigit()  and rec.numEtiq[2].isdigit() and rec.numEtiq.endswith('+') and rec.numEtiq.count(';') >= 4:
                        try:
                            modified_string = rec.numEtiq[2:-1]
                            rec.su=rec.numEtiq
                            qr = modified_string.split(";")
                            rec.ref = qr[0]
                            rec.lot = qr[1]
                            rec.qty = qr[2]
                            rec.of = qr[3]
                            rec.hu = qr[4]
                        except Exception as e:
                            # You can log the exception or take appropriate action
                            raise exceptions.ValidationError(f"Error processing record ID {rec.id}: {str(e)}")
                            _logger.error(f"Error processing record {rec.id}: {str(e)}")
                            # Optional: assign default values or handle fallback logic if necessary
                            rec.ref = rec.qty = rec.of = rec.hu = rec.lot = None  # or some default value
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("QR code l'etiquette scannée est erroné"),
                            }}
                    else:
                        try:
                            rec.su=rec.numEtiq
                            modified_string = rec.numEtiq[2:-1]
                            rec.ref = modified_string[0:9]
                            rec.lot = modified_string[9:19]
                            rec.qty = modified_string[19:26]
                            rec.of = modified_string[26:34]
                            rec.hu = modified_string[34:38]
                        except:
                            return {'warning': {
                                'title': 'Erreur',
                                'message': ("l'etiquette scannée est erroné ( traitement )"),
                            }}
                
                    
        elif self.typedegalia == "logistique"  or self.typedegalia == "Manuel":
            for rec in self: 
                try:
                    txtEt = rec.numEtiq.split(';')
                    rec.ref = txtEt[0]
                    rec.qty = txtEt[1]
                    rec.lot = txtEt[2]
                    rec.su = txtEt[3]
                    rec.of = ""
                    rec.hu = txtEt[3]
                except:
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("l'etiquette scannée est erroné ( traitement )"),
                    }}

        elif self.typedegalia == "ReceptionSAP" :
            for rec in self: 
                try:
                    txtEt = rec.numEtiq.split('_')
                    rec.ref = txtEt[0]
                    rec.qty = txtEt[1]
                    rec.lot = txtEt[2]
                    rec.su = txtEt[4]+'_'+txtEt[3]
                    rec.of = ""
                    rec.hu = txtEt[3]
                except:
                    return {'warning': {
                        'title': 'Erreur',
                        'message': ("l'etiquette scannée est erroné ( traitement )"),
                    }}


class ARinventorySessionMain(models.Model):
    _name = 'araymond.maininvsession'
    _description = 'Araymond inventory'

    #TODO : make all fields mondatory ==> DONE
    #TODO : make all in tracking
    #TODO : supprimer les droit de suppression
    #TODO : ajouter l'UC
    resume = fields.Char(string='Info Session', compute="ch_resume", store=True)
    desc = fields.Char(string='Description',  tracking=True)

    state = fields.Selection([('CreationMain', 'CreationMain'),('Creation', 'Creation'),('Scan', 'Scan'),('Fermeture', 'Fermeture')], default = 'CreationMain' )
    datesession = fields.Datetime(string='Date de la session', required=True , tracking=True)
    typesession = fields.Selection([('InvGen', 'General')], string='Type de la session', default='InvGen', required=True, tracking=True)
    # One2many relation to araymond.lxo3 model
    sess_records = fields.One2many('araymond.invsession', 'mainsession_id', string='invsession Records', tracking=True)
    lx03_file = fields.Binary(string='LX03 Excel File', help='Upload the LX03 Excel file here', tracking=False)
    lx03_filename = fields.Char(string='LX03 Excel Filename', help='The name of the uploaded LX03 Excel file', tracking=True)
    file_loaded = fields.Boolean(string='UM obligatoire', default = False, tracking=True)
    # One2many relation to araymond.lxo3 model
    lxo3_records = fields.One2many('araymond.lxo3', 'session_id', string='LX03 Records',  tracking=True)

    @api.depends( 'datesession', 'typesession', 'desc')
    def ch_resume(self):
        for rec in self:
            rec.resume = str(rec.typesession)+"/"+str(rec.datesession)+"/"+str(rec.desc)

    def action_mod_empl(self):
        self = self.with_context(action_mod_empl=True)
        self.state = 'CreationMain'
    
    
    
    def action_scan_ok(self):
        self.state = 'Scan'
    

    def action_load_file(self):
        for rec in self:           
            # Verify that the uploaded file contains the required headers
            if rec.lxo3_records:
                rec.lxo3_records.unlink()  # Unlink (delete) all associated lxo3 records
            if rec.lx03_file:
                try:
                    import openpyxl
                except ImportError:
                    raise exceptions.UserError("La librairie Python openpyxl est requise pour lire le fichier Excel.")
                try:
                    # Convert binary to a readable stream
                    data = BytesIO(base64.b64decode(rec.lx03_file))
                    workbook = openpyxl.load_workbook(filename=data, data_only=True)
                    sheet = workbook.active  # You can specify the sheet name if needed

                    # Get the first row of the sheet containing headers
                    headers = [cell.value for cell in sheet[1]]  # Assuming the first row has headers

                    # Define the expected headers
                    expected_headers = [
                        "Type de magasin", "Emplacement", "Article", "Lot", "Stock total",
                        "Unité de qté base", "Type unité stock", "Magasin", "Unité de stock"
                    ]

                    # Normalize headers by stripping whitespace
                    normalized_headers = [(str(header).strip() if header else '') for header in headers]
                    
                    # Check if any header starts with the expected headers
                    matched = any(any(header.startswith(expected) for header in normalized_headers) for expected in expected_headers)

                    if not matched:
                        raise exceptions.UserError(
                            f"Aucun en-tête trouvé dans le fichier Excel ne commence par l'un des en-têtes attendus: {expected_headers}"
                        )
                    # Create a mapping from normalized headers to their corresponding index
                    header_to_index = {header: index for index, header in enumerate(normalized_headers)}
                    # Check if all expected headers exist in the Excel file
                    if not all(header in header_to_index for header in expected_headers):
                        raise exceptions.UserError(
                            f"Le fichier Excel ne contient pas tous les en-têtes attendus: {expected_headers}"
                        )
                                        # Create records in `araymond.lxo3` for each subsequent row
                    # Create records in `araymond.lxo3` for each subsequent row
                    for row in sheet.iter_rows(min_row=2, values_only=True):  # Start from the second row
                        stock_total = row[header_to_index["Stock total"]]  # Use headers to get the index dynamically
                        type_mag = row[header_to_index["Type de magasin"]]
                        if stock_total and stock_total != 0 and type_mag and type_mag !="":
                            values = {
                                'type_magasin': type_mag,
                                'emplacement': row[header_to_index["Emplacement"]],
                                'article': row[header_to_index["Article"]],
                                'lot': row[header_to_index["Lot"]],
                                'stock_total': stock_total,
                                'unite_qty_base': row[header_to_index["Unité de qté base"]],
                                'type_unite_stock': row[header_to_index["Type unité stock"]],
                                'magasin': row[header_to_index["Magasin"]],
                                'su' : row[header_to_index["Unité de stock"]],
                                'session_id': rec.id,  # Link back to the inventory session
                            }
                            # Create a new record in the lxo3 model
                            self.env['araymond.lxo3'].create(values)

                except exceptions.UserError:
                    raise
                except Exception as e:
                    raise exceptions.ValidationError(f"Erreur de lecture du fichier Excel: {str(e)}")
            else:
                raise exceptions.ValidationError("Veuillez télécharger un fichier Excel.")

            # Change the state if all checks are passed
            rec.file_loaded = True
    def action_creation(self):
        for rec in self:
            # Check if there are any LX03 records
            if not rec.lxo3_records:
                raise exceptions.ValidationError("Veuillez d'abord ajouter des enregistrements LX03 avant de continuer.")
            rec.state = 'Creation'


class ARinventorySession(models.Model):
    _name = 'araymond.invsession'
    _description = 'Araymond inventory'

    #TODO : make all fields mondatory ==> DONE
    #TODO : make all in tracking
    #TODO : supprimer les droit de suppression
    #TODO : ajouter l'UC
    state = fields.Selection([('Creation', 'Creation'),('Scan', 'Scan'),('Fermeture', 'Fermeture')], default = 'Scan' , tracking=True)
    mainsession_id = fields.Many2one('araymond.maininvsession', string='Inventory Session', ondelete='cascade', required=True)
    mainsession_desc = fields.Char(related='mainsession_id.desc', string='Inventory Session description')
    resume = fields.Char(string='Info Session', compute="ch_resume", store=True)

    magasinPhysique = fields.Selection([('All', 'All'),('M05', 'M05'),('T37', 'T37'),('M10', 'M10'),('M11', 'M11'), ('PROD', 'PROD'),('JL0', 'JL0'),('Autre', 'Autre')], string='Magasin', required=True, tracking=True )
    magasinSAP = fields.Selection([('FG01', 'FG01'),('FG00', 'FG00'),('JL00', 'JL00'),('TR00', 'TR00'),('TR37', 'TR37'),('CL00', 'CL00'),('EMB', 'EMB'),('EMBW', 'EMBW'),('EX00', 'EX00'),('RM00', 'RM00'),('RM01', 'RM01'),('SF01', 'SF01'),('PROD', 'PROD')], string='Magasin SAP', required=True, tracking=True )
    ummondatory = fields.Boolean(string='UM obligatoire', tracking=True)
    dummondatory = fields.Boolean(string='DUM obligatoire', tracking=True)
    statusSession = fields.Boolean(string='Status de la session', tracking=True)
    typedegalia = fields.Selection([('RayPro', 'RayPro'),('ReceptionSAP', 'Reception SAP'),('logistique', 'logistique'),('Manuel', 'Manuel'),('Hybride', 'Hybride')] , string='Type de galia', tracking=True)
    recs_scan = fields.One2many('araymond.inventory', 'rec_scan_id', tracking=True)
    nbrEmp = fields.Integer(string='Nombre d\'emplacements', required=True, tracking=True)
    rack = fields.Selection(
        [('Autre', 'Autre')] + [(letter, letter) for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'],
        string='Rack', required=True, tracking=True)
   



    @api.depends( 'magasinPhysique', 'magasinSAP', 'rack', 'nbrEmp')
    def ch_resume(self):
        for rec in self:
            rec.resume = str(rec.magasinPhysique)+"/"+str(rec.rack)+"/"+str(rec.magasinSAP)+"/nbr emp:"+str(rec.nbrEmp)



    def action_creation(self):
        for rec in self:
            # Check that the number of positions is greater than zero
            if rec.nbrEmp < 1:
                raise exceptions.ValidationError("Nombre d'emplacements a inventorier doit etre superieur a 0")
            # Change the state if all checks are passed
            rec.state = 'Scan'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Check if nbrEmp is present in vals and is less than 1
            if vals.get('nbrEmp', 0) < 1:
                raise exceptions.ValidationError("Nombre d'emplacements à inventorier doit être supérieur à 0")

        # Call the super method to create the records
        return super().create(vals_list)


    def action_create_empl(self):
        self.env['araymond.inventory'].create({
            'rec_scan_id' : self.id,
            'emplacementVide' : False,
            'mainsession_id': self.mainsession_id
        })

    def action_mod_empl(self):
        self.state = 'Creation'

    def action_close_invsession(self):
        self.state = 'Fermeture'
    

class ARInventoryLXO3(models.Model):
    _name = 'araymond.lxo3'
    _description = 'LX03 Inventory Data'

    # Link to the inventory session
    session_id = fields.Many2one('araymond.maininvsession', string='Inventory Session', ondelete='cascade', required=True)
        # Define the fields according to your expected headers
    type_magasin = fields.Char(string="Type de magasin", tracking=True)
    emplacement = fields.Char(string="Emplacement", tracking=True)
    article = fields.Char(string="Article", tracking=True)  # Assumed to be unique
    lot = fields.Char(string="Lot", tracking=True)
    stock_total = fields.Float(string="Stock total", tracking=True)
    unite_qty_base = fields.Char(string="Unité de qté base", tracking=True)
    type_unite_stock = fields.Char(string="Type unité stock", tracking=True)
    magasin = fields.Char(string="Magasin", tracking=True)  # Assumed to be a field non-specifically defined
    su = fields.Char(string="SU", tracking=True)  # Assumed to be a field non-specifically defined    


class ARinventoryRef(models.Model):
    _name = 'araymond.ref'
    _description = 'Araymond inventory reference'

    #TODO : ajouter un menu parametre 
    #TODO : ajuster la view form

    reference = fields.Char(string='Reference', tracking=True)
    desc = fields.Char(string='Description', tracking=True)
    uc = fields.Char(string='UC', tracking=True)
    UniteM = fields.Char(string='Unite de mesure', tracking=True)

    _sql_constraints = [('ref_unique', 'unique(reference)', 'reference déja inserée')]
   



class ARinventoryImageStock(models.Model):
    _name = 'araymond.imagestock'
    _description = 'Araymond inventory Image de stock'

    #TODO : CONNAITRE le rack depuis le prefix de l'emplacement

    typemagasin = fields.Char(string='type de magasin')
    emplacement = fields.Char(string='Emplacement')
    reference = fields.Char(string='Reference')
    lot = fields.Char(string='Lot')
    stocktotal = fields.Float(string='stock total')
    UniteM = fields.Char(string='Unite de mesure')
    magasin = fields.Char(string='magasin')
    emplVide = fields.Char(string='Emp.v.')
    su = fields.Char(string='Unité de stockage')


   
