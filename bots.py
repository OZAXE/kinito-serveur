"""
Cerveaux des bots (mode test du pseudo Admin01).

Un bot est un joueur de la table que le serveur fait jouer tout seul (voir
« BOTS » dans serveur.py : place a table, boucle de reflexion, verrou). Ici,
seulement les decisions : un cerveau par jeu, PUR et sans reseau.

    cerveau(etat, i, mem) -> message a envoyer au serveur, ou None

  - etat : l etat du jeu (salon["etat"]). Le cerveau n y lit que ce que le
    joueur i voit a l ecran : l etat public, et ses propres secrets (sa main,
    ses des). Exception assumee : au Juste Pinte, le bot « connait » la bonne
    reponse a un bruit pres, pour simuler un joueur plus ou moins cultive.
  - i : l index du bot.
  - mem : un dict propre au bot, qui dure toute sa vie a la table. mem["vus"]
    contient les messages recus depuis la derniere reflexion (ses messages
    prives : un regard au Buckshot...), ranges par observer() s il existe.
  - le message peut porter "_delai" (secondes de « reflexion » avant d agir),
    retire avant l envoi.

Le cerveau est appele deux fois : pour savoir s il y a quelque chose a faire,
puis de nouveau sous le verrou du salon juste avant d agir (l etat a pu
changer pendant la reflexion). Il doit donc rendre None quand ce n est pas a
lui, et ne pas compter sur un seul appel.
"""
import math
import random

VITESSE = 1.0       # multiplie les temps de reflexion (les essais accelerent)
CERVEAUX = {}       # jeu -> cerveau(etat, i, mem)
OBSERVATEURS = {}   # jeu -> observer(mem, message) : appele a chaque message recu

# Prenoms des bots (« Bot Léa »...) : courts, faciles a distinguer.
PRENOMS = ["Léa", "Hugo", "Inès", "Tom", "Jade", "Noé", "Lina", "Max", "Zoé", "Sacha",
           "Emma", "Lou", "Nina", "Paul", "Rose", "Ugo", "Alix", "Théo", "Mia", "Léo"]


def jeu(nom, observer=None):
    def enregistrer(f):
        CERVEAUX[nom] = f
        if observer is not None:
            OBSERVATEURS[nom] = observer
        return f
    return enregistrer


def lent(message, mini=1.0, maxi=2.5):
    """Le message, avec un temps de reflexion tire au hasard."""
    message["_delai"] = random.uniform(mini, maxi) * VITESSE
    return message


def _presents(etat, sauf=None):
    partis = etat.get("partis") or [False] * etat["nb_joueurs"]
    return [j for j in range(etat["nb_joueurs"]) if not partis[j] and j != sauf]


# =============================================================
# KINITO (bluff aux des)
# =============================================================
KI_ECHELLE = [31, 32, 41, 42, 43, 52, 53, 54, 61, 62, 63, 64, 65, 11, 22, 33, 44, 55, 66, 21]
KI_ANNONCABLES = [s for s in KI_ECHELLE if s != 31]


def _ki_rang(s):
    return KI_ECHELLE.index(s) if s in KI_ECHELLE else -1


def _ki_proba_au_moins():
    """P(un lancer normal fait au moins tel score), 31 et 51 exclus (ils se jouent seuls)."""
    tirages = []
    for a in range(1, 7):
        for b in range(1, 7):
            s = max(a, b) * 10 + min(a, b)
            if s not in (31, 51):
                tirages.append(_ki_rang(s))
    return {s: sum(1 for r in tirages if r >= _ki_rang(s)) / len(tirages) for s in KI_ANNONCABLES}


KI_PROBA = _ki_proba_au_moins()


def _ki_suivant(etat, index):
    nb = etat["nb_joueurs"]
    partis = etat.get("partis") or [False] * nb
    for k in range(1, nb + 1):
        j = (index + etat["sens"] * k) % nb
        if not partis[j]:
            return j
    return (index + etat["sens"]) % nb


@jeu("kinito")
def cerveau_kinito(etat, i, mem):
    phase = etat.get("phase")
    prec = etat.get("annonce_precedente") or 0
    premiere = etat.get("premiere_annonce", True)
    if phase == "lancer" and etat["joueur_courant"] == i:
        # Score a battre tres haut : parfois on assume tout de suite.
        if not premiere and KI_PROBA.get(prec, 1) < 0.12 and random.random() < 0.5:
            return lent({"type": "abandonner"})
        return lent({"type": "lancer"}, 0.8, 1.8)
    if phase == "annonce" and etat["joueur_courant"] == i:
        reel = etat.get("score_reel")
        if reel not in KI_ANNONCABLES:
            return None
        if premiere or _ki_rang(reel) > _ki_rang(prec):
            # On dit vrai, sauf un petit bluff de temps en temps.
            if random.random() < 0.15:
                plus = [s for s in KI_ANNONCABLES if _ki_rang(s) > _ki_rang(reel)][:3]
                if plus:
                    return lent({"type": "annoncer", "score": random.choice(plus)}, 1.5, 3.5)
            return lent({"type": "annoncer", "score": reel}, 1.2, 3.0)
        # Trop faible : on bluffe juste au-dessus, ou on assume.
        plus = [s for s in KI_ANNONCABLES if _ki_rang(s) > _ki_rang(prec)]
        if not plus or random.random() < 0.2:
            return lent({"type": "abandonner"}, 1.5, 3.0)
        return lent({"type": "annoncer", "score": random.choice(plus[:2])}, 1.5, 3.5)
    if phase == "reaction" and _ki_suivant(etat, etat["joueur_courant"]) == i:
        annonce = etat.get("score_annonce")
        if not premiere and _ki_rang(annonce) < _ki_rang(prec):
            return lent({"type": "reaction", "choix": "moins", "score_cite": prec}, 1.5, 3.0)
        # Plus l annonce est rare, plus on doute.
        doute = (1 - KI_PROBA.get(annonce, 1)) * (0.55 if premiere else 0.8)
        choix = "menteur" if random.random() < doute else "prends"
        return lent({"type": "reaction", "choix": choix}, 1.5, 3.5)
    return None


