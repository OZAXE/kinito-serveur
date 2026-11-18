"""
Banc de test des departs et retours, pour les six jeux.

Lance le vrai serveur (serveur.py) en local, fait jouer des robots qui
respectent le protocole des clients 3D, et leur fait quitter et rejoindre la
partie au hasard, de toutes les facons qu un telephone peut le faire en
soiree :

  coupure     la connexion se ferme proprement, il revient avec son jeton
  reseau      la connexion tombe sans prevenir (Wi-Fi perdu), il revient
  fantome     il revient avec son jeton alors que l ancienne connexion est
              toujours ouverte (onglet rouvert, appli relancee)
  sans_jeton  il revient sous le meme prenom mais sans jeton (bouton Quitter,
              autre navigateur, appli installee vs Safari)
  veille      telephone verrouille : la connexion reste ouverte mais plus
              rien ne repond (ni actions, ni battements de coeur), puis il
              se reveille
  definitif   il part pour de bon

Le banc verifie en continu :
  - aucun prenom en double a la table (chaque robot a un prenom unique) ;
  - la partie avance : si plus rien ne bouge alors qu au moins deux joueurs
    sont la et reveilles, c est un blocage ;
  - aucune erreur cote serveur.

Usage :  python tests/banc_reconnexions.py [--jeu kinito] [--duree 60] [--graine 1]
Code de sortie 0 si tout est propre, 1 sinon.
"""
import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import uvicorn
import websockets

import casino
import menteur
import picolopoly
import serveur
import telephone
import undercover

# Delais raccourcis : le banc n a pas une heure devant lui.
picolopoly.PP_DELAI_CHOIX = 2
picolopoly.PP_DELAI_TOUR = 2
picolopoly.PP_DELAI_ABSENT = 1
menteur.MT_DELAI_TOUR = 4
menteur.MT_DELAI_CIBLE = 3
menteur.MT_DELAI_ABSENT = 1
menteur.MT_DELAI_REVELATION = 1.0
menteur.MT_DELAI_PAR_TIR = 0.3
undercover.UC_DELAI_LECTURE = 3
undercover.UC_DELAI_INDICE = 3
undercover.UC_DELAI_VOTE = 5
undercover.UC_DELAI_DEVINE = 3
undercover.UC_DELAI_ABSENT = 1
undercover.UC_DELAI_REVELATION = 1.0
undercover.UC_DELAI_RETOUR_VOTE = 3
for _v in telephone.TD_CONFIG["vitesses"].values():
    _v.update(phrase=4, dessin=4, ecriture=4, visionnage=1, speedrun_phrase=3, speedrun_dessin=3,
              speedrun_ecriture=3)
telephone.TD_CONFIG["knockoff"]["minimum"] = 2
telephone.TD_CONFIG["delai_grace"] = 1
telephone.TD_CONFIG["delai_absent"] = 1
telephone.TD_CONFIG["revelation"].update(vote_album=4, vote_final=4, retour_vote=3,
                                         auto_texte=0.5, auto_dessin=0.5, auto_resultat=0.5)
casino.CS_DELAI_MISES = 3
casino.CS_DELAI_TOUR = 3
casino.CS_DELAI_POKER = 3
casino.CS_DELAI_ABSENT = 1
casino.CS_DELAI_ROULETTE = 1.5
casino.CS_DELAI_BJ_FIN = 1.0
casino.CS_DELAI_PAR_CARTE = 0.1
casino.CS_DELAI_POKER_FIN = 1.0
casino.CS_DELAI_POKER_CARTE = 0.1
if hasattr(serveur, "DELAI_SILENCE"):
    serveur.DELAI_SILENCE = 4          # telephone muet => considere parti
if hasattr(serveur, "DELAI_SALON_VIDE"):
    serveur.DELAI_SALON_VIDE = 30

# Trace des rearmements du minuteur de Picopoly (diagnostic des blocages).
TRACE_ARMER = []
_vrai_armer = picolopoly.pp_armer


def _armer_trace(etat, maintenant=None):
    import traceback
    pile = [f.name for f in traceback.extract_stack(limit=6)[:-1]]
    TRACE_ARMER.append((round(time.time(), 2), "<".join(reversed(pile))))
    del TRACE_ARMER[:-15]
    return _vrai_armer(etat, maintenant)


picolopoly.pp_armer = _armer_trace
serveur.pp_armer = _armer_trace

