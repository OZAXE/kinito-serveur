"""
L Enquete comme couche parallele de la soiree (voir couches.py) : fait le
lien entre le moteur pur (enquete.py) et le reseau.

Les messages arrivent par la connexion « soiree » de chaque joueur
(/ws-soiree), distincte de sa place a une table de jeu : la page de
l Enquete peut ainsi s ouvrir par-dessus n importe quel jeu sans lui voler
sa connexion.

Apres chaque changement : l etat public a tous les abonnes, puis a CHACUN
sa vue privee (memes cles pour tous, voir enquete.vue_privee).
"""
import enquete as moteur
from couches import Couche, enregistrer


@enregistrer
class Enquete(Couche):
    prefixe = "enq"

    def __init__(self, code, etat):
        super().__init__(code)
        self.etat = etat

    @classmethod
    async def creer(cls, code, nom, message):
        if message.get("type") != "enq_creer":
            return None
        import time
        return cls(code, moteur.nouvelle_partie(code, nom, time.time()))

    def terminee(self):
        return self.etat["phase"] == "annulee"

    def exporter(self):
        return self.etat

    @classmethod
    def importer(cls, code, donnees):
        return cls(code, donnees)

    def prochaine_echeance(self):
        return moteur.prochaine_echeance(self.etat)

    async def _publier(self, ctx, evenements):
        for e in evenements:
            if e["a"] is None:
                await ctx.diffuser(e["msg"])
            else:
                await ctx.envoyer_nom(e["a"], e["msg"])
        await ctx.diffuser(moteur.vue_publique(self.etat, ctx.maintenant()))
        for nom in ctx.abonnes():
            await ctx.envoyer_nom(nom, moteur.vue_privee(self.etat, nom, ctx.maintenant()))
        ctx.sauvegarder()

    async def _envoyer_tout(self, ctx, nom):
        await ctx.envoyer_nom(nom, moteur.pack_public(moteur.PACKS[self.etat["univers"]]))
        await ctx.envoyer_nom(nom, moteur.vue_publique(self.etat, ctx.maintenant()))
        await ctx.envoyer_nom(nom, moteur.vue_privee(self.etat, nom, ctx.maintenant()))

    async def action(self, ctx, nom, message):
        if message.get("type") in ("enq_creer", "enq_etat"):
            # Deja creee (deux hotes en meme temps), ou simple demande d etat.
            if message.get("type") == "enq_creer" and moteur.cle_nom(nom) == moteur.cle_nom(self.etat["hote"]):
                await self._publier(ctx, [_tous_pack(self.etat)])
            else:
                await self._envoyer_tout(ctx, nom)
            return
        version = self.etat["version"]
        evenements = moteur.action(self.etat, nom, message, ctx.maintenant())
        if self.etat["version"] != version:
            await self._publier(ctx, evenements)
        else:
            for e in evenements:
                await ctx.envoyer_nom(e["a"] or nom, e["msg"])

    async def echeance(self, ctx):
        version = self.etat["version"]
        evenements = moteur.expirer(self.etat, ctx.maintenant())   # fin d ouverture, heure butoir, Temoin anonyme
        evenements += moteur.tic_paliers(self.etat, ctx.tables(), ctx.maintenant())
        evenements += moteur.tic_bots(self.etat, ctx.maintenant())    # joueurs fictifs (mode test)
        if self.etat["version"] != version:
            await self._publier(ctx, evenements)
        else:
            ctx.sauvegarder()       # le compteur de temps des paliers a avance

    async def arrivee(self, ctx, nom):
        await self._envoyer_tout(ctx, nom)

    async def evenement_jeu(self, ctx, evenement):
        """Une victoire, une fin de manche... a une table : points d enquete."""
        version = self.etat["version"]
        evenements = moteur.evenement_jeu(self.etat, evenement, ctx.maintenant())
        if self.etat["version"] != version:
            await self._publier(ctx, evenements)


def _tous_pack(etat):
    return {"a": None, "msg": moteur.pack_public(moteur.PACKS[etat["univers"]])}
