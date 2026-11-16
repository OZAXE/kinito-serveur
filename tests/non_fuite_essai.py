"""
Essais de NON-FUITE de l'Enquete (tranche 9).

Des centaines de parties jouees au hasard : tous les joueurs font des actions
(indices, achats, lectures, consultations, expertises, accusations), le tueur
sabote, les autres essaient aussi (et doivent echouer sans rien apprendre),
le temps avance (temoins, rumeurs, evenements). A CHAQUE etape, pour chaque
joueur, on verifie ce qu il recoit :

- etat public : jamais la solution, le tueur, le stock, les faux, les
  sabotages, le programme des evenements, ni l heure du prochain temoin ;
- vue privee : exactement les memes cles (et sous-cles) pour tous, la
  console n existe que pour le tueur ;
- archive et textes d indices : memes champs pour un vrai et un faux ;
- messages prives : un enqueteur ne recoit que des types de messages que le
  tueur peut recevoir aussi (sauf l ecoute, propre au tueur et jamais envoyee
  a un autre) ;
- refus : la meme reponse neutre pour une action reservee au tueur ;
- statistiques : le tueur recoit le Temoin anonyme aussi souvent que les
  autres.

Usage : python tests/non_fuite_essai.py [nombre de parties]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import enquete as m

ECHECS = []
NOMS = ["Lea", "Max", "Zoe", "Tom", "Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Ida"]

# Ce que l etat public ne doit JAMAIS contenir avant la fin (cles internes)
CLES_SECRETES = {"solution", "tueur", "pool", "vrai", "sabotages", "programme", "differes", "detournement",
                 "faux_emis", "prochain_bonus_a", "prochaine_rumeur_a", "dernier_bonus", "bonus", "perso",
                 "accusations", "faux_en_attente", "interception", "charges", "console", "retenu", "visible_a"}
# Messages prives propres au tueur (jamais envoyes a un autre)
PRIVES_TUEUR = {"enq_ecoute"}


def verifier(cond, quoi):
    if not cond:
        ECHECS.append(quoi)
        print("  ECHEC " + quoi)
    return cond


def cles(o, prefixe="", profondeur=2):
    """Les cles d un message sur 2 niveaux (au-dela, le contenu depend des noms des joueurs)."""
    out = set()
    if profondeur == 0:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(prefixe + k)
            out |= cles(v, prefixe + k + ".", profondeur - 1)
    elif isinstance(o, list):
        for x in o:
            out |= cles(x, prefixe + "[].", profondeur)
    return out


def cles_publiques_interdites(o):
    trouve = set()
    if isinstance(o, dict):
        for k, v in o.items():
            if k in CLES_SECRETES:
                trouve.add(k)
            trouve |= cles_publiques_interdites(v)
    elif isinstance(o, list):
        for x in o:
            trouve |= cles_publiques_interdites(x)
    return trouve


def nouvelle(n, graine, vitesse):
    rng = random.Random(graine)
    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    univers = rng.choice(list(m.PACKS))
    m.action(e, "Lea", {"type": "enq_config", "vitesse": vitesse, "univers": univers}, 1000.0)
    ids = [p["id"] for p in m.PACKS[univers]["portraits"]]
    for i in range(n):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": ids[i]}, 1000.0)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=rng)
    t = 1000.0 + m.CONFIG["duree_ouverture"]
    m.expirer(e, t, rng=rng)
    return e, t, rng


def action_au_hasard(e, nom, t, rng):
    p = m._perso(e, nom)
    lus = [x["id"] for x in m.vue_privee(e, nom, t)["archive"] if x["lu"]]
    non_lus = [x["id"] for x in m.vue_privee(e, nom, t)["archive"] if not x["lu"]]
    autres = [j["nom"] for j in e["joueurs"] if j["nom"] != nom]
    choix = rng.random()
    if choix < 0.15:
        p["pe"] += rng.randint(1, 4)
        return {"type": "enq_indice"}
    if choix < 0.22:
        return {"type": "enq_acheter"}
    if choix < 0.32 and non_lus:
        return {"type": "enq_lire", "id": rng.choice(non_lus)}
    if choix < 0.40 and lus:
        return {"type": "enq_consulter", "id": rng.choice(lus)}
    if choix < 0.46 and lus:
        return {"type": "enq_expertise", "id": rng.choice(lus), "paiement": rng.choice(["pe", "gorgee"])}
    if choix < 0.50:
        return {"type": "enq_accuser", "suspect": rng.choice([j["nom"] for j in e["joueurs"]]),
                "arme": rng.choice(e["armes"]), "lieu": rng.choice(e["lieux"])}
    if choix < 0.58:
        p["pe"] += rng.randint(0, 3)
        return {"type": "enq_charge"}
    if choix < 0.80:
        genre = rng.choice(m.SABOTAGES)
        msg = {"type": "enq_sabotage", "sabotage": genre, "cible": rng.choice(autres)}
        if genre == "fausse_piste":
            msg.update(categorie=rng.choice(["suspect", "lieu", "arme"]), mode=rng.choice(["remplacement", "detournement"]))
        if genre == "rumeur":
            cat = rng.choice(["suspect", "lieu", "arme"])
            msg.update(categorie=cat, element=rng.choice(m._candidats(e, cat)))
        return msg
    if choix < 0.85:
        return {"type": "enq_refuser"}
    pen = [x for x in e.get("penalites", []) if x["nom"] == nom and not x["bu"]]
    if pen:
        return {"type": "enq_bu", "id": pen[0]["id"]}
    return {"type": "enq_etat_inconnu"}


def controler_etape(e, t, recus, k):
    """Controles a chaque etape, pour tous les joueurs."""
    if e["phase"] != "enquete":
        return
    pub = m.vue_publique(e, t)
    interdites = cles_publiques_interdites({x: y for x, y in pub.items() if x != "fin"})
    verifier(not interdites, f"etat public : cles secretes {interdites}")
    texte_pub = json.dumps(pub, ensure_ascii=False)
    verifier(str(e["prochain_bonus_a"]) not in texte_pub or e["prochain_bonus_a"] is None, "etat public : heure du prochain temoin")
    vues = {j["nom"]: m.vue_privee(e, j["nom"], t) for j in e["joueurs"]}
    formes = {}
    for nom, v in vues.items():
        formes[nom] = cles(v)
        verifier(set(v.keys()) == set(vues[k].keys()), f"vue privee : cles differentes pour {nom}")
        if nom != k:
            verifier(v["console"]["charges"] == 0 and not v["console"]["cibles"] and v["solution"]["tueur"] is None
                     and v["role"] == "enqueteur", f"vue privee de {nom} : role, solution ou console")
        for x in v["archive"]:
            verifier(set(x.keys()) == {"id", "source", "recu_a", "lu", "tampon"}, "archive : champs en trop")
    # Memes sous-cles pour tous (a archive egale) : on compare les cles de premier niveau des archives
    verifier(len({frozenset(c for c in f if not c.startswith(("archive", "accusations"))) for f in formes.values()}) == 1,
             "vue privee : structure differente selon le joueur")
    # Messages prives recus
    for nom, liste in recus.items():
        for msg in liste:
            if nom != k:
                verifier(msg.get("type") not in PRIVES_TUEUR, f"{nom} a recu un message propre au tueur ({msg.get('type')})")
            if msg.get("type") == "enq_texte":
                verifier(set(msg.keys()) == {"type", "id", "texte", "source", "recu_a", "tampon", "gratuit", "expertise",
                                             "categorie", "element"}, "enq_texte : champs inattendus")


def partie(graine, n, vitesse, pas=140):
    e, t, rng = nouvelle(n, graine, vitesse)
    k = e["tueur"]
    refus_neutres = True
    for _ in range(pas):
        if e["phase"] != "enquete":
            break
        t += rng.uniform(5, 90)
        recus = {j["nom"]: [] for j in e["joueurs"]}
        for ev in m.expirer(e, t, rng=rng):
            for nom in recus:
                if ev["a"] is None or m.cle_nom(ev["a"]) == m.cle_nom(nom):
                    recus[nom].append(ev["msg"])
        nom = k if rng.random() < 0.35 else rng.choice([j["nom"] for j in e["joueurs"]])   # le tueur joue souvent
        msg = action_au_hasard(e, nom, t, rng)
        ev = m.action(e, nom, msg, t, rng=rng)
        for x in ev:
            cible = x["a"] or None
            for n2 in recus:
                if cible is None or m.cle_nom(cible) == m.cle_nom(n2):
                    recus[n2].append(x["msg"])
        # Un enqueteur qui tente une action du tueur : reponse neutre, rien appris
        if nom != k and msg["type"] in ("enq_charge", "enq_sabotage"):
            refus_neutres &= len(ev) == 1 and ev[0]["msg"] == {"type": "enq_erreur", "message": "Action impossible."}
        controler_etape(e, t, recus, k)
    verifier(refus_neutres, "les actions du tueur tentees par un autre recoivent toujours la meme reponse neutre")
    # Vrais et faux indices : memes champs dans l archive interne (hors le marqueur secret)
    champs = {frozenset(set(x.keys()) - {"vrai", "retenu", "visible_a"}) for p in e["perso"].values() for x in p["archive"]}
    verifier(len(champs) <= 1, "archive interne : un faux n a pas les memes champs qu un vrai")
    return e


def frequence_temoin(parties=600, n=6):
    """Le tueur recoit le Temoin anonyme aussi souvent qu un autre joueur."""
    pour_tueur = total = 0
    for g in range(parties):
        e, t, rng = nouvelle(n, 10_000 + g, "soiree")
        e["programme"] = []
        for _ in range(4):
            if e["prochain_bonus_a"] is None:
                break
            ev = m.expirer(e, e["prochain_bonus_a"], rng=rng)
            for x in ev:
                if x["msg"].get("type") == "enq_nouvel_indice":
                    total += 1
                    pour_tueur += m.cle_nom(x["a"]) == m.cle_nom(e["tueur"])
    part = pour_tueur / max(1, total)
    verifier(abs(part - 1 / n) < 0.035, f"temoin anonyme : le tueur le recoit {part:.1%} du temps (attendu {1 / n:.1%})")
    return part


if __name__ == "__main__":
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    print(f"Non-fuite : {nb} parties jouees au hasard")
    sabotages = faux = 0
    for g in range(nb):
        n = 4 + g % 9
        vitesse = ["flash", "express", "soiree", "marathon"][g % 4]
        e = partie(g, n, vitesse)
        sabotages += len(e["sabotages"])
        faux += sum(1 for p in e["perso"].values() for x in p["archive"] if not x["vrai"])
    print(f"  {sabotages} sabotages et {faux} faux indices joues, controles a chaque etape pour chaque joueur")
    part = frequence_temoin()
    print(f"  temoin anonyme recu par le tueur : {part:.1%} des fois (6 joueurs : 16,7 % attendus)")
    print("OK" if not ECHECS else f"{len(ECHECS)} ECHEC(S)")
    sys.exit(1 if ECHECS else 0)
