"""
Stockage durable des donnees qui doivent survivre a un redemarrage du serveur
(redeploiement Render, mise en veille, plantage).

Aujourd hui : les couches paralleles d une soiree (l Enquete). Les parties des
jeux eux-memes restent en memoire : elles durent quelques minutes et se
relancent en un clic.

Deux implementations, memes regles :
- StockageMemoire : pour les tests, et quand DATABASE_URL n est pas defini
  (le serveur tourne alors exactement comme avant) ;
- StockagePostgres : base Neon en production. Une seule table cle -> JSON.

Le serveur ne doit JAMAIS planter ni se figer a cause de la base : toute
erreur est journalisee, et la donnee reste en memoire en attendant.
"""
import asyncio
import json
import logging
import os

journal = logging.getLogger("stockage")

SCHEMA = """
create table if not exists donnees (
    cle text primary key,
    valeur jsonb not null,
    maj timestamptz not null default now()
)
"""


class StockageMemoire:
    durable = False

    def __init__(self):
        self._d = {}

    async def ouvrir(self):
        return True

    async def fermer(self):
        pass

    async def lire(self, cle):
        v = self._d.get(cle)
        return None if v is None else json.loads(v)

    async def ecrire(self, cle, valeur):
        # Serialise comme la vraie base : une valeur non JSON doit echouer
        # ici aussi, pas seulement en production.
        self._d[cle] = json.dumps(valeur)

    async def supprimer(self, cle):
        self._d.pop(cle, None)

    async def lister(self, prefixe):
        return [(c, json.loads(v)) for c, v in sorted(self._d.items()) if c.startswith(prefixe)]


class StockagePostgres:
    """
    Une seule connexion, utilisee par une requete a la fois. La charge est
    minuscule (un petit document par soiree, ecrit au plus une fois par
    seconde), un pool serait inutile.

    prepare_threshold=None : pas de requetes preparees, pour fonctionner
    aussi a travers le pooler de Neon (adresse en "-pooler").
    Neon met la base en veille apres quelques minutes sans requete et coupe
    alors la connexion : on se reconnecte et on reessaie une fois.
    """
    durable = True

    def __init__(self, url):
        self.url = url
        self._cnx = None
        self._verrou = asyncio.Lock()

    async def _connexion(self):
        import psycopg
        if self._cnx is None or self._cnx.closed:
            self._cnx = await psycopg.AsyncConnection.connect(
                self.url, autocommit=True, prepare_threshold=None, connect_timeout=10)
        return self._cnx

    async def _executer(self, sql, params=(), lignes=False):
        async with self._verrou:
            for essai in (1, 2):
                try:
                    cnx = await self._connexion()
                    async with cnx.cursor() as cur:
                        await cur.execute(sql, params)
                        return await cur.fetchall() if lignes else None
                except Exception:
                    if self._cnx is not None:
                        try:
                            await self._cnx.close()
                        except Exception:
                            pass
                    self._cnx = None
                    if essai == 2:
                        raise

    async def ouvrir(self):
        await self._executer(SCHEMA)
        return True

    async def fermer(self):
        if self._cnx is not None:
            try:
                await self._cnx.close()
            except Exception:
                pass
            self._cnx = None

    async def lire(self, cle):
        r = await self._executer("select valeur from donnees where cle = %s", (cle,), lignes=True)
        return r[0][0] if r else None

    async def ecrire(self, cle, valeur):
        await self._executer(
            "insert into donnees (cle, valeur, maj) values (%s, %s::jsonb, now()) "
            "on conflict (cle) do update set valeur = excluded.valeur, maj = now()",
            (cle, json.dumps(valeur)))

    async def supprimer(self, cle):
        await self._executer("delete from donnees where cle = %s", (cle,))

    async def lister(self, prefixe):
        motif = prefixe.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        r = await self._executer(
            "select cle, valeur from donnees where cle like %s order by cle", (motif,), lignes=True)
        return [(c, v) for c, v in r]


async def creer_stockage(url=None):
    """
    Postgres si une adresse est fournie (ou DATABASE_URL) et joignable,
    sinon la memoire. Ne leve jamais.
    """
    url = url if url is not None else os.environ.get("DATABASE_URL", "").strip()
    if url:
        s = StockagePostgres(url)
        try:
            await s.ouvrir()
            journal.warning("Stockage : base Postgres connectee.")
            return s
        except Exception:
            journal.exception("Stockage : base injoignable, repli en memoire.")
    return StockageMemoire()


class Sauvegarde:
    """
    Ecritures regroupees : on marque une cle comme modifiee (avec la fonction
    qui produit sa valeur), et une tache l ecrit au plus une fois par
    INTERVALLE. Une action de jeu ne attend donc jamais la base. Une ecriture
    ratee est retentee au tour suivant.
    """
    INTERVALLE = 1.0

    def __init__(self, stockage):
        self.stockage = stockage
        self._sales = {}        # cle -> fonction sans argument -> valeur (None = supprimer)
        self._tache = None

    def marquer(self, cle, produire):
        self._sales[cle] = produire
        if self._tache is None or self._tache.done():
            self._tache = asyncio.ensure_future(self._boucle())

    async def _boucle(self):
        while self._sales:
            await asyncio.sleep(self.INTERVALLE)
            await self.vider()

    async def vider(self):
        lot, self._sales = self._sales, {}
        for cle, produire in lot.items():
            try:
                valeur = produire()
                if valeur is None:
                    await self.stockage.supprimer(cle)
                else:
                    await self.stockage.ecrire(cle, valeur)
            except Exception:
                journal.exception("Sauvegarde ratee pour %s, nouvel essai bientot.", cle)
                # Une modification plus recente a pu etre marquee entre-temps.
                self._sales.setdefault(cle, produire)