# =============================================================
# CAL 12 (menteur aux cartes + roulette)
# =============================================================
@jeu("menteur")
def cerveau_menteur(etat, i, mem):
    if i >= etat["nb_joueurs"] or not etat["vivants"][i]:
        return None
    phase = etat["phase"]
    if phase == "cible":
        choix = (etat.get("cibles") or {}).get("choix", {})
        if str(i) in choix and choix[str(i)] is None:
            autres = [j for j in range(etat["nb_joueurs"]) if etat["vivants"][j] and j != i]
            if autres:
                # On vise celui qui a le plus de chances de tomber (barillet avance).
                autres.sort(key=lambda j: -etat["tirs"][j] + random.random() * 2)
                return lent({"type": "mt_cible", "cible": autres[0]}, 1.5, 4.0)
        return None
    if phase != "jeu" or etat["courant"] != i:
        return None
    main = etat["mains"][i]
    table = etat["carte_table"]
    pose = etat.get("pose")
    if pose is not None and pose["joueur"] != i:
        nb = len(pose["cartes"])
        if not main:
            return lent({"type": "mt_menteur"}, 1.5, 3.0)
        # Je connais mes cartes : moins il en reste dehors, plus je doute.
        vraies_chez_moi = sum(1 for c in main if c in (table, "J"))
        doute = 0.18 + 0.12 * (nb - 1) + 0.08 * vraies_chez_moi
        if random.random() < min(0.75, doute):
            return lent({"type": "mt_menteur"}, 1.5, 3.5)
    if not main:
        return None
    vraies = [k for k, c in enumerate(main) if c in (table, "J")]
    speciales = [k for k, c in enumerate(main) if c in ("X", "C", "M")]
    if speciales and random.random() < 0.35:
        cartes = [speciales[0]]
    elif vraies:
        nb = min(len(vraies), random.choice([1, 1, 2, 2, 3]))
        cartes = vraies[:nb]
        # Parfois on glisse une fausse avec les vraies.
        fausses = [k for k, c in enumerate(main) if k not in vraies and c != "X"]
        if fausses and len(cartes) < 3 and random.random() < 0.25:
            cartes.append(random.choice(fausses))
    else:
        fausses = [k for k, c in enumerate(main) if c != "X"] or [0]
        cartes = random.sample(fausses, min(len(fausses), random.choice([1, 1, 2])))
    return lent({"type": "mt_poser", "cartes": cartes}, 1.5, 4.0)


# =============================================================
# BUCKSHOT (roulette aux cartouches)
# =============================================================
def _bt_observer(mem, msg):
    t = msg.get("type")
    if t == "bt_regard":
        mem.setdefault("bt_connues", {})
        reste = mem.get("bt_reste", 0)
        # Position absolue depuis le bas de la pile : stable quand le dessus part.
        mem["bt_connues"][reste - 1 - msg["position"]] = msg["nature"]
    elif t == "bt_tir":
        mem["bt_reels_tires"] = mem.get("bt_reels_tires", 0) + (1 if msg.get("nature") == "reel" else 0)
    elif t == "bt_etat":
        if msg.get("phase") == "composition":
            mem["bt_connues"] = {}
            mem["bt_reels_tires"] = 0
        mem["bt_reste"] = msg.get("pile_reste", 0)


@jeu("buckshot", observer=_bt_observer)
def cerveau_buckshot(etat, i, mem):
    n = etat["nb_joueurs"]
    if i >= n:
        return None
    if etat["phase"] == "composition" and etat["chargeur"] == i:
        mini = max(4, n)
        maxi = max(8, 2 * n)
        total = random.randint(mini, maxi)
        reel = max(1, round(total * random.uniform(0.3, 0.6)))
        return lent({"type": "bt_composer", "total": total, "reel": reel}, 1.5, 3.0)
    if etat["phase"] != "objets" or etat["courant"] != i:
        return None
    pile = len(etat["pile"])
    if pile == 0:
        return None
    haut = pile - 1
    connue = mem.get("bt_connues", {}).get(haut)
    reels = max(0, etat.get("annonce_reel", 0) - mem.get("bt_reels_tires", 0))
    p_reel = 1.0 if connue == "reel" else 0.0 if connue == "blanc" else min(1.0, reels / pile)
    autres = [j for j in _presents(etat, sauf=i)]
    o = etat["objets"][i]
    if o and not o["utilise"]:
        oid = o["id"]
        if oid in ("loupe", "telephone") and connue is None:
            return lent({"type": "bt_jouer_objet"})
        if oid == "inverseur" and connue == "reel":
            mem["bt_connues"][haut] = "blanc"
            return lent({"type": "bt_jouer_objet"})
        if oid == "biere" and p_reel > 0.6 and pile > 1:
            mem.get("bt_connues", {}).pop(haut, None)
            return lent({"type": "bt_jouer_objet"})
        if oid == "scie" and p_reel >= 0.5 and not etat["scie_active"]:
            return lent({"type": "bt_jouer_objet"})
        if oid == "menottes" and autres:
            suivant = next((j for j in [(i + k) % n for k in range(1, n)] if j in autres), autres[0])
            return lent({"type": "bt_jouer_objet", "cible": suivant})
    if connue == "blanc" or (connue is None and p_reel < 0.45 and random.random() < 0.8):
        cible = i                  # se servir un blanc : on rejoue
    else:
        cible = random.choice(autres) if autres else i
    return lent({"type": "bt_tirer", "cible": cible}, 1.5, 3.0)


# =============================================================
# LE JUSTE PINTE (estimation)
# =============================================================
@jeu("justepinte")
def cerveau_justepinte(etat, i, mem):
    if i >= etat["nb_joueurs"]:
        return None
    phase = etat["phase"]
    if phase == "question" and str(i) not in etat["reponses"]:
        _, _, reponse, unite, _, _ = etat["tirage"][etat["manche"]]
        if unite == "année":
            valeur = round(reponse + random.gauss(0, 15))
        else:
            # Erreur multiplicative : un joueur plus ou moins cultive.
            valeur = reponse * math.exp(random.gauss(0, 0.35))
            if float(reponse).is_integer():
                valeur = round(valeur)
            else:
                valeur = round(valeur, 1)
        return lent({"type": "jp_repondre", "valeur": valeur}, 4.0, 12.0)
    if phase == "revelation" and not etat["suivants"][i]:
        return lent({"type": "jp_suivant"}, 4.0, 8.0)
    return None


