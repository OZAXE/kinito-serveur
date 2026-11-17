"""
Le Bingo de soiree comme couche parallele (voir couches.py) : fait le lien
entre le moteur pur (bingo.py) et le reseau.

Independant d Enquete, mais il en reprend ce qui est commun a la soiree :
plafond de gorgees (un seul compteur par joueur et par heure, les deux jeux
s additionnent), unite de penalite et heure de fin.

Apres chaque changement : l etat public a tous les abonnes, puis a CHACUN
sa vue privee (memes cles pour tous, jamais la grille d un autre).
"""
import time

import bingo as moteur
from couches import Couche, enregistrer


@enregistrer
class Bingo(Couche):
    prefixe = "bng"

    def __init__(self, code, etat):
        super().__init__(code)
        self.etat = etat

    @classmethod
    async def creer(cls, code, nom, message):
        if message.get("type") != "bng_creer":
            return None
        etat = moteur.nouvelle_partie(code, nom, time.time())
        moteur.action(etat, nom, {"type": "bng_rejoindre"}, time.time())
        return cls(code, etat)

    def terminee(self):
        return self.etat["phase"] == "fermee"

    def exporter(self):
        return self.etat

    @classmethod
    def importer(cls, code, donnees):
        return cls(code, donnees)

    def prochaine_echeance(self):
        return moteur.prochaine_echeance(self.etat)

    def en_cours(self):
        return self.etat["phase"] == "jeu"

    def gorgees_heure(self, nom, maintenant):
        p = self.etat["j"].get(moteur.cle_nom(nom))
        return moteur.gorgees_heure(p, maintenant) if p else 0

    # --- ce que la soiree partage ------------------------------------
    def _synchroniser(self, ctx):
        commun = {"enquete": False, "jeux": [t["jeu"] for t in ctx.tables() if t.get("jeu")]}
        enq = ctx.couche("enq") if ctx.couche else None
        e = getattr(enq, "etat", None)
        if e and e.get("phase") not in ("fin", "annulee"):
            commun.update(enquete=True, plafond=e.get("plafond"), unite=e.get("unite"),
                          fin_a=e.get("fin_a"), vitesse=e.get("vitesse"))
        moteur.synchroniser(self.etat, commun)

    def _ailleurs(self, ctx):
        return (lambda nom, t: ctx.gorgees_ailleurs(nom, t)) if ctx.gorgees_ailleurs else None

    async def _publier(self, ctx, evenements):
        for e in evenements:
            if e["a"] is None:
                await ctx.diffuser(e["msg"])
            else:
                await ctx.envoyer_nom(e["a"], e["msg"])
        maintenant = ctx.maintenant()
        await ctx.diffuser(moteur.vue_publique(self.etat, maintenant))
        for nom in ctx.abonnes():
            await ctx.envoyer_nom(nom, moteur.vue_privee(self.etat, nom, maintenant))
        ctx.sauvegarder()

    async def _envoyer_tout(self, ctx, nom):
        maintenant = ctx.maintenant()
        await ctx.envoyer_nom(nom, moteur.vue_publique(self.etat, maintenant))
        await ctx.envoyer_nom(nom, moteur.vue_privee(self.etat, nom, maintenant))

    async def action(self, ctx, nom, message):
        self._synchroniser(ctx)
        if message.get("type") in ("bng_creer", "bng_etat"):
            # Deja creee (deux hotes en meme temps), ou simple demande d etat.
            await self._envoyer_tout(ctx, nom)
            return
        version = self.etat["version"]
        evenements = moteur.action(self.etat, nom, message, ctx.maintenant(),
                                   connectes=ctx.abonnes(), ailleurs=self._ailleurs(ctx))
        if self.etat["version"] != version:
            await self._publier(ctx, evenements)
        else:
            for e in evenements:
                await ctx.envoyer_nom(e["a"] or nom, e["msg"])

    async def echeance(self, ctx):
        self._synchroniser(ctx)
        version = self.etat["version"]
        evenements = moteur.expirer(self.etat, ctx.maintenant(), connectes=ctx.abonnes(),
                                    ailleurs=self._ailleurs(ctx))
        if self.etat["version"] != version:
            await self._publier(ctx, evenements)

    async def arrivee(self, ctx, nom):
        self._synchroniser(ctx)
        await self._envoyer_tout(ctx, nom)
