# kinito-serveur

Serveur multijoueur des jeux de soirée (FastAPI + WebSocket), publié sur Render.

## Organisation

- `serveur.py` : réseau, salons, et **registre des jeux** (`JEUX`). Chaque jeu y est déclaré une fois
  (message de démarrage, actions, reprise après coupure, départ, retour, arrivée en cours de partie,
  minuteurs, nombre de joueurs). Ajouter un jeu = ajouter une entrée.
- Moteurs de règles purs, testables hors ligne : `picolopoly.py`, `menteur.py`, `casino.py`.
- `couches.py` : contrat des **couches parallèles** d'une soirée (l'Enquête), qui tournent à côté des jeux.
- `stockage.py` : sauvegarde durable (Postgres Neon si `DATABASE_URL` est défini, sinon mémoire).

## Une soirée, ses tables

- Une soirée a un code de 4 lettres (`ABCD`), qui est aussi sa table 1. Ses autres tables sont
  `ABCD-2` … `ABCD-9` (8 places chacune) : chacune est un salon complet, avec son jeu.
  `GET /soiree/{code}` liste les tables, `GET /soiree/{code}/nouvelle-table` en ouvre une.
- Un prénom n'est qu'à une table à la fois : s'asseoir à une autre le fait quitter la première.
- Les couches (l'Enquête) appartiennent à la soirée : elles voient les joueurs et les victoires
  de toutes les tables.

## Une table = un salon

- Le même code sert toute la soirée. **Règle unique de lancement** : en salle d'attente le jeu démarre ;
  le même jeu en cours ne se relance que s'il est fini ; un autre jeu le remplace (le nombre de joueurs
  est vérifié avant, pour ne pas détruire la partie en cours). Le serveur diffuse alors
  `{"type": "salon_jeu", "jeu", "titre", "page"}`.
- `{"type": "retour_salon"}` ramène tout le monde en salle d'attente (`salon_retour`).
- `GET /salon/{code}` : joueurs, jeu en cours, page à ouvrir, couches actives.

## Couches parallèles

Une couche a un préfixe (`enq_` pour l'Enquête) : ses messages lui sont routés quel que soit le jeu.
Elle connaît les joueurs par leur **nom**. Les jeux signalent leurs résultats par un seul point,
`signaler(code, jeu, type, gagnants, perdants, details)` : fin de manche (Kinito, Buckshot),
victoire (BeerBattle, Menteur, Picopoly), fin de jeu (remplacement ou retour au salon).
Une couche survit à la libération du salon (son code n'est pas redonné) et au redémarrage du serveur.

## Enquête (jeu fil conducteur)

- `enquete.py` : moteur pur (salle d'attente, portraits, réglages, tirage secret, ouverture, fin).
- `enquete_couche.py` : la couche « enq » de la soirée. `enquete_config.json` : tous les chiffres.
  `enquetes/*.json` : un pack par univers (ajouter un univers ne touche pas au moteur).
- Connexion dédiée `/ws-soiree/{code}/{prénom}` : les couches parlent aux joueurs par ici, à côté de
  leur place à une table (la page de l'Enquête s'ouvre par-dessus un jeu sans lui voler sa connexion).
- Témoin anonyme : toutes les `bonus_toutes` secondes (± `bonus_alea`), un indice offert à un joueur
  tiré au sort, tueur compris, jamais deux fois de suite le même. Hors plafond horaire.
- Contre-expertise (`enq_expertise`, paiement `pe` ou `gorgee`) : tampon permanent « authentique » ou
  « falsifié » sur un indice lu de son archive ; compte comme une consultation (même chrono).
- Sabotages (tueur seul, `enq_charge` puis `enq_sabotage`) : fausse piste (remplacement du prochain
  indice de la cible, ou détournement du prochain Témoin), interception (pli retenu 10 min), écoute
  (dernier indice de la cible), fausse rumeur (une par partie, sur un innocent). 3 charges au plus,
  délai entre deux sabotages, détection qui baisse avec le temps ; l'annonce « interférence » et la
  fausse rumeur sortent après un délai aléatoire. Rumeurs ambiantes (`rumeurs_toutes`) en bruit de fond.
  La console (`console` dans la vue privée) vaut `None` pour tous les autres.
- Événements d'ambiance (programme secret tiré au lancement, nombre et durée selon la vitesse, jamais
  deux à la fois ni deux fois de suite le même) : coupure (archive verrouillée, plis illisibles,
  détection /2), découverte (indice vrai publié à tous), alerte (délai d'accusation /2, gorgées
  d'accusation ratée x2), brouhaha (rumeurs plus fréquentes, une fausse rumeur gratuite), interrogatoire
  (2 joueurs désignés, `enq_refuser` coûte des gorgées), marée (PE doublés).
- Gorgées : vitesse x dosage (doux 0,5, normal 1, corsé 1,5), unité libre (mode sans alcool). Plafond
  par heure et par joueur (6, 12, 20 ou sans) : au-delà, chaque gorgée non bue devient 1 min de handicap
  (archive et accusations bloquées). Récap de fin : gorgées bues et converties par joueur.
- Pénalités visibles par tous (`gorgees_publiques`, activé par défaut) : chaque gorgée imposée apparaît
  dans l'état public (`penalites`) jusqu'à ce que le joueur confirme (`enq_bu`). Contrepartie : on sait
  qui a raté une accusation (jamais son contenu). Un sabotage ne fait jamais boire le tueur.
- Trois univers (`enquetes/manoir.json`, `station.json`, `velvet.json`) : 8 lieux, 8 armes, 12 portraits,
  plan (avec `decor` pour le thème 3D du site), textes du narrateur. Validés au chargement (textes complets,
  modèles qui se remplissent) ; contractions « de le » -> « du » après insertion d'un nom.
- Non-fuite : l'état public n'a jamais la solution ni le tueur avant la fin ; la vue privée a les
  mêmes clés pour tous (`tests/enquete_essai.py`).

## Base de données

`DATABASE_URL` (variable d'environnement Render, jamais dans le dépôt) : base Postgres Neon, Francfort.
Une table `donnees (cle, valeur jsonb, maj)` créée au démarrage. Sans la variable, ou base injoignable,
tout tourne en mémoire. `GET /` indique `"stockage": "postgres"` ou `"memoire"`.

## Tests

```
python tests/menteur_essai.py
python tests/casino_essai.py
python tests/fondations_essai.py            # + TEST_DATABASE_URL=postgresql://... pour la base
python tests/enquete_essai.py
node tests/enquete_navigateur.js            # 4 joueurs : salle d attente, rôles, onglet par-dessus un jeu
python tests/banc_reconnexions.py --jeu tous --duree 20
node tests/navigateur.js                    # serveur_essai.py + site sur localhost:8765
# Pour les tests de soirée et d enquête (scènes 3D lourdes sans carte graphique) :
#   DELAI_SILENCE=30 ENQ_RAPIDE=1 python tests/serveur_essai.py
node tests/soiree_navigateur.js             # parcours d une soirée (accueil, changement de jeu)
```
