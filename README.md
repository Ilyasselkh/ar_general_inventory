# AR - Inventaire général

Module Odoo d'inventaire par scan pour les flux entrepôt Araymond.

Le module gere les sessions d'inventaire, les emplacements, les scans RayPro, Reception SAP, Logistique ou manuel, les etiquettes, les lots, les quantites et les comparaisons avec les donnees SAP/LX03.

## Objectif fonctionnel

Faciliter les inventaires tournants ou generaux en fiabilisant la collecte terrain par scan.

Le module permet de :

- creer une session principale d'inventaire ;
- creer des sessions ou emplacements de scan ;
- charger des fichiers de reference SAP/LX03 ;
- scanner les etiquettes par emplacement ;
- gerer les emplacements vides ;
- controler les UM, lots, references et quantites ;
- faire plusieurs passages de scan ;
- comparer SAP avec les scans ;
- comparer les passages entre eux ;
- identifier les ecarts ;
- fermer une session d'inventaire ;
- exporter ou consulter les donnees de suivi.

## Roles fonctionnels

### Operateur inventaire

L'operateur realise les scans terrain.

Il peut :

- ouvrir un emplacement ;
- scanner les etiquettes ;
- declarer un emplacement vide ;
- renseigner manuellement une reference, un lot, une HU ou une quantite si necessaire ;
- terminer le scan d'un emplacement.

### Responsable inventaire

Le responsable inventaire pilote les sessions.

Il peut :

- creer une session principale ;
- charger les fichiers source ;
- creer les emplacements ;
- suivre l'avancement des scans ;
- consulter les anomalies ;
- fermer la session.

### Administrateur

L'administrateur gere les droits, les vues et les controles techniques.

## Types de session

Les types de session principaux sont :

- inventaire tournant ;
- inventaire general ;
- autre.

## Types de scan

Le module gere plusieurs modes de scan :

- `RayPro`
- `Reception SAP`
- `Logistique`
- `Manuel`
- `Hybride` selon les sessions.

## Etats des sessions

Les sessions principales utilisent notamment :

- `CreationMain`
- `Creation`
- `Scan`
- `Fermeture`

Les sessions de scan utilisent notamment :

- `Creation`
- `Scan`
- `Fermeture`

## Flux operationnel

1. Creer une session principale.
2. Choisir le type de session.
3. Charger les fichiers SAP/LX03 si necessaire.
4. Creer les emplacements ou sessions de scan.
5. Scanner les etiquettes par emplacement.
6. Controler les anomalies.
7. Comparer les resultats avec SAP/LX03.
8. Corriger ou completer les saisies manuelles.
9. Fermer la session.

## Controles et ecarts

Le module permet de consulter :

- ecarts SAP vs premier scan ;
- ecarts premier scan vs deuxieme scan ;
- ecarts deuxieme scan vs troisieme scan ;
- emplacements en anomalie ;
- etiquettes scannees ;
- donnees manuelles.

## Dependances

Dependance Odoo :

- `base`

Dependance Python :

- `openpyxl`

`openpyxl` est utilise pour charger ou traiter les fichiers Excel.

## Modeles principaux

- `araymond.inventory`
- `araymond.etiquette`
- `araymond.maininvsession`
- `araymond.invsession`
- `araymond.lxo3`
- `araymond.ref`
- `araymond.imagestock`

## Structure du module

- `security/ir.model.access.csv`
- `views/menu.xml`
- `views/inventory.xml`
- `models/AR_inventory.py`
- `__manifest__.py`

## Installation

1. Copier le module dans le dossier addons Odoo.
2. Installer la dependance Python `openpyxl`.
3. Redemarrer le serveur Odoo si necessaire.
4. Mettre a jour la liste des applications.
5. Installer le module.
6. Verifier les droits utilisateurs.
7. Tester une session avec un fichier source.
8. Tester un scan RayPro et un scan manuel.

## Maintenance fonctionnelle

Lorsqu'une regle d'inventaire change, verifier aussi :

- les types de scan ;
- les champs obligatoires UM/DUM ;
- les imports Excel ;
- les comparaisons d'ecarts ;
- les vues de fermeture ;
- ce README.


## Parcours des nouvelles sessions (version 19.0.3.0.0)

