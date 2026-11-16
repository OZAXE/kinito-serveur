# kinito-serveur

Serveur multijoueur des jeux de soirée (FastAPI + WebSocket), publié sur Render.

## Organisation

- `serveur.py` : réseau, salons, et **registre des jeux** (`JEUX`). Chaque jeu y est déclaré une fois
  (message de démarrage, actions, reprise après coupure, départ, retour, arrivée en cours de partie,
  minuteurs, nombre de joueurs). Ajouter un jeu = ajouter une entrée.
- Moteurs de règles purs, testables hors ligne : `picolopoly.py`, `menteur.py`, `casino.py`.
- `couches.py` : contrat des **couches parallèles** d'une soirée (l'Enquête), qui tournent à côté des jeux.
- `stockage.py` : sauvegarde durable (Postgres Neon si `DATABASE_URL` est défini, sinon mémoire).

## Une soirée = un salon = un code

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

## Base de données

`DATABASE_URL` (variable d'environnement Render, jamais dans le dépôt) : base Postgres Neon, Francfort.
Une table `donnees (cle, valeur jsonb, maj)` créée au démarrage. Sans la variable, ou base injoignable,
tout tourne en mémoire. `GET /` indique `"stockage": "postgres"` ou `"memoire"`.

## Tests

```
python tests/menteur_essai.py
python tests/casino_essai.py
python tests/fondations_essai.py            # + TEST_DATABASE_URL=postgresql://... pour la base
python tests/banc_reconnexions.py --jeu tous --duree 20
```
