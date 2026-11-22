"""
Essai du moteur du Juste Pinte, hors ligne : des centaines de parties jouees
au hasard (reponses valides et absurdes, absents, delais depasses), de 2 a 8
joueurs, avec toutes les categories. On verifie qu aucune ne plante ni ne se
fige, que les reponses ne fuient pas avant la revelation, et que les regles
tiennent (classement, points, gorgees, fin).
Usage : python tests/justepinte_essai.py [nombre de parties]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import justepinte as J
from justepinte_questions import QUESTIONS, JP_CATEGORIES


def verifier_banque():
    vues = set()
    for q in QUESTIONS:
        assert len(q) == 6, q
        cat, texte, rep, unite, tol, info = q
        assert cat in JP_CATEGORIES, q
        assert texte not in vues, "question en double : " + texte
        vues.add(texte)
        assert isinstance(rep, (int, float)) and not isinstance(rep, bool), q
        assert isinstance(unite, str) and unite, q
        assert tol is None or tol >= 0, q
        assert "poitiers" not in texte.lower(), "pas de question sur Poitiers : " + texte
        if unite == "année":
            assert 1000 < rep < 2100 and isinstance(rep, int), q


def verifier(etat):
    pub = J.jp_etat_public(etat, 0)
    if etat["phase"] == "question":
        assert etat["echeance"] is not None
        assert "reponse" not in pub["question"], "la bonne reponse fuit"
        assert pub["resultat"] is None, "les reponses fuient"
        texte = repr(pub)
        assert "'reponses'" not in texte and "'tirage'" not in texte
    elif etat["phase"] == "revelation":
        assert etat["echeance"] is not None
        assert pub["question"]["reponse"] == etat["tirage"][etat["manche"]][2]
        assert pub["resultat"] is not None
    else:
        assert etat["phase"] == "fin" and etat["echeance"] is None and etat["final"]
        assert pub["question"] is None
    assert all(p >= 0 for p in etat["points"]) and all(g >= 0 for g in etat["gorgees"])


def verifier_revelation(etat, avant_points):
    res = etat["resultat"]
    q = etat["tirage"][etat["manche"]]
    lignes = res["lignes"]
    ecarts = [l["ecart"] for l in lignes]
    assert ecarts == sorted(ecarts), "classement par ecart"
    for l in lignes:
        assert abs(l["ecart"] - abs(l["valeur"] - q[2])) < 1e-6
        assert l["mille"] == (l["ecart"] <= J.tolerance(q) + 1e-9)
        if l["mille"]:
            assert l["boit"] == 0
        assert etat["points"][l["joueur"]] - avant_points[l["joueur"]] == l["points"]
    if lignes and lignes[0]["ecart"] < lignes[-1]["ecart"] and not lignes[-1]["mille"]:
        assert lignes[-1]["boit"] == J._G["dernier"], "le plus loin boit"
    if len({l["rang"] for l in lignes}) <= 1:
        assert all(l["boit"] == 0 for l in lignes), "tous ex aequo : personne ne boit"


def valeur_hasard(q):
    r = random.random()
    if r < 0.05:
        return random.choice(["abc", None, True, "1e999", float("nan"), "", [], {}, 10 ** 15])
    if r < 0.15:
        return q[2]
    if r < 0.25:
        return str(q[2] * random.uniform(0.8, 1.2)).replace(".", ",")
    return q[2] * random.uniform(0, 3) + random.choice([0, 1, -1, 10])


def partie(n, graine):
    random.seed(graine)
    t = 1000.0
    cats = random.choice([None, [], ["alcool"], ["fete", "culture"], ["inconnue"], list(JP_CATEGORIES)])
    manches = random.choice([None, 5, 10, 15, 7, "x"])
    etat = J.jp_initialiser([f"J{i}" for i in range(n)], t, manches=manches, categories=cats)
    assert etat["nb_manches"] in (5, 10, 15) or etat["nb_manches"] <= len(QUESTIONS)
    reveles = 0
    for _ in range(2000):
        verifier(etat)
        if etat["phase"] == "fin":
            break
        r = random.random()
        i = random.randrange(n)
        avant_phase = etat["phase"]
        avant_points = list(etat["points"])
        if r < 0.05:
            if etat["partis"][i]:
                J.jp_joueur_revenu(etat, i, t)
            elif sum(not p for p in etat["partis"]) > 1:
                J.jp_joueur_parti(etat, i, t)
        elif r < 0.12:
            t = etat["echeance"] + 0.1
            J.jp_expirer(etat, t)
        elif etat["phase"] == "question":
            q = etat["tirage"][etat["manche"]]
            J.jp_action(etat, i, {"type": "jp_repondre", "valeur": valeur_hasard(q)}, t)
        else:
            J.jp_action(etat, i, {"type": random.choice(["jp_suivant", "jp_repondre", "jp_rien"])}, t)
        t += random.uniform(0, 3)
        if avant_phase == "question" and etat["phase"] == "revelation":
            reveles += 1
            verifier_revelation(etat, avant_points)
    else:
        raise AssertionError("partie figee")
    assert reveles == etat["nb_manches"] == len(etat["historique"])
    assert len(set(h["question"] for h in etat["historique"])) == etat["nb_manches"]
    final = etat["final"]
    assert sorted(final["classement"]) == list(range(n))
    meilleur = max(etat["points"])
    assert final["gagnants"] == [j for j in range(n) if etat["points"][j] == meilleur]
    return etat


def soiree():
    """Deux parties de suite dans un salon : pas de question reposee."""
    e1 = J.jp_initialiser(["A", "B"], 0, manches=15)
    e2 = J.jp_initialiser(["A", "B"], 0, manches=15, deja=e1["deja"])
    q1 = {q[1] for q in e1["tirage"]}
    q2 = {q[1] for q in e2["tirage"]}
    assert not q1 & q2, "une question reposee le meme soir"


def scenario():
    """Un cas a la main : 3 joueurs, reponses connues."""
    e = J.jp_initialiser(["A", "B", "C"], 0, manches=5)
    q = e["tirage"][0]
    q[2], q[3], q[4] = 100, "cl", None   # tolerance 5 %
    J.jp_action(e, 0, {"type": "jp_repondre", "valeur": 103}, 1)
    J.jp_action(e, 1, {"type": "jp_repondre", "valeur": "80"}, 1)
    assert e["phase"] == "question"
    pub = J.jp_etat_public(e, 1)
    assert pub["a_repondu"] == [True, True, False]
    J.jp_action(e, 2, {"type": "jp_repondre", "valeur": "250,5"}, 2)
    assert e["phase"] == "revelation"
    l = {x["joueur"]: x for x in e["resultat"]["lignes"]}
    assert l[0]["mille"] and l[0]["points"] == 4 and l[0]["boit"] == 0
    assert l[1]["points"] == 2 and l[1]["boit"] == 0
    assert l[2]["points"] == 1 and l[2]["boit"] == 2
    assert e["gorgees"] == [0, 0, 2]
    for j in range(3):
        J.jp_action(e, j, {"type": "jp_suivant"}, 3)
    assert e["phase"] == "question" and e["manche"] == 1
    # Personne ne repond : chacun boit, personne ne marque.
    J.jp_expirer(e, e["echeance"] + 1)
    assert e["phase"] == "revelation" and e["gorgees"] == [2, 2, 4]


if __name__ == "__main__":
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    verifier_banque()
    scenario()
    soiree()
    for k in range(nb):
        partie(random.Random(k).randint(J.JP_JOUEURS_MIN, J.JP_JOUEURS_MAX), k)
    print(f"Juste Pinte : {len(QUESTIONS)} questions, {nb} parties OK")