# =============================================================
# QUI DE NOUS ? (vote social)
# =============================================================
@jeu("quidenous")
def cerveau_quidenous(etat, i, mem):
    if i >= etat["nb_joueurs"]:
        return None
    phase = etat["phase"]
    if phase == "vote" and str(i) not in etat["votes"]:
        autres = _presents(etat, sauf=i)
        if not autres:
            return None
        cible = random.choice(autres)
        # Pari : souvent sur le plus designe jusqu ici, sinon sur sa propre cible.
        desig = etat.get("designations") or [0] * etat["nb_joueurs"]
        tous = _presents(etat)
        favori = max(tous, key=lambda j: (desig[j] if j < len(desig) else 0, random.random()))
        pari = favori if random.random() < 0.5 else cible
        return lent({"type": "qn_voter", "cible": cible, "pari": pari}, 3.0, 9.0)
    if phase == "revelation" and not etat["suivants"][i]:
        return lent({"type": "qn_suivant"}, 4.0, 8.0)
    return None


# =============================================================
# LOUP-GAROU (roles caches)
# Le bot ne lit que sa vue secrete (lg_vue, la meme que sa page) et l etat
# public. Villageois : il vote contre les loups qu il connait (visions de la
# Voyante), sinon au hasard ; loup : il devore et vote contre le village.
# =============================================================
LG_ROLES_LOUPS = ("loup", "infect", "loup_blanc")


def _lg_suspects(etat, i, vue):
    """Les autres vivants, sans ses allies connus (meute, amoureux, famille)."""
    amis = set(vue.get("loups") or []) if i in (vue.get("loups") or []) else set()
    amis |= set(vue.get("amoureux") or [])
    amis |= set(vue.get("famille") or [])
    amis |= set(vue.get("vampires") or []) if etat["vampires"][i] else set()
    autres = [j for j in range(etat["nb_joueurs"]) if etat["vivants"][j] and j != i]
    return [j for j in autres if j not in amis] or autres


def _lg_loups_vus(vue):
    return [v["cible"] for v in (vue.get("visions") or []) if v.get("role") in LG_ROLES_LOUPS]


@jeu("loupgarou")
def cerveau_loupgarou(etat, i, mem):
    from loupgarou import lg_vue
    if i >= etat["nb_joueurs"]:
        return None
    phase = etat["phase"]
    vue = lg_vue(etat, i)
    vivant = etat["vivants"][i]
    n = etat["nb_joueurs"]
    vivants = [j for j in range(n) if etat["vivants"][j]]
    if phase == "role":
        if vivant and not etat["prets"][i]:
            m = {"type": "lg_pret"}
            if vue["role"] == "chien_loup":
                m["camp"] = random.choice(["loups", "village"])
            return lent(m, 3.0, 7.0)
        return None
    if phase in ("aube", "verdict"):
        if not etat["suivants"][i]:
            return lent({"type": "lg_suivant"}, 4.0, 9.0)
        return None
    if not vivant:
        return None
    suspects = _lg_suspects(etat, i, vue)
    if phase == "nuit":
        genre = vue["type_nuit"]
        if genre == "meute":
            loups = set(vue.get("loups") or [])
            proies = [j for j in vivants if j not in loups]
            if not proies:
                return None
            # La meute s accorde : on suit le choix deja le plus repandu.
            choix = [c for k, c in (vue.get("meute") or {}).items() if int(k) != i and c in proies]
            mien = (vue.get("meute") or {}).get(str(i))
            if choix:
                cible = max(set(choix), key=choix.count)
            elif mien in proies:
                return None
            else:
                cible = random.choice(proies)
            if mien == cible:
                return None
            return lent({"type": "lg_nuit", "cible": cible}, 3.0, 8.0)
        if etat["agi"][i]:
            return None
        autres = [j for j in vivants if j != i]
        if not autres:
            return None
        m = {"type": "lg_nuit"}
        if genre == "vision":
            vus = {v["cible"] for v in (vue.get("visions") or [])}
            m["cible"] = random.choice([j for j in autres if j not in vus] or autres)
        elif genre == "protection":
            m["cible"] = random.choice([j for j in vivants if j != vue.get("dernier_protege")] or vivants)
        elif genre == "cupidon":
            if len(vivants) < 2:
                return None
            a, b = random.sample(vivants, 2)
            m["cible"], m["cible2"] = a, b
        elif genre == "vampire":
            vamp = set(vue.get("vampires") or [])
            cibles = [j for j in autres if j not in vamp]
            if not cibles:
                return None
            m["cible"] = random.choice(cibles)
        elif genre == "pyromane":
            arroses = vue.get("arroses") or []
            neufs = [j for j in autres if j not in arroses]
            if arroses and (not neufs or (len(arroses) >= 2 and random.random() < 0.4)):
                m["allumer"] = True
            else:
                m["cible"] = random.choice(neufs)
        else:
            # suspect, renard, corbeau, modele, assassin : un autre joueur.
            m["cible"] = random.choice(suspects if genre in ("corbeau", "assassin", "suspect") else autres)
        return lent(m, 3.0, 8.0)
    if phase == "potion":
        # potion_faite n est rempli que pour la Sorciere qui doit choisir.
        if vue.get("potion_faite") is not False:
            return None
        potions = vue.get("potions") or {}
        m = {"type": "lg_potion"}
        victime = vue.get("victime")
        if potions.get("soin") and victime is not None and (victime == i or random.random() < 0.6):
            m["soin"] = True
        elif potions.get("poison") and random.random() < 0.15:
            cibles = [j for j in suspects if j != i]
            if cibles:
                m["poison"] = random.choice(cibles)
        return lent(m, 3.0, 7.0)
    if phase in ("tir", "succession"):
        if etat.get("tireur") != i:
            return None
        cibles = [j for j in vivants if j != i]
        if not cibles:
            return None
        if phase == "tir":
            connus = [j for j in _lg_loups_vus(vue) if j in cibles]
            cible = random.choice(connus or [j for j in suspects if j in cibles] or cibles)
        else:
            amis = [j for j in cibles if j not in suspects]
            cible = random.choice(amis or cibles)
        return lent({"type": "lg_tir", "cible": cible}, 3.0, 7.0)
    if phase in ("debat", "election"):
        if str(i) in etat["votes"] or (phase == "debat" and etat.get("idiot_revele") == i):
            return None
        if phase == "election":
            return lent({"type": "lg_voter", "cible": random.choice(vivants)}, 4.0, 10.0)
        connus = [j for j in _lg_loups_vus(vue) if etat["vivants"][j]]
        cibles = [j for j in vivants if j != i]
        if not cibles:
            return None
        if connus:
            cible = random.choice(connus)
        else:
            # Un peu de mouvement de foule : souvent comme le dernier vote public.
            cible = random.choice([j for j in suspects if j in cibles] or cibles)
        return lent({"type": "lg_voter", "cible": cible}, 5.0, 12.0)
    return None


