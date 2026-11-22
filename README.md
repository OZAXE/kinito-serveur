# kinito-serveur

Serveur multijoueur des jeux de soirée (FastAPI + WebSocket), publié sur Render.

## Organisation

- `serveur.py` : réseau, salons, et **registre des jeux** (`JEUX`). Chaque jeu y est déclaré une fois
  (message de démarrage, actions, reprise après coupure, départ, retour, arrivée en cours de partie,
  minuteurs, nombre de joueurs). Ajouter un jeu = ajouter une entrée.
- Moteurs de règles purs, testables hors ligne : `picolopoly.py`, `menteur.py`, `casino.py`, `undercover.py`
  (mots dans `undercover_mots.py` : familles « classique » et « trash »), `telephone.py` (Gribouillophone :
  chiffres dans `telephone_config.json`, phrases de départ dans `telephone_phrases.py`), `conquete.py` (Royaumes : chiffres
  dans `conquete_config.json`, cartes dans `conquete_cartes.json`, fabriquées par `conquete_cartes.py`), `justepinte.py` (Le Juste Pinte : chiffres dans
  `justepinte_config.json`, questions dans `justepinte_questions.py`), `quidenous.py` (Qui de nous ? : chiffres dans
  `quidenous_config.json`, cartes dans `quidenous_cartes.py`), `tournee.py` (La Tournée : chiffres dans
  `tournee_config.json`, plateaux dans `tournee_plateaux.json` fabriqués par `tournee_plateaux.py`, mini-jeux dans
  `tournee_minijeux.json`).
- `couches.py` : contrat des **couches parallèles** d'une soirée (l'Enquête), qui tournent à côté des jeux.
- `stockage.py` : sauvegarde durable (Postgres Neon si `DATABASE_URL` est défini, sinon mémoire).

## Une soirée, ses tables

- Une soirée a un code de 4 lettres (`ABCD`), qui est aussi sa table 1. Ses autres tables sont
  `ABCD-2` … `ABCD-9` (8 places chacune) : chacune est un salon complet, avec son jeu.
  `GET /soiree/{code}` liste les tables, `GET /soiree/{code}/nouvelle-table` en ouvre une.
- Un prénom n'est qu'à une table à la fois : s'asseoir à une autre le fait quitter la première.
- Les couches (l'Enquête) appartiennent à la soirée : elles voient les joueurs et les victoires
  de toutes les tables.

- Qui est là : `/soiree/{code}?nom=Lea` compte Léa présente sur l'accueil (30 s sans nouvelles : plus
  comptée ; `&table=` donne sa table) ; `presents` réunit les joueurs des tables (avec le jeu), ceux qui ont l'Enquête
  ouverte et l'accueil, avec la table de chacun pour la rejoindre d'un toucher.
