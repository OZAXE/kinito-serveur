# Consignes pour Claude

## Fusion systématique
Après chaque modification demandée, sans redemander l'accord :
1. commiter et pousser sur la branche de travail ;
2. ouvrir une pull request vers `main` ;
3. la fusionner dans `main` (fusion classique) dès que les vérifications passent.

Ce dépôt se fusionne AVANT le site (`table-de-jeu`), dont les pages appellent ce serveur
publié sur Render (`render.yaml`, déploiement depuis `main`). Entre les deux fusions, le site
publié est encore l'ancien : un changement de message doit rester compatible avec lui
(champ ajouté plutôt que renommé, valeur par défaut qui garde l'ancien comportement).

Le README est la documentation de référence (messages, règles, chiffres) : le mettre à jour
dans la même modification quand un comportement change.

## Le projet
Serveur FastAPI + WebSocket des jeux de soirée (`serveur.py`).

- **Registre des jeux** `JEUX` (`serveur.py`) : kinito, beerbattle, buckshot, picolopoly,
  menteur (affiché « Cal 12 »), casino, undercover, telephone (Gribouillophone, préfixe `td_`). Chaque jeu y est déclaré une fois
  (démarrage, actions, reprise, départ, retour, minuteurs, nombre de joueurs) : ajouter un jeu
  = ajouter une entrée.
- **Moteurs de règles purs** à part, testables hors ligne : `picolopoly.py`, `menteur.py`,
  `casino.py`, `undercover.py` (mots dans `undercover_mots.py`), `telephone.py` (config
  `telephone_config.json`, phrases `telephone_phrases.py`), `enquete.py`, `bingo.py`.
- `stockage.py` : Postgres Neon si `DATABASE_URL` est défini (variable Render, jamais dans le
  dépôt), sinon mémoire. Le serveur ne doit jamais planter à cause de la base.
- `protection.py` : garde-fous (débit, taille, connexions par IP).

## Soirée, tables et lancement
- Code de soirée `ABCD` = table 1, autres tables `ABCD-2` … `ABCD-9`. Un prénom n'est qu'à
  une table à la fois.
- **Règle unique de lancement** (`lancer_jeu`) : en salle d'attente le jeu démarre ; le même
  jeu ne se relance que s'il est fini ; remplacer un autre jeu **pas fini** exige
  `"remplacer": true` (envoyé par le site seulement après confirmation), sinon seul le
  demandeur reçoit un refus et la partie continue. Le nombre de joueurs est vérifié avant
  de remplacer. Un lancement réussi diffuse `salon_jeu`.
- `GET /salon/{code}` donne le jeu, `depuis` (secondes) et `fini` ; `GET /soiree/{code}` les
  tables, les présents et `couches_en_cours`.

## Jeux parallèles (couches)
Couches de la soirée (`couches.py`) : Enquête (`enq`, `enquete_couche.py`, packs
`enquetes/*.json`, chiffres dans `enquete_config.json`) et Bingo de soirée (`bng`,
`bingo_couche.py`, `bingo_cases.json`, `bingo_config.json`).
Leurs pages ouvrent `/ws-soiree/{code}/{nom}?page=...` : une connexion par prénom et par page.
Les jeux leur signalent les résultats par un seul point, `signaler(...)`.
Le plafond de gorgées est commun aux couches (`Couche.gorgees_heure`).
Une couche survit à la libération du salon et au redémarrage (sauvegardée par `stockage.py`).

## Tests
Hors ligne, à lancer après toute modification du moteur ou du serveur concerné :
```
python tests/fondations_essai.py      # salons, soirée, lancement, couches
python tests/menteur_essai.py
python tests/bingo_essai.py
python tests/enquete_essai.py
python tests/casino_essai.py
python tests/undercover_essai.py
python tests/telephone_essai.py 300
python tests/picolopoly_essai.py 300
python tests/non_fuite_essai.py 300   # rien ne trahit le tueur de l'Enquête
```
Les tests navigateur (`node tests/*_navigateur.js`, avec `tests/serveur_essai.py`) et les bancs
(`banc_reconnexions.py`, `fuzz_serveur.py`, `securite_essai.py`) sont décrits dans le README.