# =============================================================
# PICOPOLY (Monopoly a boire)
# =============================================================
@jeu("picolopoly")
def cerveau_picolopoly(etat, i, mem):
    from picolopoly import PP_CASES, PP_GROUPES, PP_AMENDE_PRISON, pp_cout_levee
    if i >= etat["nb_joueurs"] or etat["faillite"][i] or etat["phase"] == "fin":
        return None
    argent = etat["argent"][i]
    props = etat["proprietes"]
    a = etat["attente"]
    if a is not None:
        if a["joueur"] != i:
            return None
        t = a["type"]
        if t == "achat":
            v = argent - a["prix"] >= 150 or (argent >= a["prix"] and random.random() < 0.3)
        elif t == "echange":
            p = a["proposition"]
            gagne = sum(PP_CASES[c]["prix"] for c in p["donne"]) + p["argent_donne"]
            perd = sum(PP_CASES[c]["prix"] for c in p["recoit"]) + p["argent_recu"]
            v = gagne >= perd * 1.2
        elif t in ("prison_carte", "parc"):
            v = True
        elif t == "cible":
            v = random.choice(a["cibles"])
        elif t == "deplacement":
            # Une case achetable libre si possible, sinon au hasard.
            libres = [c for c in a["options"] if props[c] is not None and props[c]["proprietaire"] is None]
            v = random.choice(libres or a["options"])
        elif t == "sommelier":
            v = {}
            for _ in range(a["total"]):
                k = random.choice(a["cibles"])
                v[str(k)] = v.get(str(k), 0) + 1
        else:
            return None
        return lent({"type": "pp_choix", "valeur": v}, 1.5, 3.5)
    mes = [c for c, p in enumerate(props) if p is not None and p["proprietaire"] == i]

    def maisons(g):
        return [props[k]["maisons"] for k in PP_GROUPES[g]]

    d = etat["dette"]
    if d is not None:
        if d["joueur"] != i:
            return None
        ventes = [c for c in mes if props[c]["maisons"] > 0
                  and props[c]["maisons"] == max(maisons(PP_CASES[c]["groupe"]))]
        if ventes:
            return lent({"type": "pp_vendre", "case": ventes[0]}, 1.0, 2.0)
        hypos = [c for c in mes if not props[c]["hypotheque"]
                 and not (PP_CASES[c]["type"] == "propriete" and any(maisons(PP_CASES[c]["groupe"])))]
        if hypos:
            hypos.sort(key=lambda c: PP_CASES[c]["prix"])
            return lent({"type": "pp_hypothequer", "case": hypos[0]}, 1.0, 2.0)
        return lent({"type": "pp_faillite"}, 2.0, 3.0)
    if etat["courant"] != i or etat["phase"] not in ("tour", "fin_tour"):
        return None
    # Gestion : lever une hypotheque, construire (en gardant une reserve).
    for c in mes:
        if props[c]["hypotheque"] and argent - pp_cout_levee(c) > 600:
            return lent({"type": "pp_lever", "case": c}, 1.0, 2.0)
    for c in mes:
        case = PP_CASES[c]
        if case["type"] != "propriete":
            continue
        g = case["groupe"]
        groupe = PP_GROUPES[g]
        if not all(props[k]["proprietaire"] == i and not props[k]["hypotheque"] for k in groupe):
            continue
        p = props[c]
        if p["maisons"] < 5 and p["maisons"] == min(maisons(g)) and argent - case["maison"] > 350:
            return lent({"type": "pp_construire", "case": c}, 1.0, 2.0)
    if etat["phase"] == "tour":
        # (apres un double, a_lance reste vrai : on relance quand meme)
        if etat["en_prison"][i] and not etat["a_lance"]:
            if etat["cartes_sortie"][i]:
                return lent({"type": "pp_prison_carte"}, 1.0, 2.0)
            if argent > 500 + PP_AMENDE_PRISON:
                return lent({"type": "pp_prison_payer"}, 1.0, 2.0)
        return lent({"type": "pp_lancer"}, 1.0, 2.5)
    if etat["phase"] == "fin_tour":
        return lent({"type": "pp_terminer"}, 1.5, 3.0)
    return None


# =============================================================
# CASINO (roulette, blackjack, poker)
# Le bot ne choisit pas la table (c est Admin01 qui mene le casino).
# =============================================================
CS_PARIS_BOT = ["rouge", "noir", "pair", "impair", "manque", "passe", "d1", "d2", "d3"]


def _cs_force_poker(cartes, tableau):
    from casino import RANGS, cs_meilleure_main
    if len(cartes) < 2:
        return 0.0
    if not tableau:
        a, b = sorted(RANGS.index(c[0]) for c in cartes)
        if a == b:
            return 0.55 + a / 30
        f = (a + b) / 24 * 0.55 + (0.05 if cartes[0][1] == cartes[1][1] else 0) + (0.04 if b - a == 1 else 0)
        return f
    valeur = cs_meilleure_main(cartes + tableau)[0]
    return [0.2, 0.45, 0.62, 0.72, 0.8, 0.85, 0.9, 0.96, 0.99][valeur[0]] + (valeur[1] / 13 * 0.08 if len(valeur) > 1 else 0)