- Changer de jeu : une page de jeu se connecte avec `?page=<jeu>`. Si elle arrive à une table dont la partie
  (d'un autre jeu) n'a plus aucun joueur actif, cette partie abandonnée est close : la table repasse en salle
  d'attente du nouveau jeu (`attente` dans `/soiree`), au lieu de renvoyer le joueur dans l'ancien jeu.
- Défaites : `signaler` annonce aussi à la table `table_resultat` (perdants, gagnants, gorgées) ; les pages
  jouent l'animation (commun/soiree.js).

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
  (2 joueurs désignés : `enq_repondu` devant tous, ou `enq_refuser` et des gorgées ; le silence jusqu'au bout compte comme un refus), marée (PE doublés).
- Gorgées : vitesse x dosage (doux 0,5, normal 1, corsé 1,5), unité libre (mode sans alcool). Plafond
  par heure et par joueur (sans par défaut, ou 6, 12, 20) : au-delà, chaque gorgée non bue devient 1 min de handicap
  (archive et accusations bloquées). Récap de fin : gorgées bues et converties par joueur.
- Pénalités visibles par tous (`gorgees_publiques`, activé par défaut) : chaque gorgée imposée apparaît
  dans l'état public (`penalites`) jusqu'à ce que le joueur confirme (`enq_bu`). Contrepartie : on sait
  qui a raté une accusation (jamais son contenu). Un sabotage ne fait jamais boire le tueur.
- Trois univers (`enquetes/manoir.json`, `station.json`, `velvet.json`) : 8 lieux, 8 armes, 12 portraits,
  plan (avec `decor` pour le thème 3D du site), textes du narrateur. Validés au chargement (textes complets,
  modèles qui se remplissent) ; contractions « de le » -> « du » après insertion d'un nom.
- Vitesse Démo (10 min) pour tester seul : chronos courts, 3 événements d'1 min, PE automatiques pour tous
  (`pe_auto_heure`), joueurs fictifs qui jouent toutes les `tic_bots` secondes.
- Joueurs fictifs (`enq_bot_ajouter` / `enq_bot_retirer`, hôte, salle d'attente) : pour tester seul.
  Ils gagnent `bot_pe_heure` PE par heure et jouent avec `jouer_bot`, qui ne voit que ce qu'un vrai joueur
  verrait (ses plis, les découvertes, le retour de ses accusations, déduit façon Mastermind). Le même
  moteur sert au simulateur `tests/simulation.py`.
- Équilibre (simulation, 6 joueurs, 4 PE/h) : l'entraide pèse plus que tout. À 50 % d'entraide, le tueur
  gagne environ 60 % en Flash, 35 % en Express, 50 % en Soirée, 35 % en Marathon ; à 25 %, 70 à 85 %.
- Non-fuite : l'état public n'a jamais la solution ni le tueur avant la fin ; la vue privée a les
  mêmes clés pour tous ; solution et console factices, de même forme, pour les enquêteurs
  (`tests/enquete_essai.py`, `tests/non_fuite_essai.py`).

## Undercover

- 3 à 15 joueurs. Civils (même mot), Undercover (mot proche, sans le savoir), Mr White (aucun mot).
  Les indices se donnent **à l'oral** : la page gère la lecture du mot, l'ordre de parole, le vote, le mot
  de Mr White, les gorgées et les points.
- `uc_demarrer` : `famille` (`classique`, `trash`, `melange`), `undercover` et `white` (nombre de chaque).
  Réglage conseillé par `uc_repartition(n)` ; un réglage invalide (aucun infiltré, ou pas plus de Civils
  que d'infiltrés) est remplacé par le conseillé.
- Actions : `uc_pret`, `uc_fini` (l'orateur a parlé), `uc_voter` (`cible`, modifiable jusqu'au dernier
  vote), `uc_deviner` (`mot`, Mr White éliminé). Égalité : les ex aequo redonnent un indice, revote, puis
  le sort. Le mot de Mr White tolère accents, articles, pluriel et une ou deux fautes, mais pas le mot
  de l'Undercover.
- Secret : `uc_etat` n'a jamais les mots ni les rôles des vivants avant la fin ; chacun reçoit son mot
  par `uc_mot`. Les points (2 / 6 / 10) et les paires déjà jouées suivent d'une partie à l'autre du salon.

## Gribouillophone

- Nom affiché « Gribouillophone » ; les identifiants restent `telephone` (registre, page `telephone-jeu.html`,
  fichiers `telephone*.py`) et le préfixe `td_`, pour ne pas casser les liens et les parties en cours.
- 3 à 8 joueurs, page `telephone-jeu.html` (2D, portrait). On écrit, on dessine, on devine ; à la fin chaque
  album est révélé étape par étape. Toutes les valeurs (minuteurs, tailles, gorgées) sont dans
  `telephone_config.json`. Pas de plafond de gorgées. Trois modes (`mode`) :
  - `normal` : phrase, dessin, description, dessin… ;
  - `knockoff` : une seule phrase de la banque pour tous les albums (`source` ignorée), puis uniquement des
    dessins ; dès la 2e étape, le dessin précédent n'est visible que `visionnage` secondes (5 / 8 / 10 s) puis
    on le recopie de mémoire, avec un temps de dessin qui baisse à chaque étape (x `decroissance`, jamais sous
    `minimum`). Jusqu'à N étapes ;
  - `speedrun` : comme Normal avec des minuteurs très courts (dessin 8 / 12 / 15 s) et 4 étapes par défaut ;
    à chaque étape, l'album de chacun est tiré au hasard (jamais un album déjà touché, tous jouent).
- `td_demarrer` : `vitesse` (`flash`, `normal`, `long`), `source` des phrases de départ (`libre`, `banque` :
  l'étape 0 est sautée, `mixte` : le champ est prérempli par la banque), `mode`, `etapes` (1 à N-1, N en
  Knock-Off ; défaut : toutes, chaque album passe par tout le monde ; 4 en Speedrun). Celui qui lance est l'**hôte** (`hote`) ; s'il part, le premier
  présent le remplace.
- Rotation : N albums ; à l'étape k le joueur i travaille sur l'album (i + k) % N (Speedrun : tirage). Étape 0 : phrase,
  impaires : dessin, paires : description. L'étape finit quand tous les présents ont envoyé, ou à
  l'échéance (+ `delai_grace`) : ce qui manque est rempli par le brouillon, sinon page vide ou « ... ».
- Messages : `td_contribuer` (`contenu` : texte de 80 caractères au plus, ou dessin ; `auto: true` quand la
  page envoie seule à zéro, compte comme un retard), `td_brouillon` (gardé pour une coupure, rien n'est
  diffusé), `td_suggestion` (une autre phrase de la banque, réponse privée `td_suggestion`), `td_reveler`,
  `td_suivant`, `td_auto` (`actif`) réservés à l'hôte, `td_voter` (`cible` = indice dans l'album ; au vote
  final `album` + `cible`), `td_reaction` (`emoji` parmi la liste de la config, diffusé en `td_reaction`).
- Dessin : liste de traits `{"c": couleur 0..7 (8 = gomme), "e": épaisseur 0..2, "p": points}`. Les points
  sont encodés 2 caractères base64url par coordonnée (x puis y, cadre 400 x 300). Au plus 200 traits et
  2500 points : le dessin tient dans un message de 16 Ko. Aucune image n'est acceptée.
- Phases (`td_etat.phase`) : `etape`, `pret` (l'hôte lance la révélation), `revelation`, `vote_album`
  (meilleur moment, anonyme, pas pour soi), `resultat_album`, `vote_final` (dessin le plus mal compris),
  `fin` (`resultats`, `final`).
- Secret : `td_etat` ne contient que l'album en cours de révélation, jusqu'à la contribution dévoilée ;
  chacun reçoit par `td_tache` la SEULE contribution précédente de son album. En Knock-Off, `td_tache` a `copie` et `vision` (secondes de
  visionnage restantes) ; passé ce délai, le serveur ne renvoie plus le modèle (une page rechargée non plus). Tous les albums (`td_galerie`)
  ne partent qu'au vote final.
- Gorgées (x1,25 / x1 / x0,75 selon la vitesse) : la plus votée d'un album distribue 2, la plus votée de la
  partie distribue 3, le dessin le plus mal compris boit 1, une contribution pas envoyée à temps boit 1
  (`td_gorgees` à chaque fois).

## Royaumes (conquête, préfixe `sw_`)

Jeu de conquête de régions, 2 à 8 joueurs, page `conquete-3d-jeu.html`. Inspiré des mécaniques de
Small World (race + pouvoir, conquête, déclin, pièces) ; noms, textes et visuels sont originaux.
Toutes les valeurs sont dans `conquete_config.json` (valeurs de départ, à régler en test).

- **Cartes** : une par nombre de joueurs. Régions conquérables (`cartes` dans la config) : 18, 27, 36, 45, 54
  de 2 à 6 joueurs (9 par joueur, la règle de conception de l'extension officielle Realms), puis 60 et 66 à
  7 et 8 (un peu moins, pour rester jouable au doigt). La taille d'une région reste la même (`aire_region`) :
  l'île grandit avec le nombre de joueurs. Chaque carte est une île : régions aux frontières sinueuses
  (Voronoï relâché, frontières déplacées puis lissées, le même tracé partagé par les deux voisines), mer tout
  autour, lacs intérieurs plus nombreux et plus grands sur les grandes cartes (`eaux` : une entrée par
  cellule d'eau ; ni conquérables, ni voisines à travers l'eau). Terrain (plaine, forêt, colline, marais,
  montagne), marque (mine, source magique), `bord` = la région touche la mer. Fabriquées une fois par
  `conquete_cartes.py` (graine fixe) et vérifiées : terres d'un seul tenant, terrains à 1 près, voisines de
  terrains différents si possible, marques jamais voisines, au moins 30 % des régions au bord.
- **Combinaisons** : race (16) + pouvoir (20) tirés au hasard, pions = race + pouvoir, au moins 7.
  File de 6 : la première gratuite, 1 pièce par combinaison sautée (posée dessus, ramassée par qui la
  prend). `bannis` et `bonus_depart` dans la config ; mode `aleatoire` : 12 races et 15 pouvoirs par partie.
- **Tour** : choisir une combinaison si on n'a pas de peuple actif ; puis étendre (conquêtes puis
  redéploiement) ou passer en déclin (fin du tour). Score de fin de tour : 1 pièce par région (actif et
  déclin) + bonus de race et de pouvoir. Partie en N tours par joueur (vitesse) ; égalité : le plus de
  régions, puis le sort. Premier joueur tiré au sort.
- **Conquête** : coût = 2 + pions adverses + 1 en montagne + fortin + Titans − effets, jamais sous 1.
  Premier pas d'un peuple sur une région de bord, ensuite une voisine (Aérien, Marins, Souterrain, dragon :
  exceptions). Défenseur actif : perd 1 pion, récupère les autres en main ; peuple en déclin : perd tout.
  Dé de renfort (faces 0 0 0 1 2 3, tiré par le serveur) pour une dernière conquête (Furie : à tout moment).
- **Déclin** : 1 pion par région, plus de pouvoirs, 1 pièce par région. L'ancien peuple en déclin
  disparaît (sauf pouvoir Immortel).
- **Interprétations de la trame** : Revenants = en déclin, ils gardent les pièces de leur pouvoir ;
  Mages = relance auto d'un dé raté + 1 pièce par source magique (sinon les sources ne servaient à
  personne) ; Nomades = 2 régions gardées sans pion ; Immortel = ce peuple, une fois en déclin, ne
  disparaît jamais ; Souterrain = depuis une de ses montagnes, toutes les montagnes sont voisines ;
  Endurant = déclin après le score d'un tour où il a conquis. Fortins, camps et gardiens disparaissent
  au déclin.
- **Tours et vitesses** : en Soirée, 10 tours par joueur à 2-3 joueurs, 9 à 4, 8 à 5-6, 7 à 7-8
  (`tours_joueurs`, règle de l'extension officielle Realms prolongée à 7-8). Les vitesses multiplient ce nombre
  (`facteur_tours`, arrondi) et règlent le minuteur et les gorgées : Flash ×0,5 / 40 s / gorgées ×1,5,
  Express ×0,75 / 45 s / ×1,25, Soirée ×1 / 60 s / ×1, Marathon ×1,25 / 90 s / ×0,75. L'hôte peut imposer
  `tours` entre 3 et 15 ; hors bornes, on prend le conseillé (`reglage.tours_conseilles` dans `sw_demarree`).
  À 7 joueurs et plus, Flash par défaut.
- **Gorgées** (sans plafond) : région perdue, l'ancien occupant boit 1 (au plus 3 par tour d'attaquant) ;
  déclin, 2 à distribuer ; fin, le dernier boit 3 et le vainqueur distribue 5 ; option vengeance (3 tours
  adverses d'affilée à perdre des régions : 1 à distribuer).
- **Messages** : `sw_demarrer` (`vitesse`, `tours`, `aleatoire`, `vengeance`, et `remplacer`). Actions du
  joueur dont c'est le tour : `sw_choisir` (`rang`), `sw_declin`, `sw_abandonner` (`region`, avant toute
  conquête), `sw_conquerir` (`region`), `sw_tenter` (`region`, dé de renfort), `sw_dragon` (`region`),
  `sw_fin_conquete`, `sw_redeployer` (`pions` : {région: nombre}, et selon le pouvoir `fortin`, `camp`,
  `gardiens`, `sabotage`, `emissaire`, `declin` pour Endurant). `sw_reaction` (`emoji`) : n'importe qui,
  n'importe quand. Le serveur envoie `sw_demarree` (carte, races, pouvoirs, réglage : au début et à la
  reconnexion) puis `sw_etat` après chaque coup, avec `possibles` (régions conquérables par le joueur
  actif et leur coût, calculées ici : la page ne refait jamais les règles). Événements : `sw_choix`,
  `sw_conquete`, `sw_de`, `sw_declin`, `sw_abandon`, `sw_sabotage`, `sw_score`, `sw_gorgees`,
  `sw_reaction`, `sw_fin`.
- **Minuteur** : à l'échéance (ou 3 s pour un joueur parti), le serveur finit le tour : combinaison
  gratuite si besoin, redéploiement sûr (pions en main vers les régions les plus exposées), score.
- Lien avec l'Enquête : la victoire est signalée, mais ne rapporte rien tant que `conquete` n'est pas
  dans la table des PE de `enquete_config.json` (désactivé par défaut).

## Le Juste Pinte (estimation, préfixe `jp_`)

- 2 à 8 joueurs, page `justepinte-3d-jeu.html`. Une question à réponse chiffrée par manche (fête, alcool,
  ciné et musique, bouffe, jeux de bar, insolite ; jamais une ville en particulier), chacun tape un nombre
  en secret. Toutes les valeurs sont dans `justepinte_config.json`.
- `jp_demarrer` : `manches` (5, 10 ou 15 ; défaut 10), `categories` (liste parmi `alcool`, `fete`,
  `culture`, `bouffe`, `jeux`, `insolite` ; vide = toutes). Les questions déjà posées ce soir dans le salon
  ne reviennent pas tant qu'il en reste.
- Actions : `jp_repondre` (`valeur` : nombre, ou texte « 12,5 », modifiable jusqu'à la révélation ; réponse
  privée `jp_reponse`), `jp_suivant` (passer à la manche suivante quand tous les présents l'ont touché).
- Phases (`jp_etat.phase`) : `question` (30 s, finit dès que tous les présents ont répondu), `revelation`
  (12 s, `question.reponse`, `question.info`, `resultat.lignes` : valeur, écart, rang, points, gorgées),
  `fin` (`final` : classement, gagnants, derniers). Événements `jp_revelation` et `jp_fin`.
- Classement par écart à la bonne réponse, ex aequo au même rang. Points 3 / 2 / 1, +1 « dans le mille »
  (5 % de la réponse, un an pour une année, ou la tolérance propre à la question).
- Gorgées : le plus loin boit 2, l'avant-dernier 1 dès 5 répondants, un présent sans réponse boit 2 ;
  « dans le mille » distribue 3 (jamais de gorgée pour lui) ; en fin de partie, le dernier au classement boit 3
  (seules ces gorgées de fin partent dans `signaler` : celles des manches sont déjà bues).
- Secret : `jp_etat` n'a ni la bonne réponse ni les réponses des joueurs avant la révélation.

## Qui de nous ? (vote social, préfixe `qn_`)

- 3 à 8 joueurs, page `quidenous-3d-jeu.html`. Chaque manche tire une carte « Qui de nous… » (« …finit la
  soirée en dansant sur une table ») ; chacun désigne en secret un autre joueur et parie sur le plus désigné.
  Toutes les valeurs sont dans `quidenous_config.json`, les cartes dans `quidenous_cartes.py`.
- `qn_demarrer` : `manches` (5, 10 ou 15 ; défaut 10), `paquet` (`gentil` par défaut, `epice` ou `melange`),
  `anonyme` (par défaut ; `false` = votes à découvert). Les cartes déjà tirées ce soir dans le salon ne
  reviennent pas tant qu'il en reste.
- Actions : `qn_voter` (`cible` : index d'un autre joueur présent, `pari` : index de n'importe quel joueur,
  soi compris ; modifiable jusqu'à la révélation ; réponse privée `qn_vote`), `qn_suivant`.
- Phases (`qn_etat.phase`) : `vote` (25 s, finit dès que tous les présents ont voté ; `a_vote` dit qui a
  voté, jamais pour qui), `revelation` (12 s, `resultat` : `compte` par joueur, `designes`, `paris` avec
  `bon`, `sans_vote`, `boit` par joueur, et `votes` (qui pour qui) seulement à découvert), `fin` (`final` :
  classement, gagnants, derniers, `titre` = le plus désigné de la partie). Événements `qn_revelation`, `qn_fin`.
- Le ou les plus désignés (ex aequo) boivent 2. Bon pari +1 point, mauvais pari 1 gorgée, présent sans vote
  1 gorgée. Fin : meilleur devin = plus de points ; le dernier au classement boit 2 (seules ces gorgées de fin
  partent dans `signaler`).

## La Tournée (party game plateau + mini-jeux, préfixe `tr_`)

- 2 à 8 joueurs, page `tournee-3d-jeu.html`. Trois plateaux dans `tournee_plateaux.json` (fabriqué par
  `tournee_plateaux.py`, ne pas le modifier à la main) : **La Terrasse** (`terrasse`, facile), **Le Pub** (`pub`,
  moyen, défaut), **La Cave** (`cave`, difficile). Un plateau = une boucle et des branches ; chaque case a un
  `type` (`depart`, `pieces`, `piege`, `evenement`, `tournee`, `chance`, `duel`, `trappe`), des `suivants` (deux =
  embranchement), une `zone` (nom de la branche) et des coordonnées `x`, `z` pour le site, qui pose autour un
  monde 3D (`commun/mondes3d.js` : port ensoleillé, ville de nuit, caves volcaniques ; la boucle serpente pour lui
  laisser de la place). Chaque plateau a ses
  gains (`pieces`), ses gorgées (`boire`), ses événements de case, ses cartes chance et ses événements de plateau.
  La difficulté, c'est surtout les **retournements** : rien à la Terrasse ; vols de pièces, échanges de place,
  duels et coup de pouce au dernier au Pub ; à la Cave en plus, vol de capsule, duels pour une capsule, partage
  de toutes les pièces, trappes et videur. Valeurs communes dans `tournee_config.json`.
- `tr_demarrer` : `tours` (5, 10 ou 15 ; défaut 10), `plateau` (défaut : celui de la partie précédente du salon,
  sinon `pub`). Le dernier mini-jeu joué dans le salon n'ouvre pas la partie suivante.
- `tr_etat` : `carte` (le plateau complet : `id`, `nom`, `difficulte`, `cases`, `boire`...), `plateau` (liste des
  types, ancien format), `positions`, `pieces`, `capsules`, `gorgees`, `capsule_case`, `prix_capsule`, `videur`
  (case du videur ou `null`), `bonus`, `evenement_plateau`, `des`, `options`, `choix`...
- Début de chaque tour (sauf le premier) : à la Cave, le videur avance de 1 à 3 cases ; tous les `tous_les` tours,
  un événement de plateau (`evenement_plateau`, événement `tr_evenement_plateau`) : happy hour (pièces doublées),
  promo sur la capsule, tournée du patron, capsule qui bouge, partage des pièces...
- Un tour, quatre phases (`tr_etat.phase`) :
  1. `des` (20 s) : `tr_lancer`, tout le monde en même temps ; le serveur tire le dé (événement `tr_de`, `des`
     public). Si plusieurs chemins sont possibles, `options[joueur]` les liste (`chemin`, `arrivee`, `libelle`,
     `type_arrivee`) et le joueur choisit par `tr_chemin` (`choix` = indice). À l'échéance, dé et chemin au hasard.
  2. `deplacement` (2,5 s + 1,5 s par joueur, sans action) : on avance dans l'ordre des dés, du plus haut au plus bas.
     Passer par le départ : +5 pièces. Passer ou s'arrêter sur la capsule : achetée si on a de quoi (elle part
     ailleurs, pas juste devant un pion). Passer devant le videur : fouille (−2 pièces) ; s'arrêter sur lui :
     retour au départ et gorgées. Puis la case d'arrivée : `pieces`, `piege` (pièces perdues et gorgées),
     `tournee` (gorgées à distribuer), `evenement`, `chance`, `duel` (contre un joueur au hasard : 5 pièces, ou une
     capsule à la Cave ; le perdant boit), `trappe` (on ressort par l'autre). `deplacements.lignes` : dé, départ,
     chemin, arrivée, effets ; `deplacements.positions` : les positions finales. Événement `tr_deplacements`.
  3. `minijeu` (consignes 7 s + durée du mini-jeu + 6 s de marge) : `minijeu` = `{id, nom, regle, duree, sens,
     consignes, graine, equipes, gorgees_max}`, tiré dans `tournee_minijeux.json`, ni le dernier ni un de la moitié du catalogue la plus
     récemment jouée (`minijeux_recents`, gardé d'une partie à l'autre). Dès
     3 joueurs, 35 % des mini-jeux se jouent en équipes (`equipes` = deux listes : 2 contre 2, 1 contre 3, 3 contre
     5...). Chaque téléphone joue en local avec la même graine, puis envoie `tr_score` (`score`, borné à
     `[0, max]`, une seule fois ; `gorgees` bues pendant le jeu, bornées à `gorgees_max`, jamais de cul sec ;
     réponse privée `tr_score_recu`). `a_joue` dit qui a joué, jamais son score. Événement `tr_minijeu`.
  4. `resultats` (10 s, ou `tr_suivant` de tous) : `resultat_minijeu.lignes` (score, rang, pièces, gorgées du
     classement `boit`, gorgées du jeu `boit_jeu`, `equipe`) ; selon `sens` (`grand` : le plus haut gagne,
     `petit` : le plus bas). Chacun pour soi : 10 / 6 / 3 pièces, le dernier boit (`boire.minijeu_dernier`) ;
     sans score, on est dernier. En équipes : score d'équipe = moyenne de ceux qui ont joué
     (`bilan_equipes.moyennes`), l'équipe gagnante se partage 16 pièces, l'autre boit ; égalité : 3 pièces
     chacun. Événement `tr_resultats`.
- Fin (`final` : classement, gagnants, derniers) : classement aux capsules, puis aux pièces ; le dernier boit
  (`boire.fin_dernier` ; seules ces gorgées partent dans `signaler`). Événement `tr_fin`.
- Les 24 mini-jeux (un module `commun/minijeux/<id>.js` chacun sur le site) :
  - adresse et réflexes : Glisse-Pinte (`glisse_pinte`), Dégaine (`degaine`, le plus bas gagne, 2 faux départs
    = 1 gorgée), Remplis la pinte (`remplis_pinte`), Fléchettes (`flechettes`), Lancer de Cacahuètes
    (`cacahuete`), Pile de Sous-Bocks (`sous_bocks`), Le Rond Parfait (`rond_parfait`) ;
  - rapidité : Tape-Tonneau (`tape_tonneau`), Tire-Bouchon (`tire_bouchon`, temps, le plus bas gagne),
    Chasse-Mouches (`chasse_mouches`), Tranche-Citron (`tranche_citron`, bouteille = 1 gorgée, 2 au plus),
    Attrape-Glaçons (`attrape_glacons`, piment = 1 gorgée, 2 au plus) ;
  - esquive et survie : Sol glissant (`sol_glissant`), Saute-Tabouret (`saute_tabouret`), Plateau en équilibre
    (`equilibre`) ; les deux qui font tomber le plateau ou la pinte = 1 gorgée ;
  - mémoire et observation : Mémo-Cocktail (`memo_cocktail`), Paires de Sous-Bocks (`paires`, temps),
    Bonneteau (`bonneteau`), Le Bon Compte (`bon_compte`, écart, 3 ou plus = 1 gorgée), Le Bon Verre
    (`bon_verre`, temps) ;
  - tête et rythme : L'Addition (`addition`, 3 erreurs = 1 gorgée), La Bonne Couleur (`bon_cocktail`), Le Bon
    Tempo (`tempo`), Chrono-Mousse (`chrono_mousse`, compter 3, 5 et 7 s à l'aveugle, écart).

  Ajouter un mini-jeu = une entrée dans
  `tournee_minijeux.json` (`equipes: false` s'il ne se joue jamais en équipes, `gorgees_max` s'il fait boire) et un
  module `commun/minijeux/<id>.js` sur le site.

## Base de données

`DATABASE_URL` (variable d'environnement Render, jamais dans le dépôt) : base Postgres Neon, Francfort.
Une table `donnees (cle, valeur jsonb, maj)` créée au démarrage. Sans la variable, ou base injoignable,
tout tourne en mémoire. `GET /` indique `"stockage": "postgres"` ou `"memoire"`.

## Tests

```
python tests/menteur_essai.py
python tests/telephone_essai.py 300         # rotation, non-fuite des albums, votes, gorgées, absents
python tests/undercover_essai.py             # règles, mot de Mr White (tolérance), égalités, non-fuite des mots
python tests/conquete_essai.py 300          # Royaumes : cartes, règles dirigées, parties au hasard (invariants)
python tests/casino_essai.py
python tests/justepinte_essai.py 300     # Juste Pinte : banque de questions, non-fuite, classement, gorgées
python tests/quidenous_essai.py 300      # Qui de nous ? : cartes, non-fuite des votes, désignés, paris, gorgées
python tests/tournee_essai.py 300        # La Tournée : plateaux, chemins, retournements, équipes, mini-jeux, non-fuite des scores
python tests/picolopoly_essai.py 300       # parties au hasard (coups absurdes, absents, faillites) : invariants du plateau
python tests/fondations_essai.py            # + TEST_DATABASE_URL=postgresql://... pour la base
python tests/enquete_essai.py
python tests/non_fuite_essai.py 300         # parties au hasard : rien ne trahit le tueur, à chaque étape
python tests/simulation.py --partage 0.5    # équilibre : victoires du tueur, accusations, gorgées/h
node tests/enquete_navigateur.js            # 4 joueurs : salle d attente, rôles, onglet par-dessus un jeu
node tests/solo_navigateur.js               # un seul joueur et des joueurs fictifs
node tests/mode_test_navigateur.js          # mode test du site (pseudo Admin01) : bots dans chaque jeu
node tests/presents_navigateur.js           # accueil : qui est dans la soirée, annonce des arrivées
node tests/menteur_annonces_navigateur.js   # Cal 12 (ex-Menteur) : MENTEUR !, VRAI / MENSONGE, tir, visibles de tous
node tests/undercover_navigateur.js [captures]  # Undercover : partie complète à 5 navigateurs (un par joueur)
node tests/conquete_navigateur.js [captures]    # Royaumes : partie Flash à 3 navigateurs, en touchant la carte
node tests/justepinte_navigateur.js [captures] # Le Juste Pinte : 5 questions à 3 navigateurs, dans le mille, classement, Rejouer
node tests/quidenous_navigateur.js [captures]  # Qui de nous ? : 5 cartes à 3 navigateurs, vote, pari, à découvert, titre, Rejouer
node tests/tournee_navigateur.js [captures]    # La Tournée : 5 tours à 3 navigateurs, dés, plateau, mini-jeux au doigt, classement, Rejouer
# Machine sans accès aux CDN : CDN_LOCAL=<dossier des paquets npm three et qrious décompressés> (tests justepinte, quidenous et mode test)
python tests/banc_reconnexions.py --jeu tous --duree 20   # les sept jeux, coupures et retours au hasard
python tests/fuzz_serveur.py --duree 8      # messages de travers a chaque jeu : aucune exception serveur
python tests/securite_essai.py              # attaques : taille, debit, codes, places, origines, limites par IP
node tests/navigateur.js                    # les six jeux ; serveur_essai.py + site sur localhost:8765
# Pour les tests de soirée et d enquête (scènes 3D lourdes sans carte graphique) :
#   DELAI_SILENCE=30 ENQ_RAPIDE=1 python tests/serveur_essai.py
node tests/soiree_navigateur.js             # parcours d une soirée (accueil, changement de jeu)
bash tests/ci_navigateur.sh ../table-de-jeu [test ...]  # lance serveur d essai + site, enchaîne les tests navigateur
```

### Vérifications automatiques (GitHub Actions)

`.github/workflows/tests.yml` tourne sur chaque pull request et à chaque arrivée sur `main` :
- **Hors ligne** : les tests `*_essai.py` ci-dessus (sauf `simulation.py`), un par machine, en parallèle.
  Bloquant : une PR ne se fusionne que si tous sont verts.
- **Navigateur** : `tests/ci_navigateur.sh <test>`, un test par machine, sur le site publié (`table-de-jeu`, `main`). Pour info pour
  l instant (`continue-on-error`) : un échec s affiche sans bloquer la fusion.

Le site (`table-de-jeu`) a la même vérification navigateur, jouée sur ses PR contre ce dépôt (`main`) :
`tests/ci_navigateur.sh` doit donc rester ici, avec la liste des tests navigateur à jour.
