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
  `tournee_minijeux.json`), `loupgarou.py` (Loup-Garou : chiffres dans `loupgarou_config.json`), `pichenette.py`
  (Pichenette FC : règles et physique, chiffres et formations dans `pichenette_config.json`).
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
- `GET /salon/{code}` : joueurs, jeu en cours, page à ouvrir, couches actives, bots (voir plus bas).

## Bots du mode test (pseudo Admin01)

Un pseudo de test (`PSEUDOS_TEST` dans `serveur.py`, aujourd'hui `Admin01`, majuscules indifférentes)
assis à une table peut y ajouter des bots qui jouent contre lui. Le site le fait depuis l'onglet 🧪.

- `GET /salon/{code}/bots/ajouter?par=Admin01` : un bot (« Bot Léa », prénom libre dans toute la soirée)
  s'assoit à la table. En pleine partie, il entre comme n'importe quel arrivant (si le jeu le permet).
- `GET /salon/{code}/bots/retirer?par=Admin01&nom=Bot%20Léa` : il part comme un joueur qui ferme sa page.
- Les deux répondent `{"bots": [...], "bots_jeux": [...], "jeu"}` (403 si `par` n'est pas un pseudo de test
  assis à cette table, 409 si la table est pleine). `GET /salon/{code}` donne aussi `bots` et `bots_jeux`.
- Pour les jeux, un bot est un joueur ordinaire (une place, un index) dont la « connexion » est un faux
  WebSocket (`WsBot`) : chaque message reçu réveille sa boucle (`vie_bot`), qui demande au cerveau du jeu
  (`bots.py`) quoi faire, attend un temps de réflexion, puis joue sous le verrou du salon par
  `traiter_message`, exactement comme un téléphone.
- Un cerveau ne lit que ce que le joueur voit (l'état public, sa main, ses dés, ses messages privés) ;
  exception assumée au Juste Pinte, où le bot connaît la réponse à un bruit près (joueur plus ou moins cultivé).
- Tous les jeux de table ont un cerveau (`bots.CERVEAUX`). Quelques choix :
  - Loup-Garou : tous les rôles, à partir de sa seule vue secrète (`lg_vue`) ; la meute s'accorde sur une proie.
  - Casino : c'est Admin01 qui choisit la table (roulette, blackjack, poker), les bots misent et jouent.
  - Pichenette FC : le bot essaie quelques tirs avec la vraie physique (sans connaître les visées adverses),
    garde le meilleur et le joue avec un peu de maladresse ; avec un coéquipier, il valide sa formation sans la changer.
  - La Tournée : les mini-jeux se jouent sur les téléphones, le bot envoie un score crédible (fourchette de
    joueur moyen) à la fin du temps du mini-jeu.
  - Undercover : un bot ne parle pas, il touche « J'ai parlé » puis vote au hasard ; Mr White tente un mot.
  - Gribouillophone : phrases de la banque, gribouillis au hasard, votes au hasard ; Admin01 (l'hôte) mène
    la révélation.
  - Kinito, BeerBattle, Buckshot : certaines étapes reviennent à un humain (« Nouvelle manche » du Kinito).
  Un jeu sans cerveau laisserait ses bots assis sans jouer (le panneau du site le signale).
- Les bots ne retiennent pas un salon : sans humain présent (`personne_a_table`), ils arrêtent de jouer, les
  minuteurs s'arrêtent et le salon se libère comme d'habitude. Ils ne comptent pas parmi les présents de la
  soirée pour les couches (l'Enquête n'en fait pas des suspects).

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
  moyen, défaut), **La Cave** (`cave`, difficile). Construits comme les cartes de Mario Party : 82 à 89 cases,
  une grande boucle découpée en quartiers (la plage, la colline du phare, la butte, le cratère...), deux
  embranchements, un raccourci à péage (Pub, Cave), un gadget (bateau, tram, geyser, trappes), deux boutiques,
  au moins 4 cases objet et 7 ou 8 emplacements possibles pour la capsule (`capsule_possibles`). Chaque case a
  un `type` (`depart`, `pieces`, `piege`, `evenement`, `tournee`, `chance`, `duel`, `trappe`, `objet`,
  `boutique`, `peage`, `transport`), des `suivants` (deux = embranchement), une `zone` (le quartier), des
  coordonnées `x`, `z` (de -2,5 à 2,5) et une hauteur `y` (de 0 au bord de l'eau à environ 1 au sommet) pour le
  site, qui modèle autour un monde 3D en relief (`commun/mondes3d.js` : port ensoleillé, ville de nuit, caves
  volcaniques). Une boutique a un `nom` et un `stock` ; un `transport` (aller simple) et une `trappe`
  (aller-retour) ont un `vers`, le transport un `moyen` (`bateau`, `tram`, `geyser`). `python
  tournee_plateaux.py svg` dessine aussi chaque carte en SVG pour la vérifier. Chaque plateau a ses
  gains (`pieces`), ses gorgées (`boire`), ses événements de case, ses cartes chance et ses événements de plateau.
  La difficulté, c'est surtout les **retournements** : rien à la Terrasse ; vols de pièces, échanges de place,
  duels et coup de pouce au dernier au Pub ; à la Cave en plus, vol de capsule, duels pour une capsule, partage
  de toutes les pièces, trappes et videur. Valeurs communes dans `tournee_config.json`.
- **Le dé** va de 1 à 10 (`de.faces`). **Les objets** (`objets` de `tournee_config.json`, 3 au plus dans la poche,
  `poche_max`, publics comme dans Mario Party) : Double shot et Triple shot (2 ou 3 dés), Café serré (+3), Dé pipé
  (on choisit de 1 à 10), Taxi doré (juste avant la capsule), Chaises musicales (échange de place avec un joueur
  choisi), Main baladeuse (vole 5 à 10 pièces), Vide-poche (vole un objet), Verre renversé (piège posé sur sa
  case : le prochain autre joueur qui s'y arrête donne 10 pièces au poseur), Ange gardien (passif : bloque le
  prochain vol de pièces, d'objet ou de capsule) et Tournée offerte (un joueur choisi boit 2 gorgées). On en gagne
  sur une case `objet` (pris dans `objets_case` du plateau ; poches pleines : +5 pièces) ou en boutique.
  La Terrasse ne vend rien pour voler.
- `tr_demarrer` : `tours` (5, 10 ou 15 ; défaut 10), `plateau` (défaut : celui de la partie précédente du salon,
  sinon `pub`). Le dernier mini-jeu joué dans le salon n'ouvre pas la partie suivante.
- `tr_etat` : `carte` (le plateau complet : `id`, `nom`, `difficulte`, `cases`, `boire`...), `plateau` (liste des
  types, ancien format ; `carte` donne aussi `peage`, `capsule_possibles`, `faces`, `poche_max` et le catalogue
  `objets`), `positions`, `pieces`, `capsules`, `gorgees`, `capsule_case`, `prix_capsule`, `videur` (case du
  videur ou `null`), `bonus`, `evenement_plateau`, `des`, `des_detail` (les dés lancés), `options`, `choix`,
  `trajets` (la route déjà décidée ce tour), `objets` (les poches), `objet_tour`, `utilisations` (objets utilisés
  ce tour), `pieges_poses` (case -> poseur), `boutiques` et `achats` (phase `boutique`)...
- Début de chaque tour (sauf le premier) : à la Cave, le videur avance de 2 à 5 cases ; tous les `tous_les` tours,
  un événement de plateau (`evenement_plateau`, événement `tr_evenement_plateau`) : happy hour (pièces doublées),
  promo sur la capsule, tournée du patron, capsule qui bouge, partage des pièces, soldes en boutique...
- Un tour, quatre phases (`tr_etat.phase`), plus la boutique :
  1. `des` (20 s) : d'abord, si on veut, un objet : `tr_objet` (`objet`, `cible` = indice d'un autre joueur pour
     les objets qui visent quelqu'un, `valeur` de 1 à 10 pour le dé pipé), un seul par tour, avant son dé
     (événement `tr_objet_utilise`). Puis `tr_lancer` (`des` = 1 ou 2, au choix à chaque tour ; absent : 1),
     tout le monde en même temps ; le serveur tire les dés de 10, plus un avec le Double shot, deux avec le
     Triple shot (événement `tr_de` avec `valeur` et `detail`). Le joueur avance jusqu'au premier embranchement de sa route :
     `options[joueur]` liste alors les routes (`chemin` jusqu'au prochain embranchement ou au bout du dé,
     `arrivee`, `libelle`, `type_arrivee`, `peage`) et il choisit par `tr_chemin` (`choix` = indice) ; un
     embranchement plus loin repose la question. Un péage trop cher n'est pas proposé. À l'échéance, dé et
     chemins au hasard.
  2. `deplacement` (2,5 s + 1 s par joueur + 0,18 s par case parcourue + 0,6 s par effet, sans action) : on avance dans l'ordre des dés, du plus haut au plus bas.
     Passer par le départ : +5 pièces. Passer ou s'arrêter sur la capsule : achetée si on a de quoi (elle part
     sur un de ses emplacements, à 7 à 18 cases du joueur le plus proche si possible). Passer un `peage` :
     `peage` pièces. Passer devant le videur : fouille (−2 pièces) ; s'arrêter sur lui : retour au départ et
     gorgées. Puis un verre renversé posé par un autre (10 pièces pour lui), et la case d'arrivée : `pieces`,
     `piege` (pièces perdues et gorgées), `tournee` (gorgées à distribuer), `evenement`, `chance`, `duel` (contre
     un joueur au hasard : 5 pièces, ou une capsule à la Cave ; le perdant boit), `trappe` (on ressort par
     l'autre), `transport` (on part au `vers`), `objet` (un objet gratuit). `deplacements.lignes` : dé, départ,
     chemin, arrivée, effets ; `deplacements.positions` : les positions finales. Événement `tr_deplacements`.
  2 bis. `boutique` (15 s, seulement si quelqu'un est passé devant une boutique, a de quoi payer et de la place
     dans ses poches) : `boutiques[joueur]` = `{case, nom, stock: [{id, prix}]}` ; `tr_acheter` (`objet`, ou
     `null` pour ne rien prendre), une fois. Événements `tr_boutique` et `tr_achat`.
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

## Loup-Garou (rôles cachés, préfixe `lg_`)

- 4 à 20 joueurs, page `loupgarou-3d-jeu.html`. Pas de meneur : le serveur fait le conteur, chacun joue
  sur son téléphone. Toutes les valeurs (délais, nombre de loups, gorgées, rôles automatiques) sont dans
  `loupgarou_config.json`.
- Les rôles portent leurs noms officiels (`LG_NOMS_ROLES`). Quatre identifiants datent de la première version et
  sont gardés : `cartomancienne` = **Voyante**, `videur` = **Salvateur**, `herboriste` = **Sorcière**,
  `braconnier` = **Chasseur**.
  - Camp des loups (la meute vote une victime chaque nuit, les loups se voient) : **Loup-Garou** (`loup`),
    **Infect Père des Loups** (`infect` : une fois par partie, `infecter: true` dans son `lg_nuit` ; la victime
    rejoint la meute au lieu de mourir, garde sa carte et le sait par `lg_converti`), **Loup Blanc**
    (`loup_blanc` : chasse avec la meute mais gagne seul ; les nuits paires, ou chaque nuit quand il ne reste que
    des loups, `solo` = un loup à dévorer).
  - Village : **Simple Villageois** (`villageois`), **Voyante** (voit la carte d'un joueur par nuit, `lg_vision`),
    **Salvateur** (protège un joueur des loups, lui compris, jamais le même deux nuits de suite), **Sorcière**
    (potion de vie pour la victime des loups, potion de mort, une fois chacune), **Chasseur** (à sa mort, il abat
    un joueur), **Cupidon** (`cupidon` : nuit 1, `cible` + `cible2` = les amoureux, prévenus par `lg_amoureux` ;
    si l'un meurt, l'autre meurt de chagrin ; un couple loup + village forme son propre camp), **Petite Fille**
    (`petite_fille` : voit en direct les cibles de la meute, sans savoir qui vote ; chaque nuit 20 % de risque
    que les loups la repèrent, `lg_petite_fille`), **Ancien** (`ancien` : résiste à la première attaque des loups ;
    tué par le village, vote, Chasseur ou Sorcière, le village perd ses pouvoirs : `pouvoirs_perdus`),
    **Idiot du Village** (`idiot` : éliminé au vote, il est épargné, dévoilé, `idiot_revele`, et ne vote plus),
    **Corbeau** (`corbeau` : désigne un joueur qui aura 2 voix de plus contre lui au vote du lendemain, annoncé à
    l'aube), **Renard** (`renard` : flaire un joueur et ses deux voisins vivants, `lg_renard` dit s'il y a un loup ;
    sinon il perd son flair), **Enfant Sauvage** (`enfant_sauvage` : nuit 1, choisit un modèle ; si le modèle
    meurt, il devient loup), **Montreur d'Ours** (`montreur` : à l'aube, `grogne` si un voisin vivant est loup),
    **Chevalier à l'Épée Rouillée** (`chevalier` : dévoré, le premier loup vivant à sa gauche, index suivants,
    meurt à l'aube d'après, cause `rouille`), **Bouc Émissaire** (`bouc` : en cas d'égalité, c'est lui qui part),
    **Ange** (`ange` : gagne seul s'il meurt avant la fin du premier vote), **Chien-Loup** (`chien_loup` : choisit
    son camp avec `lg_pret` + `camp: "loups"|"village"`, village par défaut), **Sœurs** (`soeur`, 2 cartes) et
    **Frères** (`frere`, 3 cartes) qui se connaissent (`famille`).
  - Solitaires (venus des versions en ligne, seulement sur demande, chacun prend une place hors loups) :
    **Vampire** (`vampire` : camp des vampires ; les nuits impaires, `type_nuit: "vampire"`, ils mordent un joueur
    (sur un loup, la morsure échoue en silence pour ne pas trahir la meute), la proie la plus mordue devient vampire en secret à l'aube, garde sa carte mais perd son
    pouvoir, prévenue par `lg_converti` avec `cause: "vampire"` ; les vampires se voient, `vampires`, et voient les
    morsures en direct, `lg_morsure`), **Pyromane** (`pyromane` : chaque nuit il arrose un joueur, `cible`, ou il
    envoie `allumer: true` : tous les arrosés encore en vie brûlent à l'aube, cause `brule` ; `arroses` dans son
    `lg_secret`), **Assassin** (`assassin` : tue un joueur chaque nuit, cause `assassine` ; les loups ne peuvent pas
    le dévorer). Le Salvateur protège aussi de la morsure et de l'Assassin, pas du feu.
  - **Capitaine** (option `capitaine` de `lg_demarrer`, automatique dès 9 joueurs) : élu au premier jour (phase
    `election`, `lg_voter`, on peut voter pour soi), sa voix compte double, il nomme son successeur en mourant
    (phase `succession`, `lg_tir` avec `cible` ; au hasard sinon).
- `lg_demarrer` : `loups` (nombre ; absent = automatique : 1 jusqu'à 6 joueurs, 2 jusqu'à 10, 3 jusqu'à 13, 4
  jusqu'à 16, puis 5 ; jamais plus d'un loup pour deux autres joueurs), `speciaux` (liste ; absent = selon le
  nombre de joueurs : Voyante dès 4, Salvateur 6, Sorcière 7, Chasseur 8, Cupidon 9, Petite Fille 10, Ancien 11,
  Infect 12, Idiot 13, Corbeau 14, Renard 15, Enfant Sauvage 16, Loup Blanc 17, Montreur 18, Chevalier 19,
  Bouc 20 ; Ange, Chien-Loup, Sœurs, Frères, Vampire, Pyromane et Assassin seulement sur demande), `capitaine` (booléen). Infect et Loup Blanc
  prennent une place de loup (Loup Blanc : 2 loups au moins). Les autres sont Simples Villageois. La composition
  est publique (`composition`), les rôles non.
- Phases (`lg_etat.phase`) : `role` (30 s, `lg_pret`), `nuit` (50 s), `potion` (seulement si la Sorcière est dans la
  partie : 25 s si elle peut agir, sinon 5 à 9 s au hasard pour que la durée ne trahisse rien), `aube` (morts de la
  nuit, `lg_suivant`), `tir` (25 s, le Chasseur mort vise), `succession` (25 s), `election` (90 s, premier jour),
  `debat` (180 s), `verdict` (`lg_suivant`), `fin`.
- La nuit, **tout le monde en vie** envoie `lg_nuit` (`cible`) pour que personne ne se trahisse à la table ;
  `lg_secret.type_nuit` dit quoi faire : `meute` (réponse privée `lg_meute` à chaque loup), `vision`, `protection`,
  `renard`, `corbeau`, `cupidon`, `modele`, `vampire`, `pyromane`, `assassin`, ou `suspect` (le « flair », compté en fin de partie). La nuit finit
  quand tous ont agi et que la meute est d'accord, ou à l'échéance : la victime est le choix majoritaire de la
  meute (égalité : le sort ; Cupidon et l'Enfant Sauvage absents : au hasard). `lg_potion` (`soin` : booléen,
  `poison` : index ou null). `lg_tir` (`cible`). `lg_voter` (`cible` : index d'un autre vivant, ou null = blanc ;
  modifiable, réponse privée `lg_vote`). Le plus désigné est éliminé s'il a plus de voix que les blancs ; égalité :
  personne, sauf le Bouc Émissaire.
- `lg_etat` (public) : `vivants`, `roles` (ceux des morts et de l'Idiot dévoilé, tous à la fin), `a_vote`, `attente`,
  `annonce` (aube : `morts` avec rôle, cause et `converti`, `corbeau`, `grogne` ; verdict : `votes` avec `poids`,
  `compte`, `blancs`, `elimine`, `accusateurs`, `idiot`, `bouc`, `corbeau` ; `tir`, `succession`), `tireur`,
  `capitaine`, `capitaine_option`, `idiot_revele`, `pouvoirs_perdus`, `corbeau`, `morts`, `gorgees`, `final`
  (`vainqueur` : `village`, `loups`, `loup_blanc`, `vampires`, `pyromane`, `assassin`, `amoureux`, `ange` ou
  `personne` ; `gagnants`, `perdants`, `roles`, `convertis`, `vampires`, `amoureux`, `flair`, `meilleur_flair`).
  `lg_secret` (privé, après chaque coup) : `role`, `vivant`, `converti`, `type_nuit`, `loups` (pour un loup),
  `meute`, `espion` (Petite Fille), `petite_fille` (loups, si repérée), `action`, `couple` (Cupidon), `vote`,
  `visions`, `flairs` et `renard_actif`, `dernier_protege`, `potions` et `victime` (Sorcière pendant la potion),
  `infection` et `infecter` (Infect), `solo_possible` et `solo` (Loup Blanc), `amoureux`, `famille`, `modele`,
  `chien`, `vampires` et `morsures` (vampires), `arroses` et `allumer` (Pyromane), `tous_roles`, `tous_convertis`
  et `tous_vampires` pour un mort. Événements `lg_nuit`, `lg_aube`, `lg_tir_attente`,
  `lg_tir`, `lg_succession_attente`, `lg_capitaine`, `lg_verdict`, `lg_fin`.
- Victoire : le village quand il est le seul camp en vie ; les loups (ou les vampires) dès qu'ils sont au moins
  aussi nombreux que les autres vivants, s'il ne reste qu'eux et le village (sinon il faut un seul camp en vie) ;
  le Loup Blanc, le Pyromane, l'Assassin ou le couple mixte seul ; l'Ange s'il meurt au premier tour. Gorgées :
  mort la nuit 2 (dévoré, brûlé, assassiné), abattu 2, éliminé au vote 2 (loup démasqué 3), mort de chagrin 2, chaque accusateur d'un innocent éliminé 1, en fin de
  partie 2 pour chaque joueur du camp perdant (seules celles-ci partent dans `signaler`).

## Pichenette FC (foot de comptoir à pichenettes, préfixe `pf_`)

- 2 à 8 joueurs, page `pichenette-3d-jeu.html`. Foot de table au tour par tour avec des capsules de bière
  (5 par équipe) et un bouchon en guise de balle, sur une table de bistrot. Pas d'écran principal : chacun
  joue sur son téléphone, son but en bas. Toutes les valeurs (délais, terrain, physique, gorgées,
  formations) sont dans `pichenette_config.json`.
- Équipes tirées au sort à chaque partie : **Les Blondes** (équipe 0, en bas, capsules 0 à 4, attaquent vers
  les y positifs) et **Les Brunes** (équipe 1, en haut, capsules 5 à 9). Nombre impair : le dernier arrivé à la
  table (ordre des places) regarde (`spectateurs`).
- `pf_demarrer` : `buts` (3, 4 ou 5 ; défaut 3).
- Phases (`pf_etat.phase`) :
  - `formation` (45 s) : `pf_formation` (`attaque`, `defense` : identifiants parmi les 6 formations, `valider` :
    booléen ; n'importe quel joueur de l'équipe, la dernière demande gagne). L'attaque sert après avoir marqué,
    la défense après avoir encaissé et au premier coup d'envoi. Au chrono, les choix en cours valent.
  - `visee` (20 s) : les deux équipes visent en secret et en même temps. `pf_viser` (`tirs` : liste de
    `{palet, dx, dy, force}`, direction dans le repère du terrain, force de 0 à 1 ; `pret` : booléen) remplace
    les visées du joueur. En 1 contre 1, 2 capsules par joueur, sinon 1. Une capsule n'est tirée qu'une fois par
    tour (refus si un coéquipier l'a déjà prise). Une visée posée sans valider part quand même au chrono. La
    phase finit dès que tous les joueurs présents ont validé.
  - `tir` : toutes les capsules partent ensemble, le serveur calcule la physique (pas fixe de 4 ms, frottement,
    chocs entre disques, rebonds sur les bandes, les poteaux et les filets ; les capsules freinent plus que
    le bouchon : `frottement`/`amorti` pour elles, `frottement_balle`/`amorti_balle` pour lui) et diffuse `pf_tir` (`tir.images` :
    une image toutes les 40 ms, `[x0, y0, x1, y1, ...]` en millièmes de terrain, capsules 0 à 9 puis le bouchon ;
    `tirs` dévoilés, `but` : équipe qui marque ou null, `buteur`, `contre_son_camp`, `boit`, `passe`). La phase dure le
    temps du tir plus 2 s (3,5 s après un but), puis nouveau tour. Une capsule arrêtée avec le centre dans une
    cage est ressortie juste devant la ligne de but (première place libre ; dernière image du tir en plus).
    Belle passe (`tir.passe` : `{de, palet, vers, equipe}` ou null) : le bouchon finit sur une capsule de la
    même équipe, pas tirée ce tour, juste après une capsule tirée de cette équipe. Contrôle
    (`physique.controle_passe`, vrai par défaut) : le bouchon s'arrête alors collé à la receveuse, sans la pousser. Puis il pivote autour d'elle (quelques
    images en plus) pour se placer face au but adverse, ou le plus près possible si une capsule ou une bande gêne.
  - `fin` : `final` (`gagnante`, `gagnants`, `perdants`, `derniers`, `score`).
- But : le bouchon passe entièrement la ligne entre les poteaux. Chaque joueur présent de l'équipe qui encaisse
  boit 1 gorgée ; si la dernière capsule à avoir touché le bouchon est de cette équipe et a été tirée ce tour,
  son tireur boit 2 de plus (contre son camp). Replacement : formation d'attaque pour l'équipe qui a marqué,
  de défense pour l'autre, bouchon au centre. Première équipe à `buts_victoire` : chaque joueur de l'équipe
  perdante boit 5 gorgées (seules celles-ci partent dans `signaler`).
- `pf_catalogue` (au démarrage et à la reprise) : formations, terrain, rayons. `pf_etat` (public) : équipes,
  score, positions, `formations_validees`, `formations` (seulement après le coup d'envoi), `a_vise` (nombre de
  capsules visées, jamais où), `prets`, `tir` (sans ses images), `buts`, `gorgees`. `pf_vue` (privé, à chaque joueur
  après chaque coup) : `equipe`, `formation` et `visees` de son équipe seulement.

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
python tests/loupgarou_essai.py 300      # Loup-Garou : composition, règles dirigées, non-fuite des rôles et de la nuit
python tests/pichenette_essai.py 300     # Pichenette FC : formations, physique, non-fuite des visées, buts, gorgées
python tests/tournee_essai.py 300        # La Tournée : plateaux, chemins, retournements, équipes, mini-jeux, non-fuite des scores
python tests/picolopoly_essai.py 300       # parties au hasard (coups absurdes, absents, faillites) : invariants du plateau
python tests/picolopoly_rythme.py          # Picopoly : gorgées par personne et par heure (cibles 100 / 40 / 30)
python tests/bots_essai.py [jeu ...]        # bots du mode test : parties complètes contre des bots, accès, libération
python tests/fondations_essai.py            # + TEST_DATABASE_URL=postgresql://... pour la base
python tests/enquete_essai.py
python tests/non_fuite_essai.py 300         # parties au hasard : rien ne trahit le tueur, à chaque étape
python tests/simulation.py --partage 0.5    # équilibre : victoires du tueur, accusations, gorgées/h
node tests/enquete_navigateur.js            # 4 joueurs : salle d attente, rôles, onglet par-dessus un jeu
node tests/solo_navigateur.js               # un seul joueur et des joueurs fictifs
node tests/mode_test_navigateur.js          # mode test du site (pseudo Admin01) : panneau 🧪, bots dans chaque jeu
node tests/presents_navigateur.js           # accueil : qui est dans la soirée, annonce des arrivées
node tests/menteur_annonces_navigateur.js   # Cal 12 (ex-Menteur) : MENTEUR !, VRAI / MENSONGE, tir, visibles de tous
node tests/undercover_navigateur.js [captures]  # Undercover : partie complète à 5 navigateurs (un par joueur)
node tests/conquete_navigateur.js [captures]    # Royaumes : partie Flash à 3 navigateurs, en touchant la carte
node tests/justepinte_navigateur.js [captures] # Le Juste Pinte : 5 questions à 3 navigateurs, dans le mille, classement, Rejouer
node tests/quidenous_navigateur.js [captures]  # Qui de nous ? : 5 cartes à 3 navigateurs, vote, pari, à découvert, titre, Rejouer
node tests/loupgarou_navigateur.js [captures]  # Loup-Garou : partie complète à 6 navigateurs, chaque rôle, vote, Rejouer
node tests/pichenette_navigateur.js [captures] # Pichenette FC : partie 2 contre 2 à 4 navigateurs, formations, visée au doigt, buts, Rejouer
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