@jeu("casino")
def cerveau_casino(etat, i, mem):
    from casino import cs_valeur_bj, CS_MISE_MIN
    if i >= etat["nb_joueurs"]:
        return None
    jeu_cs, phase, t = etat["jeu"], etat["phase"], etat.get("t")
    jetons = etat["jetons"][i]
    if t is None:
        return None
    if jeu_cs in ("roulette", "blackjack") and phase == "mises":
        a_mise = bool(t["mises"][i])
        if not a_mise and jetons >= CS_MISE_MIN:
            montant = max(CS_MISE_MIN, min(jetons, random.choice([10, 20, 20, 50, 50, 100])))
            if jeu_cs == "roulette":
                pari = random.choice(CS_PARIS_BOT) if random.random() < 0.8 else "n" + str(random.randint(0, 36))
                from casino import cs_pari_valide
                if not cs_pari_valide(pari):
                    pari = random.choice(CS_PARIS_BOT)
                return lent({"type": "cs_rl_miser", "pari": pari, "montant": montant}, 2.0, 6.0)
            return lent({"type": "cs_bj_miser", "montant": montant}, 2.0, 6.0)
        if not t["prets"][i]:
            return lent({"type": "cs_pret"}, 1.0, 3.0)
        return None
    if jeu_cs == "blackjack" and phase == "jeu" and t.get("courant") == i:
        main = t["mains"][i][t["main_courante"]]
        total, souple = cs_valeur_bj(main["cartes"])
        visible = t["croupier"][0] if t["croupier"] else "T"
        v_crp = 10 if visible[0] in "TJQK" else (11 if visible[0] == "A" else int(visible[0]))
        if len(main["cartes"]) == 2 and total in (10, 11) and jetons >= main["mise"] and v_crp < total:
            return lent({"type": "cs_bj_doubler"}, 1.5, 3.0)
        if souple:
            tirer = total < 18
        elif total <= 11:
            tirer = True
        elif total <= 16:
            tirer = v_crp >= 7 or (total == 12 and v_crp <= 3)
        else:
            tirer = False
        return lent({"type": "cs_bj_tirer" if tirer else "cs_bj_rester"}, 1.5, 3.0)
    if jeu_cs == "poker" and phase == "jeu" and t.get("courant") == i:
        force = _cs_force_poker(t["cartes"][i], t["tableau"])
        du = t["mise_max"] - t["mise_tour"][i]
        pot = sum(t["engage"])
        cote = du / (du + pot) if du > 0 else 0
        if force > 0.7 and random.random() < 0.6 and jetons > du:
            total = t["mise_max"] + t["relance_min"] * random.choice([1, 1, 2, 3])
            m = {"type": "cs_pk_relancer", "montant": total}
            if total >= t["mise_tour"][i] + jetons:
                m = {"type": "cs_pk_relancer", "tapis": True}
            return lent(m, 2.0, 5.0)
        if du <= 0:
            return lent({"type": "cs_pk_suivre"}, 1.5, 4.0)
        if force >= cote + 0.15 or du <= 20 or random.random() < 0.08:
            return lent({"type": "cs_pk_suivre"}, 2.0, 5.0)
        return lent({"type": "cs_pk_coucher"}, 2.0, 4.0)
    return None


# =============================================================
# PICHENETTE FC (foot a pichenettes)
# Le bot « imagine » quelques tirs avec la vraie physique (simuler, sans
# connaitre les visees adverses, secretes) et garde le meilleur, puis le joue
# avec un peu de maladresse.
# =============================================================
@jeu("pichenette")
def cerveau_pichenette(etat, i, mem):
    from pichenette import (FORMATIONS, NB_PALETS, OBJETS, PF_CONFIG, simuler, tirs_par_joueur,
                            _visees_equipe, _geles)
    if i >= etat["nb_joueurs"]:
        return None
    e = etat["equipe_de"][i]
    if e is None:
        return None
    phase = etat["phase"]
    if phase == "formation":
        f = etat["formations"][e]
        if f["valide"]:
            return None
        # Seul dans son equipe : il choisit. Avec des coequipiers, il valide
        # le choix en cours (sans l ecraser) apres un moment.
        if len(etat["equipes"][e]) == 1:
            return lent({"type": "pf_formation", "attaque": random.choice(list(FORMATIONS)),
                         "defense": random.choice(list(FORMATIONS)), "valider": True}, 3.0, 8.0)
        return lent({"type": "pf_formation", "valider": True}, 8.0, 15.0)
    if phase != "visee" or etat["prets"][i]:
        return None
    # Mode objets : une fois par tour, il tente parfois un objet de la reserve.
    if etat.get("mode") == "objets" and etat["reserves"][e] and str(e) not in etat["activations"] \
            and mem.get("pf_objet_tour") != etat["tour"]:
        mem["pf_objet_tour"] = etat["tour"]
        if random.random() < 0.6:
            oid = random.choice(etat["reserves"][e])
            c = OBJETS[oid]["cible"]
            cible = None
            if c == "palet_adverse":
                cible = random.randrange((1 - e) * NB_PALETS, (2 - e) * NB_PALETS)
            elif c == "palets_miens":
                cible = random.sample(range(e * NB_PALETS, (e + 1) * NB_PALETS), 2)
            elif c == "point":
                bx, by = etat["balle"]
                cible = [round(bx, 3), round(by + (0.15 if e == 1 else -0.15), 3)]
            elif c == "joueur_adverse":
                cible = random.choice(etat["equipes"][1 - e])
            return lent({"type": "pf_objet", "objet": oid, "cible": cible}, 1.0, 4.0)
    palets, balle = etat["palets"], etat["balle"]
    pris = {v["palet"] for j, vs in _visees_equipe(etat, e).items() if j != i for v in vs}
    pris |= _geles(etat)
    miens = [k for k in range(e * NB_PALETS, (e + 1) * NB_PALETS) if k not in pris]
    if not miens:
        return lent({"type": "pf_viser", "tirs": [], "pret": True})
    sens = 1 if e == 0 else -1            # l equipe 0 attaque vers les y positifs
    ly = PF_CONFIG["terrain"]["longueur"] / 2
    but = (0.0, sens * ly)
    r = PF_CONFIG["palet"]["rayon"] + PF_CONFIG["balle"]["rayon"]

    def note(res):
        if res["but"] == e:
            return 100
        if res["but"] is not None:
            return -100
        bx, by = res["balle"]
        return sens * by * 10 - abs(bx) * 2

    def visee_vers(k, cx, cy, force):
        dx, dy = cx - palets[k][0], cy - palets[k][1]
        lg = math.hypot(dx, dy) or 1
        return (k, dx / lg, dy / lg, force)

    gx, gy = but[0] - balle[0], but[1] - balle[1]
    lg = math.hypot(gx, gy) or 1
    contact = (balle[0] - gx / lg * r * 0.9, balle[1] - gy / lg * r * 0.9)
    candidats = []
    for k in miens:
        for decal in (-0.02, 0.0, 0.02):
            for force in (0.45, 0.7, 0.95):
                candidats.append([visee_vers(k, contact[0] + decal, contact[1], force)])
    meilleur, score = None, -1e9
    for tirs in random.sample(candidats, min(len(candidats), 18)):
        s = note(simuler(palets, balle, tirs)) + random.random()
        if s > score:
            meilleur, score = tirs, s
    tirs = list(meilleur)
    if tirs_par_joueur(etat, i) > 1:
        # Deuxieme capsule : la plus reculee vient defendre devant son but.
        restants = [k for k in miens if k != tirs[0][0]]
        if restants:
            k = min(restants, key=lambda k: sens * palets[k][1])
            tirs.append(visee_vers(k, balle[0] * 0.5, -sens * ly * 0.6, 0.35))
    sortie = []
    for k, dx, dy, force in tirs:
        a = math.atan2(dy, dx) + random.gauss(0, 0.05)
        sortie.append({"palet": k, "dx": round(math.cos(a), 4), "dy": round(math.sin(a), 4),
                       "force": round(min(1.0, max(0.1, force + random.gauss(0, 0.06))), 3)})
    return lent({"type": "pf_viser", "tirs": sortie, "pret": True}, 3.0, 9.0)


