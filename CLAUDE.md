# Consignes pour Claude

## Fusion systématique
Après chaque modification demandée, sans redemander l'accord :
1. commiter et pousser sur la branche de travail ;
2. ouvrir une pull request vers `main` ;
3. la fusionner dans `main` (fusion classique) dès que les vérifications passent.

Ce dépôt se fusionne AVANT le site (`table-de-jeu`), dont les pages appellent ce serveur
publié sur Render.

## Le projet
Serveur FastAPI + WebSocket des jeux de soirée (`serveur.py`). Les moteurs de règles purs
sont à part (`picolopoly.py`, `menteur.py`, `bingo.py`...), testables hors ligne :
`python tests/menteur_essai.py`, `python tests/bingo_essai.py`.

Jeux parallèles de la soirée = couches (`couches.py`) : Enquête (`enq`) et Bingo de soirée (`bng`).
Leurs pages ouvrent `/ws-soiree/{code}/{nom}?page=...` : une connexion par prénom et par page.
Le plafond de gorgées est commun aux couches (`Couche.gorgees_heure`).
