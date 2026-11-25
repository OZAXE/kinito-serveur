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
           "Emma", "Enzo", "Nina", "Paul", "Rose", "Ugo", "Alix", "Théo", "Mia", "Léo"]


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