# =============================================================
# LA TOURNEE (plateau + mini-jeux)
# Les mini-jeux se jouent sur les telephones : le bot envoie un score
# credible (au hasard dans une fourchette de joueur moyen) au bout du temps
# du mini-jeu.
# =============================================================
def _tr_score(m):
    if m.get("sens") == "petit":
        # Temps (ms), erreurs... : plus petit = mieux.
        return round(m["max"] * random.uniform(0.04, 0.35))
    return round(m["max"] * random.uniform(0.25, 0.8))


@jeu("tournee")
def cerveau_tournee(etat, i, mem):
    from tournee import _OBJETS, _minijeu
    if i >= etat["nb_joueurs"] or etat["partis"][i]:
        return None
    phase = etat["phase"]
    if phase == "des":
        cle = str(i)
        if cle not in etat["des"]:
            poche = etat["objets"][i]
            deja = cle in etat.get("objet_tour", {})
            if poche and not deja and random.random() < 0.5:
                autres = [j for j in range(etat["nb_joueurs"]) if j != i and not etat["partis"][j]]
                for o in poche:
                    d = _OBJETS.get(o, {})
                    if d.get("passif") or d.get("piege") or d.get("taxi"):
                        continue
                    m = {"type": "tr_objet", "objet": o}
                    if d.get("cible"):
                        cibles = [j for j in autres if not d.get("vol_objet") or etat["objets"][j]]
                        if not cibles:
                            continue
                        # On vise le plus riche.
                        m["cible"] = max(cibles, key=lambda j: (etat["pieces"][j], random.random()))
                    if d.get("choix_de"):
                        m["valeur"] = random.randint(5, 10)
                    return lent(m, 2.0, 4.0)
            return lent({"type": "tr_lancer", "des": 2 if random.random() < 0.3 else 1}, 2.0, 5.0)
        options = etat["options"].get(cle)
        if options and cle not in etat["choix"]:
            # Pas de peage si on peut l eviter, sinon au hasard.
            sans = [k for k, o in enumerate(options) if not o.get("peage")]
            return lent({"type": "tr_chemin", "choix": random.choice(sans or list(range(len(options))))}, 1.5, 4.0)
        return None
    if phase == "boutique":
        cle = str(i)
        if cle in etat["boutiques"] and cle not in etat["achats"]:
            stock = [s for s in etat["boutiques"][cle]["stock"] if s["prix"] <= etat["pieces"][i] - 3]
            objet = random.choice(stock)["id"] if stock and random.random() < 0.7 else None
            return lent({"type": "tr_acheter", "objet": objet}, 2.0, 6.0)
        return None
    if phase == "minijeu":
        if str(i) in etat["scores"]:
            return None
        mj = etat["minijeu"]
        m = _minijeu(mj["id"])
        return lent({"type": "tr_score", "score": _tr_score(m)},
                    mj["consignes"] + mj["duree"] * 0.6, mj["consignes"] + mj["duree"] * 0.95)
    if phase == "resultats":
        if not etat["suivants"][i]:
            return lent({"type": "tr_suivant"}, 4.0, 9.0)
    return None