1. Dans **Paramètres → Responsables**, un administrateur désigne les utilisateurs autorisés et choisit **Checklist à valider** : Préparation, Jour d’inventaire — avant scan, Jour d’inventaire — après scan, ou Toutes les checklists. Ajouter plusieurs lignes pour affecter une même personne à plusieurs checklists précises. Les responsables existants conservent Toutes les checklists à la mise à niveau. La visibilité du menu reste contrôlée par le groupe **Afficher le menu Paramètres**. Une seule validation par un responsable actif affecté à la checklist suffit ; un administrateur doit également être désigné pour valider.
2. Dans **Sessions**, créer un inventaire général, saisir la date et la description, puis enregistrer. Les 18 points de préparation sont créés automatiquement, sans reprendre les coches des images de référence.
3. Renseigner les réponses OK/NOK et les commentaires. Chaque NOK exige une explication ; le responsable décide ensuite de valider. L’identité du validateur et la date sont conservées.
4. Après validation, renseigner les racks : responsable, magasins physique et SAP, nombre d’emplacements, niveau SOL/N1/N2/N3, personnes Scan 1, Scan 2 et Équipe 3, type de galia. Les trois affectations sont visibles et obligatoires avant confirmation du tableau. Une même personne peut être affectée aux trois équipes.
5. Compléter et faire valider les 10 points du jour d’inventaire précédant les scans.
6. Joindre le fichier LX03, le charger, puis valider son import. Les emplacements sont générés à partir du magasin physique, du magasin SAP et du préfixe du rack. Le nombre doit correspondre au nombre annoncé. Le niveau est une information d’organisation du rack ; il ne modifie pas la correspondance des codes LX03. Un même rack ne doit pas être saisi deux fois dans le même couple de magasins.
7. Les équipes 1 et 2 commencent sur deux lots d’emplacements puis échangent ces lots lors du contrôle croisé. Seuls les utilisateurs affectés à l’équipe du rack peuvent scanner.
8. En cas d’écart, les missions de l’équipe 3 sont créées selon la logique existante sur tous les emplacements de la zone et utilisent les personnes déjà désignées dans le tableau des racks. La section correspondante apparaît uniquement pour les racks à recompter. Le responsable du rack ou un administrateur peut ajuster les personnes avant le début du recomptage. L’affectation est ensuite verrouillée.
9. Compléter les 8 contrôles après scan. Leur validation nécessite la fin des comptages et des recomptages. Clôturer ensuite : l’historique et le bilan sont conservés.

Les anciennes sessions gardent leur parcours et leurs données. Le bouton **Ouvrir la configuration historique** donne accès à leur ancien formulaire. Les consignes paramétrables historiques ne remplacent pas les 36 points des nouvelles sessions.

Après mise à jour des fichiers, mettre à niveau `ar_general_inventory` et redémarrer Odoo. Tester sur une base distincte avec `--test-enable --test-tags /ar_general_inventory --stop-after-init`.

## Inventaire général par équipes — ancien parcours

1. Créer la session, charger SAP et définir au moins deux équipes avec des membres distincts.
2. Créer les zones (magasin / rack), désigner leur superviseur et activer les zones.
3. Dans Scan, répartir les emplacements nommés entre les équipes. Le nombre doit correspondre à celui prévu dans la zone.
4. Préparer la checklist : consignes proposées ou personnalisées, réponse OK/NOK pour chaque ligne. Chaque NOK exige un commentaire.
5. Le superviseur désigné valide la checklist une fois pour la zone et la session. La validation est datée et verrouillée.
6. Passer la session principale en Scan, puis lancer les équipes dans chaque zone. Les opérateurs utilisent « Mes emplacements à scanner ».
7. Chaque opérateur clôture son relevé, y compris les emplacements explicitement déclarés vides. Le dernier relevé initial génère automatiquement les missions de contrôle croisé par rotation des équipes.
8. Après tous les contrôles croisés, le rapprochement retient les résultats concordants et identifie les écarts par référence, lot et quantité, ainsi que les déclarations d’emplacement vide.
9. En cas d’écart, ajouter si nécessaire une équipe d’arbitrage dans la session, la sélectionner dans la zone et affecter les écarts. Cette équipe doit être indépendante des deux équipes concernées.
10. À la clôture de chaque arbitrage, son résultat devient automatiquement le résultat final. Le rapprochement SAP utilise exclusivement les résultats finaux. La zone peut être clôturée quand tous les emplacements sont rapprochés.

Les relevés terminés sont verrouillés et le compteur historique n’est plus proposé aux opérateurs. Les scanners existants conservent leur format d’emplacement à quatre caractères.
Les anciennes zones contenant déjà des scans ne sont pas converties automatiquement : démarrer une nouvelle zone pour le nouveau workflow.
Après déploiement, redémarrer Odoo et mettre à niveau le module `ar_general_inventory` pour créer les champs, tables et vues.
Tests Odoo : `--test-enable --test-tags /ar_general_inventory --stop-after-init` sur une base de test.


### Accès Scan et consignes paramétrables — ancien parcours

Le menu Scan présente toutes les sessions non clôturées, même sans zone active. Ouvrir la session, puis « Checklist et scan » sur la zone. Une session sans zone affiche une indication et un bouton de configuration.
Les administrateurs gèrent les consignes dans Paramètres → Consignes de checklist. Les consignes actives sont automatiquement copiées dans les zones nouvelles et les checklists non validées. Une modification du texte remet sa réponse à zéro ; une consigne archivée disparaît uniquement des checklists non validées. Les anciennes consignes et réponses restent conservées dans les checklists validées.
Le superviseur répond avec les boutons OK / NOK et ajoute un commentaire à chaque NOK avant validation. Les consignes se configurent dans les paramètres, pas dans les réponses de scan.


### Équipes automatiques — ancien parcours

Les nouvelles sessions disposent automatiquement d’Équipe 1 et Équipe 2, sans membres à configurer. Dans chaque zone, saisir les emplacements et choisir le type de galia commun sous Nombre d’emplacements. Au lancement, les emplacements triés par code sont répartis en deux lots équilibrés (le premier reçoit un emplacement de plus si le nombre est impair). Le contrôle croisé échange les lots, y compris pour une zone à un seul emplacement.
Si le rapprochement constate un écart, Équipe 3 est créée automatiquement dans la même session et reçoit les emplacements de la zone. Dans les sessions historiques automatiques, les opérateurs internes accèdent directement aux missions par branche. Les anciens inventaires déjà lancés avec des équipes manuelles conservent leurs règles d’accès.
