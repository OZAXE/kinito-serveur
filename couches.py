"""
Couches paralleles d une soiree.

Une soiree (un salon, un code) a un jeu en cours (ou aucun) et 0, 1 ou
plusieurs couches qui tournent A COTE des jeux, pendant toute la soiree :
la premiere est l Enquete. Une couche est optionnelle : une soiree sans
couche est une soiree normale.

Regles du contrat :
- une couche a un PREFIXE (ex. "enq") : les messages "enq_..." lui sont
  routes, quel que soit le jeu en cours ;
- elle connait les joueurs par leur NOM, jamais par leur index : l index
  change d un jeu a l autre, le nom reste ;
- les jeux ne la connaissent pas : ils signalent leurs evenements (fin de
  manche, victoire, fin de jeu) au serveur, qui les relaie a chaque couche ;
- elle survit au salon : un salon vide est libere apres un moment, la couche
  reste attachee au code jusqu a sa propre fin, et elle est sauvegardee
  (exporter / importer) pour survivre a un redemarrage du serveur ;
- elle ne fait jamais planter un jeu : le serveur attrape ses erreurs.

Evenement signale par un jeu (dict) :
  {"jeu": "kinito", "type": "manche" | "victoire" | "fin_jeu",
   "joueurs": [noms presents], "gagnants": [noms], "perdants": [noms],
   "details": {...propre au jeu}}
"""


class Contexte:
    """Ce que le serveur met a disposition d une couche (reseau, horloge, sauvegarde)."""

    def __init__(self, code, diffuser, envoyer_nom, noms_presents, jeu_en_cours,
                 sauvegarder, maintenant):
        self.code = code
        self.diffuser = diffuser            # async (message) -> a tous les connectes
        self.envoyer_nom = envoyer_nom      # async (nom, message) -> a ce joueur s il est la
        self.noms_presents = noms_presents  # () -> [noms connectes]
        self.jeu_en_cours = jeu_en_cours    # () -> nom du jeu ou None
        self.sauvegarder = sauvegarder      # () -> marque la couche a sauvegarder
        self.maintenant = maintenant        # () -> horodatage (secondes)


class Couche:
    prefixe = ""

    def __init__(self, code):
        self.code = code

    # --- cycle de vie -------------------------------------------------
    @classmethod
    async def creer(cls, code, nom, message):
        """
        Un message "<prefixe>_..." arrive alors que la soiree n a pas encore
        cette couche : renvoie la nouvelle couche, ou None si ce message ne
        doit pas en creer (seul un message de lancement le fait).
        """
        return None

    def terminee(self):
        """Vrai quand la couche peut etre oubliee (et effacee de la base)."""
        return False

    def exporter(self):
        """Etat complet en JSON (dict) pour la sauvegarde."""
        raise NotImplementedError

    @classmethod
    def importer(cls, code, donnees):
        raise NotImplementedError

    def prochaine_echeance(self):
        """Horodatage du prochain reveil voulu (minuteur), ou None."""
        return None

    # --- evenements ----------------------------------------------------
    async def echeance(self, ctx):
        """Appele a l heure donnee par prochaine_echeance."""

    async def action(self, ctx, nom, message):
        """Un message "<prefixe>_..." d un joueur."""

    async def arrivee(self, ctx, nom):
        """Un joueur (re)arrive dans le salon : lui renvoyer ce qu il doit voir."""

    async def evenement_jeu(self, ctx, evenement):
        """Un jeu du salon signale une fin de manche, une victoire, une fin de jeu."""


# Types de couches connus : prefixe -> classe. Rempli par les modules des
# couches (l Enquete s y ajoutera).
TYPES_COUCHES = {}


def enregistrer(classe):
    TYPES_COUCHES[classe.prefixe] = classe
    return classe