PORT = int(os.environ.get("BANC_PORT", "18765"))
BLOCAGE = 12          # secondes sans aucun progres = blocage

NB_JOUEURS = {"kinito": 4, "beerbattle": 4, "buckshot": 4, "picolopoly": 4, "menteur": 4, "casino": 4, "undercover": 5,
              "telephone": 4}
DEMARRER = {
    "kinito": {"type": "demarrer", "difficulte": "normal"},
    "beerbattle": {"type": "bb_demarrer"},
    "buckshot": {"type": "bt_demarrer"},
    "picolopoly": {"type": "pp_demarrer", "difficulte": "normal"},
    "menteur": {"type": "mt_demarrer", "mode": "chaos"},
    "casino": {"type": "cs_demarrer"},
    "undercover": {"type": "uc_demarrer", "famille": "melange"},
    "telephone": {"type": "td_demarrer", "vitesse": "flash"},
}


class Erreurs(logging.Handler):
    """Recupere toute erreur loguee par le serveur ou uvicorn."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages = []

    def emit(self, record):
        texte = record.getMessage()
        if record.exc_info:
            texte += "\n" + logging.Formatter().formatException(record.exc_info)
        self.messages.append(texte)


class Banc:
    def __init__(self, jeu, graine, battement):
        self.jeu = jeu
        self.rng = random.Random(graine)
        self.battement = battement
        self.code = None
        self.robots = []
        self.dernier_progres = time.time()
        self.signature = None
        self.etat = None
        self.problemes = []
        self.compteurs = {}
        self.doublons_vus = set()
        self.fini = False
        self.progres = 0           # changements d etat observes : la partie avance

    def note(self, cle):
        self.compteurs[cle] = self.compteurs.get(cle, 0) + 1

    def probleme(self, texte):
        if texte not in self.problemes:
            self.problemes.append(texte)
            print("  !!", texte)

    # --- Suivi de l etat de la table, vu par n importe quel robot ---
    def observer(self, msg):
        t = msg.get("type")
        sig = None
        if t == "etat":
            sig = (msg["phase"], msg["joueur_courant"], msg["annonce_precedente"], msg.get("message"))
        elif t == "effet_special":
            sig = ("effet", time.time())
        elif t == "fin_manche":
            sig = ("fin", time.time())
        elif t == "annonce":
            sig = ("annonce", msg.get("annonceur"), time.time())
        elif t == "bb_etat":
            sig = (msg["phase"], msg["joueur_courant"], msg["joueur_en_placement"],
                   msg["action_faite"], tuple(msg["verres"]))
        elif t == "bb_victoire":
            sig = ("victoire", time.time())
        elif t == "bt_etat":
            sig = (msg["phase"], msg["courant"], msg["chargeur"], msg["pile_reste"], tuple(msg["gorgees"]))
        elif t == "pp_etat":
            sig = (msg["phase"], msg["courant"], msg["a_lance"], json.dumps(msg["attente"]),
                   tuple(msg["positions"]), tuple(msg["argent"]))
        elif t == "mt_etat":
            sig = (msg["phase"], msg["courant"], msg["manche"], msg["pile"], tuple(msg["tirs"]),
                   json.dumps(msg["cibles"]))
        elif t == "uc_etat":
            sig = (msg["phase"], msg["tour"], msg["parole"], tuple(msg["a_vote"]), tuple(msg["vivants"]),
                   tuple(msg["prets"]))
        elif t == "td_etat":
            sig = (msg["phase"], msg["etape"], msg["version"])
        elif t == "cs_etat":
            sig = (msg["phase"], msg["jeu"], msg["manche"], json.dumps(msg["table"]), tuple(msg["jetons"]))
        if sig is not None and sig != self.signature:
            self.signature = sig
            self.progres += 1
            self.dernier_progres = time.time()
        if t in ("etat", "bb_etat", "bt_etat", "pp_etat", "mt_etat", "cs_etat", "uc_etat", "td_etat"):
            self.etat = msg
            self.verifier_doublons(msg)

    def verifier_doublons(self, msg):
        noms = msg.get("joueurs") or []
        partis = msg.get("partis") or [False] * len(noms)
        vus = {}
        for i, n in enumerate(noms):
            vus.setdefault(n, []).append(i)
        for n, places in vus.items():
            if len(places) > 1:
                cle = (n, tuple(places))
                if cle not in self.doublons_vus:
                    self.doublons_vus.add(cle)
                    etats = ["parti" if (i < len(partis) and partis[i]) else "présent" for i in places]
                    self.probleme(f"doublon : « {n} » occupe les places {places} ({', '.join(etats)})")

    def presents_eveilles(self):
        return [r for r in self.robots if r.connecte and r.eveille and not r.definitif]


class Robot:
    def __init__(self, banc, nom):
        self.banc = banc
        self.nom = nom
        self.jeton = None
        self.ws = None
        self.index = None
        self.connecte = False
        self.eveille = True
        self.definitif = False
        self.main = []
        self.prev = (True, 0)     # kinito : (premiere annonce, annonce a battre)
        self.taches = set()
        self.verrou = asyncio.Lock()

    # --- Connexion ---
    async def connecter(self, avec_jeton=True):
        url = f"ws://127.0.0.1:{PORT}/ws/{self.banc.code}/{self.nom}"
        params = []
        if avec_jeton and self.jeton:
            params.append("jeton=" + self.jeton)
        if self.banc.battement:
            params.append("bat=1")
        if params:
            url += "?" + "&".join(params)
        ws = await websockets.connect(url, open_timeout=10, ping_interval=None)
        self.ws = ws
        self.connecte = True
        self.lancer(self.lire(ws))
        if self.banc.battement:
            self.lancer(self.battre(ws))

    async def lire(self, ws):
        try:
            async for brut in ws:
                if ws is not self.ws:
                    continue          # vieille connexion : on n en tient plus compte
                msg = json.loads(brut)
                self.banc.observer(msg)
                if self.eveille:
                    self.recevoir(msg)
        except Exception:
            pass
        if ws is self.ws:
            self.connecte = False
            # Comme le client corrige : il se reconnecte tout seul.
            if not self.definitif and self.eveille and not self.banc.fini:
                await asyncio.sleep(0.5)
                if ws is self.ws and not self.connecte:
                    self.banc.note("reconnexion auto")
                    try:
                        await self.connecter()
                    except Exception:
                        pass

    async def battre(self, ws):
        while ws is self.ws and not self.banc.fini:
            await asyncio.sleep(1)
            if self.eveille and ws is self.ws:
                await self.envoyer({"type": "ping"}, ws)

    def lancer(self, coro):
        t = asyncio.ensure_future(coro)
        self.taches.add(t)
        t.add_done_callback(self.taches.discard)

    async def envoyer(self, msg, ws=None):
        ws = ws or self.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(msg))
        except Exception:
            pass

    def plus_tard(self, msg, mini=0.05, maxi=0.4):
        async def f():
            await asyncio.sleep(self.banc.rng.uniform(mini, maxi))
            if self.eveille and self.connecte:
                await self.envoyer(msg)
        self.lancer(f())

    # --- Reactions, comme un joueur attentif ---
    def recevoir(self, msg):
        t = msg.get("type")
        if t == "ton_index":
            self.index = msg["index"]
            if msg.get("jeton"):
                self.jeton = msg["jeton"]
        elif t == "uc_mot":
            self.index = msg["index"]
            self.mot = msg["mot"]
        elif t == "mt_main":
            self.index = msg["index"]
            self.main = msg["main"]
        elif t == "bt_mon_index":
            self.index = msg["index"]
        elif t == "bb_ta_main":
            self.index = msg["mon_index"]
            self.main = msg["main"]
        jeu = self.banc.jeu
        getattr(self, "jouer_" + jeu)(msg)

    def jouer_kinito(self, msg):
        t = msg.get("type")
        etat = msg.get("etat") if t == "effet_special" else (msg if t == "etat" else None)
        if etat:
            self.prev = (etat["premiere_annonce"], etat["annonce_precedente"])
            if etat["phase"] == "lancer" and etat["joueur_courant"] == self.index:
                self.plus_tard({"type": "lancer"})
        elif t == "ton_score":
            premiere, prec = msg["premiere_annonce"], self.prev[1]
            possibles = msg["scores_possibles"]
            score = msg["score"]
            if premiere:
                choix = score if score in possibles else self.banc.rng.choice(possibles)
            else:
                plus_forts = [s for s in possibles if serveur.rang(s) >= serveur.rang(prec)]
                if score in plus_forts:
                    choix = score
                elif plus_forts and self.banc.rng.random() < 0.8:
                    choix = self.banc.rng.choice(plus_forts)
                else:
                    self.plus_tard({"type": "abandonner"})
                    return
            self.plus_tard({"type": "annoncer", "score": choix})
        elif t == "a_toi_de_reagir":
            choix = "prends" if self.banc.rng.random() < 0.6 else "menteur"
            self.plus_tard({"type": "reaction", "choix": choix})
        elif t == "fin_manche":
            self.plus_tard({"type": "nouvelle_manche"}, 0.3, 1.2)

    def jouer_beerbattle(self, msg):
        t = msg.get("type")
        if t == "bb_etat":
            i = self.index
            if i is None or i >= len(msg["elimine"]) or msg["elimine"][i]:
                return
            if msg["phase"] == "placement" and msg["joueur_en_placement"] == i:
                prises = {(p["ligne"], p["colonne"]) for p in msg["positions"] if p}
                t = msg.get("taille", 6)
                libres = [(l, c) for l in range(t) for c in range(t) if (l, c) not in prises]
                l, c = self.banc.rng.choice(libres)
                self.plus_tard({"type": "bb_placer", "ligne": l, "colonne": c})
            elif msg["phase"] == "jeu" and msg["joueur_courant"] == i:
                if not msg["action_faite"]:
                    autres = [k for k in range(len(msg["elimine"])) if k != i and not msg["elimine"][k]]
                    if autres and self.banc.rng.random() < 0.6:
                        self.plus_tard({"type": "bb_attaquer", "cible": self.banc.rng.choice(autres),
                                        "index_carte": None, "type_attaque": "cc_mains_nues"})
                    else:
                        self.plus_tard({"type": "bb_ne_rien_faire"})
                else:
                    self.plus_tard({"type": "bb_ramasser", "decision": "prendre", "index_echange": None})
        elif t == "bb_victoire":
            self.plus_tard(DEMARRER["beerbattle"], 0.5, 1.5)

    def jouer_buckshot(self, msg):
        if msg.get("type") != "bt_etat" or self.index is None:
            return
        i = self.index
        if i >= len(msg["gorgees"]):
            return
        if msg["phase"] == "composition" and msg["chargeur"] == i:
            self.plus_tard({"type": "bt_composer", "total": 5, "reel": 2})
        elif msg["phase"] == "objets" and msg["courant"] == i:
            o = msg["objets"][i]
            if o and not o["utilise"] and self.banc.rng.random() < 0.3:
                self.plus_tard({"type": "bt_jouer_objet", "cible": (i + 1) % len(msg["gorgees"])}, 0.02, 0.1)
            self.plus_tard({"type": "bt_tirer", "cible": self.banc.rng.randrange(len(msg["gorgees"]))}, 0.2, 0.5)

    def jouer_picolopoly(self, msg):
        if msg.get("type") != "pp_etat" or self.index is None:
            return
        if os.environ.get("BANC_PP_PASSIF"):
            return          # tout repose sur le minuteur du serveur
        i = self.index
        if msg["phase"] == "fin":
            self.plus_tard(DEMARRER["picolopoly"], 0.5, 1.5)
            return
        a = msg["attente"]
        if a is not None:
            if a["joueur"] == i:
                self.plus_tard({"type": "pp_choix", "valeur": picolopoly._valeur_defaut({}, a)})
            return
        if msg["dette"] is not None:
            return          # le minuteur liquide la dette
        if msg["courant"] != i:
            return
        if msg["phase"] == "tour":          # a_lance vrai = double, on relance
            self.plus_tard({"type": "pp_lancer"})
        elif msg["phase"] == "fin_tour":
            self.plus_tard({"type": "pp_terminer"})

    def jouer_menteur(self, msg):
        if msg.get("type") != "mt_etat" or self.index is None:
            return
        i, rng = self.index, self.banc.rng
        if i >= len(msg["vivants"]):
            return
        if msg["phase"] == "fin":
            self.plus_tard(DEMARRER["menteur"], 0.5, 1.5)
        elif msg["phase"] == "jeu" and msg["courant"] == i:
            nb = msg["nb_cartes"][i]
            if msg["pose"] is not None and (nb == 0 or rng.random() < 0.35):
                self.plus_tard({"type": "mt_menteur"})
            elif nb:
                k = rng.randrange(nb)
                self.plus_tard({"type": "mt_poser", "cartes": [k]})
        elif msg["phase"] == "cible" and msg["cibles"]:
            c = msg["cibles"]
            if i in c["tireurs"] and i not in c["choisis"]:
                autres = [j for j, v in enumerate(msg["vivants"]) if v and j != i]
                if autres:
                    self.plus_tard({"type": "mt_cible", "cible": rng.choice(autres)})

    def jouer_undercover(self, msg):
        if msg.get("type") != "uc_etat" or self.index is None:
            return
        i, rng = self.index, self.banc.rng
        if i >= len(msg["vivants"]):
            return
        ph = msg["phase"]
        if ph == "fin":
            self.plus_tard(DEMARRER["undercover"], 0.5, 1.5)
        elif ph == "lecture" and not msg["prets"][i]:
            self.plus_tard({"type": "uc_pret"})
        elif ph == "indices" and msg["orateur"] == i:
            self.plus_tard({"type": "uc_fini"})
        elif ph == "vote" and msg["vivants"][i] and not msg["a_vote"][i]:
            cands = msg["departage"] or [j for j, v in enumerate(msg["vivants"]) if v]
            cands = [j for j in cands if j != i]
            if cands:
                self.plus_tard({"type": "uc_voter", "cible": rng.choice(cands)})
        elif ph == "mrwhite" and msg["elimination"] and msg["elimination"]["joueur"] == i:
            self.plus_tard({"type": "uc_deviner", "mot": rng.choice(["Café", "Bière", "truc"])})

    def jouer_telephone(self, msg):
        if self.index is None:
            return
        i, rng = self.index, self.banc.rng
        if msg.get("type") == "td_tache" and not msg["envoye"]:
            if msg["nature"] == "dessin":
                contenu = [{"c": rng.randrange(9), "e": rng.randrange(3), "p": "AKAKAUAU"}]
            else:
                contenu = "texte %d" % rng.randrange(1000)
            if rng.random() < 0.8:
                self.plus_tard({"type": "td_contribuer", "contenu": contenu})
            return
        if msg.get("type") != "td_etat" or i >= len(msg["partis"]):
            return
        ph = msg["phase"]
        if ph == "fin":
            mode = rng.choice(["normal", "knockoff", "speedrun"])
            self.plus_tard(dict(DEMARRER["telephone"], mode=mode), 0.5, 1.5)
        elif ph == "pret" and msg["hote"] == i:
            self.plus_tard({"type": "td_reveler", "auto": rng.random() < 0.5})
        elif ph in ("revelation", "resultat_album") and msg["hote"] == i:
            self.plus_tard({"type": "td_suivant"})
        elif ph == "vote_album" and not msg["a_vote"][i]:
            cands = [c["index"] for c in msg["revelation"]["contributions"]
                     if c["auteur"] is not None and c["auteur"] != i]
            if cands:
                self.plus_tard({"type": "td_voter", "cible": rng.choice(cands)})
        elif ph == "vote_final" and not msg["a_vote"][i]:
            if msg["candidats"]:
                a, k = rng.choice(msg["candidats"])
                self.plus_tard({"type": "td_voter", "album": a, "cible": k})
        if ph in ("revelation", "vote_album") and rng.random() < 0.2:
            self.plus_tard({"type": "td_reaction", "emoji": "😂"})

    def jouer_casino(self, msg):
        if msg.get("type") != "cs_etat" or self.index is None:
            return
        i, rng = self.index, self.banc.rng
        if i >= len(msg["jetons"]):
            return
        phase, jeu, t = msg["phase"], msg["jeu"], msg["table"] or {}
        if phase == "hall":
            self.plus_tard({"type": "cs_choisir", "jeu": rng.choice(["roulette", "blackjack", "poker"])}, 0.2, 1)
        elif phase == "mises":
            if rng.random() < 0.05:
                self.plus_tard({"type": "cs_hall"})
            elif not t.get("prets", [False] * 8)[i]:
                if jeu == "roulette":
                    self.plus_tard({"type": "cs_rl_miser", "pari": rng.choice(["rouge", "pair", "n7"]),
                                    "montant": 10})
                else:
                    self.plus_tard({"type": "cs_bj_miser", "montant": 10})
                self.plus_tard({"type": "cs_pret"}, 0.4, 0.8)
        elif phase == "jeu" and t.get("courant") == i:
            if jeu == "blackjack":
                self.plus_tard({"type": rng.choice(["cs_bj_tirer", "cs_bj_rester", "cs_bj_doubler"])})
            elif jeu == "poker":
                self.plus_tard({"type": rng.choice(["cs_pk_suivre"] * 4 + ["cs_pk_coucher"]),})


# =============================================================
# CHAOS : les facons de quitter et revenir
# =============================================================

async def chaos(banc, robot, sorte):
    rng = banc.rng
    banc.note(sorte)
    if sorte == "coupure":
        ws = robot.ws
        robot.ws = None
        robot.connecte = False
        try:
            await ws.close()
        except Exception:
            pass
        await asyncio.sleep(rng.uniform(0.3, 3))
        await robot.connecter()
    elif sorte == "reseau":
        ws = robot.ws
        robot.ws = None
        robot.connecte = False
        try:
            ws.transport.abort()
        except Exception:
            pass
        await asyncio.sleep(rng.uniform(0.3, 3))
        await robot.connecter()
    elif sorte == "fantome":
        # L ancienne connexion reste ouverte : on la laisse pendre.
        robot.ws = None
        robot.connecte = False
        await robot.connecter()
    elif sorte == "sans_jeton":
        ws = robot.ws
        robot.ws = None
        robot.connecte = False
        try:
            await ws.close()
        except Exception:
            pass
        robot.jeton = None
        await asyncio.sleep(rng.uniform(0.3, 2))
        await robot.connecter(avec_jeton=False)
    elif sorte == "fantome_sans_jeton":
        # Autre navigateur pendant que l ancien onglet tient encore la place.
        robot.ws = None
        robot.connecte = False
        robot.jeton = None
        await robot.connecter(avec_jeton=False)
    elif sorte == "veille":
        robot.eveille = False
        await asyncio.sleep(rng.uniform(3, 9))
        robot.eveille = True
        # Au reveil le client verifie sa connexion et se reconnecte au besoin.
        if robot.ws is None or robot.ws.state != websockets.protocol.State.OPEN or not robot.connecte:
            robot.connecte = False
            await robot.connecter()
        else:
            # Connexion toujours ouverte : il a pu manquer des messages
            # pendant son sommeil, il demande un instantane (comme le client).
            await robot.envoyer({"type": "resync"})
    elif sorte == "definitif":
        robot.definitif = True
        ws = robot.ws
        robot.ws = None
        robot.connecte = False
        try:
            await ws.close()
        except Exception:
            pass


async def surveiller(banc, duree):
    debut = time.time()
    while time.time() - debut < duree:
        await asyncio.sleep(0.5)
        calme = time.time() - banc.dernier_progres
        if calme > BLOCAGE and len(banc.presents_eveilles()) >= 2:
            e = banc.etat or {}
            resume = {k: e.get(k) for k in ("type", "phase", "joueur_courant", "courant", "chargeur",
                                             "joueur_en_placement", "action_faite", "attente", "partis")
                      if k in e}
            qui = [(r.nom, r.index, "co" if r.connecte else "déco", "éveillé" if r.eveille else "veille")
                   for r in banc.robots]
            interne = ""
            salon = serveur.salons.get(banc.code)
            if salon is not None:
                m = salon.get("pp_minuteur")
                v = salon.get("verrou")
                et = salon.get("etat") or {}
                interne = (f" ; serveur : verrou={'pris' if v and v.locked() else 'libre'}"
                           f" minuteur={'aucun' if m is None else ('fini' if m.done() else 'actif')}"
                           f" echeance={round(et['echeance'] - time.time(), 1) if et.get('echeance') else None}"
                           f" joueurs={[(j['nom'], j['index'], 'parti' if j.get('parti') else 'là') for j in salon['joueurs']]}")
                if m is not None and m.done() and not m.cancelled() and m.exception():
                    interne += f" exception_minuteur={m.exception()!r}"
            if banc.jeu == "picolopoly":
                t0 = time.time()
                interne += " ; rearmements=" + str([(round(t - t0, 1), q) for t, q in TRACE_ARMER[-8:]])
            banc.probleme(f"blocage : rien ne bouge depuis {int(calme)} s ; état {resume} ; robots {qui}{interne}")
            banc.dernier_progres = time.time()


async def scenario(jeu, duree, graine, battement, sortes):
    banc = Banc(jeu, graine, battement)
    erreurs = Erreurs()
    config = uvicorn.Config(serveur.app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off")
    srv = uvicorn.Server(config)
    t_srv = asyncio.ensure_future(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    # Apres le demarrage : uvicorn reconfigure ses journaux en demarrant.
    for nom in ("uvicorn.error", "uvicorn", "serveur", "asyncio"):
        logging.getLogger(nom).addHandler(erreurs)

    serveur.salons.clear()
    banc.code = serveur.generer_code()
    serveur.salons[banc.code] = {"joueurs": [], "etat": None, "jeu": None}

    nb = int(os.environ.get("BANC_NB", NB_JOUEURS[jeu]))
    noms = ["Alice", "Bruno", "Chloe", "Dylan", "Emma", "Farid", "Gaelle", "Hugo"][:nb]
    for n in noms:
        r = Robot(banc, n)
        banc.robots.append(r)
        await r.connecter()
        await asyncio.sleep(0.1)

    # Un peu de chaos dans la salle d attente aussi.
    await asyncio.sleep(0.5)
    for r in banc.robots[1:]:
        if banc.rng.random() < 0.5:
            await chaos(banc, r, banc.rng.choice(["coupure", "fantome", "sans_jeton", "reseau"]))
    await asyncio.sleep(1)

    hote = banc.robots[0]
    await hote.envoyer(DEMARRER[jeu])
    banc.dernier_progres = time.time()

    async def semer():
        while not banc.fini:
            await asyncio.sleep(banc.rng.uniform(1.0, 2.5))
            candidats = [r for r in banc.robots if r.connecte and r.eveille and not r.definitif]
            if len(candidats) <= 2:
                continue
            r = banc.rng.choice(candidats)
            sorte = banc.rng.choice(sortes)
            if sorte == "definitif" and sum(1 for x in banc.robots if x.definitif) >= 1:
                sorte = "coupure"
            try:
                await chaos(banc, r, sorte)
            except Exception as e:
                banc.probleme(f"le robot {r.nom} n'a pas pu revenir ({sorte}) : {e!r}")

    t_chaos = asyncio.ensure_future(semer())
    await surveiller(banc, duree)
    banc.fini = True
    t_chaos.cancel()
    # Fin : tout le monde revient, la partie doit encore avancer.
    for r in banc.robots:
        r.eveille = True
    await asyncio.sleep(1.5)
    banc.dernier_progres = time.time()
    fin = time.time()
    while time.time() - fin < 6:
        await asyncio.sleep(0.5)
    if time.time() - banc.dernier_progres > 5.5 and len(banc.presents_eveilles()) >= 2:
        banc.probleme("blocage en fin de test : plus rien ne bouge une fois tout le monde revenu")

    for r in banc.robots:
        for t in list(r.taches):
            t.cancel()
        if r.ws is not None:
            try:
                await r.ws.close()
            except Exception:
                pass
    srv.should_exit = True
    await t_srv
    for m in erreurs.messages:
        banc.probleme("erreur serveur : " + m.strip().splitlines()[-1] + "\n" + m[:1500])
    return banc


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--jeu", choices=list(NB_JOUEURS) + ["tous"], default="tous")
    p.add_argument("--duree", type=float, default=40)
    p.add_argument("--graine", type=int, default=1)
    p.add_argument("--sans-battement", action="store_true",
                   help="robots sans battement de coeur (ancien client)")
    p.add_argument("--sortes", default="coupure,reseau,fantome,sans_jeton,fantome_sans_jeton,veille,definitif")
    a = p.parse_args()
    jeux = list(NB_JOUEURS) if a.jeu == "tous" else [a.jeu]
    sortes = a.sortes.split(",")
    total = 0
    for jeu in jeux:
        print(f"== {jeu} (graine {a.graine}, {a.duree:.0f} s)")
        banc = asyncio.run(scenario(jeu, a.duree, a.graine, not a.sans_battement, sortes))
        print("   chaos :", dict(sorted(banc.compteurs.items())), "; etats successifs :", banc.progres)
        if banc.progres < 10:
            banc.probleme("la partie n'a presque pas avance (" + str(banc.progres) + " etats)")
        if banc.problemes:
            print(f"   {len(banc.problemes)} probleme(s)")
            total += len(banc.problemes)
        else:
            print("   OK")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