# =============================================================
# ROYAUMES (conquete)
# =============================================================
@jeu("conquete")
def cerveau_conquete(etat, i, mem):
    from conquete import sw_possibles, _menaces, _region
    if etat["phase"] != "jeu" or etat["joueur"] != i:
        return None
    etape = etat["etape"]
    j = etat["j"][i]
    if etape == "choix":
        file = etat["file"]
        rangs = [r for r in range(min(len(file), j["pieces"] + 1, 4))]
        rang = max(rangs, key=lambda r: file[r]["pions"] + file[r]["pieces"] - 1.5 * r + random.random())
        return lent({"type": "sw_choisir", "rang": rang}, 2.0, 5.0)
    p = etat["peuples"].get(str(j["actif"])) if j["actif"] is not None else None
    if p is None:
        return None
    miennes = [r for r, reg in enumerate(etat["regions"]) if reg["p"] == p["id"]]
    pions_sur = sum(etat["regions"][r]["pions"] for r in miennes)
    if etape in ("action", "conquete"):
        dispo = p["main"] + (0 if etat["tour"]["rassemble"] else sum(etat["regions"][r]["pions"] - 1 for r in miennes))
        if etape == "action" and not etat["tour"]["choisi"] and p["tours"] >= 2 \
                and dispo < 3 and random.random() < 0.7:
            return lent({"type": "sw_declin"}, 2.0, 4.0)
        possibles = sw_possibles(etat)
        if possibles:
            def interet(r):
                reg = etat["regions"][int(r)]
                return (reg["j"] is not None and reg["j"] != i) * 1.5 - possibles[r]["cout"] * 0.5 + random.random()
            abordables = [r for r in possibles if possibles[r]["cout"] <= dispo]
            if abordables:
                r = max(abordables, key=interet)
                return lent({"type": "sw_conquerir", "region": int(r)}, 1.5, 3.5)
            if dispo >= 1 and not etat["tour"]["de_utilise"]:
                proches = [r for r in possibles if possibles[r]["cout"] <= dispo + 2]
                if proches:
                    r = min(proches, key=lambda r: possibles[r]["cout"])
                    return lent({"type": "sw_tenter", "region": int(r)}, 2.0, 4.0)
        return lent({"type": "sw_fin_conquete"}, 1.5, 3.0)
    if etape == "redeploiement":
        total = pions_sur + p["main"]
        if not miennes:
            return lent({"type": "sw_redeployer", "pions": {}}, 1.5, 3.0)
        rep = {r: 1 for r in miennes}
        reste = total - len(miennes)
        if reste < 0:
            return None             # cas tordu : le minuteur redeploiera d office
        ordre = sorted(miennes, key=lambda r: (-_menaces(etat, i, r), random.random()))
        k = 0
        while reste > 0:
            rep[ordre[k % len(ordre)]] += 1
            reste -= 1
            k += 1
        return lent({"type": "sw_redeployer", "pions": {str(r): v for r, v in rep.items()}}, 2.0, 4.0)
    return None


# =============================================================
# BEERBATTLE (plateau de cartes)
# Meme logique que les joueurs fictifs de la page : boire le moins, viser
# le verre le plus plein.
# =============================================================
def _bb_observer(mem, msg):
    if msg.get("type") == "bb_carte_case":
        mem["bb_carte"] = msg.get("carte")


def _bb_pas_vers(depuis, vers, portee):
    l, c, reste = depuis["ligne"], depuis["colonne"], portee
    while reste > 0 and (l != vers["ligne"] or c != vers["colonne"]):
        if l != vers["ligne"]:
            l += 1 if vers["ligne"] > l else -1
        else:
            c += 1 if vers["colonne"] > c else -1
        reste -= 1
    if (l, c) == (depuis["ligne"], depuis["colonne"]):
        return None
    return l, c


@jeu("beerbattle", observer=_bb_observer)
def cerveau_beerbattle(etat, i, mem):
    n = etat["nb_joueurs"]
    if i >= n or etat["elimine"][i]:
        return None
    taille = len(etat["plateau"])
    pos = etat["positions"]
    if etat["phase"] == "placement":
        if etat["joueur_en_placement"] != i:
            return None
        libres = []
        for l in range(taille):
            for c in range(taille):
                if any(p and p["ligne"] == l and p["colonne"] == c and not etat["elimine"][k]
                       for k, p in enumerate(pos)):
                    continue
                proche = min([abs(p["ligne"] - l) + abs(p["colonne"] - c) for k, p in enumerate(pos) if p and k != i] or [99])
                libres.append((proche + random.random(), l, c))
        if not libres:
            return None
        libres.sort(reverse=True)
        _, l, c = random.choice(libres[:4])
        return lent({"type": "bb_placer", "ligne": l, "colonne": c})
    if etat["phase"] != "jeu" or etat["joueur_courant"] != i:
        return None
    main = etat["mains"][i]
    moi = pos[i]
    if moi is None:
        return None
    if etat["action_faite"]:
        carte = etat["plateau"][moi["ligne"]][moi["colonne"]]
        if carte is None:
            return lent({"type": "bb_ramasser", "decision": "rien"}, 1.0, 2.0)
        if len(main) < etat["max_cartes"]:
            return lent({"type": "bb_ramasser", "decision": "prendre"}, 1.0, 2.0)
        # Main pleine : on echange la plus faible carte de deplacement contre mieux.
        faibles = [k for k, c in enumerate(main) if c["type"] == "deplacement"]
        if faibles and carte["type"] != "deplacement":
            return lent({"type": "bb_ramasser", "decision": "echanger", "index_echange": faibles[0]}, 1.0, 2.0)
        return lent({"type": "bb_ramasser", "decision": "rien"}, 1.0, 2.0)
    vivants = [{"index": k, "pos": p, "dist": abs(p["ligne"] - moi["ligne"]) + abs(p["colonne"] - moi["colonne"])}
               for k, p in enumerate(pos) if k != i and p and not etat["elimine"][k]]
    if not vivants:
        return lent({"type": "bb_ne_rien_faire"})

    def idx(t):
        return next((k for k, c in enumerate(main) if c["type"] == t), -1)

    def trier(liste):
        return sorted(liste, key=lambda v: (-etat["verres"][v["index"]], random.random()))

    gorgees = [k for k, c in enumerate(main) if c["type"] == "gorgee"]
    meilleure = max(gorgees, key=lambda k: main[k]["valeur"]) if gorgees else -1
    armes = etat["armes"][i]

    def a_portee(d):
        return any(d <= (6 if a["type"] == "roi" else 2) for a in armes)

    if idx("dame") >= 0 and etat["verres"][i] >= 11:
        return lent({"type": "bb_dame", "cible": i, "index_carte": idx("dame")})
    portees = [v for v in vivants if v["dist"] >= 1 and a_portee(v["dist"])]
    if portees:
        cible = trier(portees)[0]
        if idx("joker") >= 0:
            return lent({"type": "bb_attaquer", "cible": cible["index"], "index_carte": idx("joker"), "type_attaque": "joker"})
        if meilleure >= 0:
            return lent({"type": "bb_attaquer", "cible": cible["index"], "index_carte": meilleure, "type_attaque": "arme"})
    ici = [v for v in vivants if v["dist"] == 0]
    if ici:
        cible = trier(ici)[0]
        if idx("as") >= 0:
            return lent({"type": "bb_attaquer", "cible": cible["index"], "index_carte": idx("as"), "type_attaque": "as"})
        if meilleure >= 0:
            return lent({"type": "bb_attaquer", "cible": cible["index"], "index_carte": meilleure, "type_attaque": "cc_gorgee"})
        return lent({"type": "bb_attaquer", "cible": cible["index"], "type_attaque": "cc_mains_nues"})
    voisins = [v for v in vivants if v["dist"] == 1]
    if idx("as") >= 0 and voisins:
        cible = trier(voisins)[0]
        return lent({"type": "bb_as_deplacer", "ligne": cible["pos"]["ligne"], "colonne": cible["pos"]["colonne"],
                     "cible": cible["index"], "index_carte": idx("as")})
    if not armes:
        for t in ("roi", "valet"):
            if idx(t) >= 0:
                return lent({"type": "bb_poser_arme", "index_carte": idx(t)})
    cible = trier(vivants)[0]
    k_depl = idx("deplacement")
    portee = main[k_depl]["valeur"] if k_depl >= 0 else 1
    dest = _bb_pas_vers(moi, cible["pos"], portee)
    if dest:
        return lent({"type": "bb_deplacer", "ligne": dest[0], "colonne": dest[1],
                     "via_carte": k_depl >= 0, "index_carte": k_depl if k_depl >= 0 else None})
    return lent({"type": "bb_ne_rien_faire"})


# =============================================================
# UNDERCOVER (indices a l oral)
# Un bot ne parle pas : a son tour il touche « J ai parle » apres un temps
# de parole, puis vote au hasard (il n a entendu personne). Mr White elimine
# tente un mot de la meme famille.
# =============================================================
@jeu("undercover")
def cerveau_undercover(etat, i, mem):
    from undercover import _orateur
    from undercover_mots import paires
    if i >= etat["nb_joueurs"]:
        return None
    phase = etat["phase"]
    if phase == "lecture":
        if etat["vivants"][i] and not etat["prets"][i]:
            return lent({"type": "uc_pret"}, 2.0, 5.0)
        return None
    if phase == "indices":
        if _orateur(etat) == i:
            return lent({"type": "uc_fini"}, 4.0, 7.0)
        return None
    if phase == "vote":
        if not etat["vivants"][i] or str(i) in etat["votes"]:
            return None
        candidats = [j for j in (etat["departage"] or
                                 [k for k in range(etat["nb_joueurs"]) if etat["vivants"][k]]) if j != i]
        if not candidats:
            return None
        return lent({"type": "uc_voter", "cible": random.choice(candidats)}, 6.0, 15.0)
    if phase == "mrwhite":
        elim = etat.get("elimination") or {}
        if elim.get("joueur") == i and etat.get("devinette") is None:
            mots = [m for _, a, b in paires(etat["famille"]) for m in (a, b)]
            return lent({"type": "uc_deviner", "mot": random.choice(mots) if mots else "bière"}, 5.0, 10.0)
    return None


# =============================================================
# GRIBOUILLOPHONE (ecrire, dessiner, deviner)
# Le bot ecrit des phrases de la banque, gribouille des traits au hasard,
# « devine » un dessin par une phrase au hasard, et vote au hasard. C est
# Admin01 (l hote) qui mene la revelation.
# =============================================================
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _td_points(points):
    out = []
    for x, y in points:
        for v in (x, y):
            out.append(_B64[v // 64] + _B64[v % 64])
    return "".join(out)


def td_gribouillis():
    """Quelques traits au hasard dans le cadre 400 x 300 (format de la page)."""
    traits = []
    for _ in range(random.randint(2, 6)):
        x, y = random.randint(60, 340), random.randint(50, 250)
        pts = []
        for _ in range(random.randint(8, 30)):
            x = min(400, max(0, x + random.randint(-25, 25)))
            y = min(300, max(0, y + random.randint(-25, 25)))
            pts.append((x, y))
        traits.append({"c": random.randint(0, 7), "e": random.randint(0, 2), "p": _td_points(pts)})
    return traits


@jeu("telephone")
def cerveau_telephone(etat, i, mem):
    from telephone import _nature, _candidats_album, _candidats_final
    from telephone_phrases import PHRASES
    if i >= etat["nb_joueurs"]:
        return None
    phase = etat["phase"]
    if phase == "etape":
        if etat["envoyes"][i]:
            return None
        nature = _nature(etat)
        if nature == "dessin":
            return lent({"type": "td_contribuer", "contenu": td_gribouillis()}, 8.0, 20.0)
        texte = etat["suggestions"][i] if nature == "phrase" and etat["suggestions"][i] else random.choice(PHRASES)
        return lent({"type": "td_contribuer", "contenu": texte}, 6.0, 15.0)
    if phase == "vote_album":
        if str(i) in etat["votes"]:
            return None
        cands = _candidats_album(etat, i)
        if cands:
            return lent({"type": "td_voter", "cible": random.choice(cands)}, 3.0, 8.0)
        return None
    if phase == "vote_final":
        if str(i) in etat["votes_final"]:
            return None
        cands = [c for c in _candidats_final(etat) if etat["albums"][c[0]][c[1]]["auteur"] != i]
        if cands:
            a, k = random.choice(cands)
            return lent({"type": "td_voter", "album": a, "cible": k}, 3.0, 8.0)
    return None
