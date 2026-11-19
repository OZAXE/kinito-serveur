// Tests navigateur des departs et retours, sur les vraies pages des jeux 3D.
//
// Prerequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/navigateur.js [kinito|beerbattle|buckshot|picolopoly|menteur|casino|undercover ...]
//
// Pour chaque jeu : trois joueurs (trois navigateurs separes) creent et
// rejoignent un salon, lancent la partie, puis on fait vivre a un joueur ce
// qui arrive en soiree, et on verifie qu il revient a SA place, seul, sans
// rien toucher :
//   1. coupure de la connexion par le reseau
//   2. telephone muet (plus rien ne part) : le serveur le declare parti, puis
//      il revient tout seul quand ca repart
//   3. retour sur la page apres une veille : il redemande l etat de la partie
//   4. meme prenom depuis un autre navigateur : il reprend la place, l ancien
//      ecran affiche « Tu joues sur un autre écran » et ne se bat pas pour elle
//   5. aucune erreur JavaScript
const { chromium } = require('playwright');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const JEUX = process.argv.slice(2).length ? process.argv.slice(2)
  : ['kinito', 'beerbattle', 'buckshot', 'picolopoly', 'menteur', 'casino', 'undercover'];

const attendre = ms => new Promise(r => setTimeout(r, ms));
async function etat(code) { return (await fetch(`${API}/test/etat/${code}`)).json(); }

// Coupe les envois de la page quand window.__muet est vrai (telephone muet).
const SCRIPT_MUET = () => {
  const envoi = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { if (!window.__muet) return envoi.call(this, d); };
};

async function attendreQue(test, delai, quoi) {
  const fin = Date.now() + delai;
  let dernier;
  while (Date.now() < fin) {
    dernier = await test();
    if (dernier) return dernier;
    await attendre(250);
  }
  throw new Error('délai dépassé : ' + quoi);
}

async function tester(navigateur, jeu) {
  const erreurs = [];
  const nouvelle = async () => {
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 } });
    await ctx.addInitScript(SCRIPT_MUET);
    const p = await ctx.newPage();
    // Sans carte graphique, chaque scène 3D ouverte ralentit les suivantes.
    p.setDefaultNavigationTimeout(120000);
    p.setDefaultTimeout(60000);
    p.on('pageerror', e => erreurs.push(e.message));
    return p;
  };
  const page = jeu + '-3d-jeu.html';
  const A = await nouvelle(), B = await nouvelle(), C = await nouvelle();

  await A.goto(SITE + page);
  await A.fill('#pseudo', 'Alice');
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  // Le code n'est plus affiché dans le jeu (il est sur l'accueil de la soirée) : on le lit dans la page.
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);

  for (const [p, nom] of [[B, 'Bruno'], [C, 'Chloe']]) {
    await p.goto(SITE + page + '?salon=' + code);
    await p.fill('#pseudo', nom);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === 3, 15000, '3 joueurs dans le salon');
  await A.locator('button', { hasText: /D.marrer la partie|Ouvrir le casino/ }).first().click();
  await attendreQue(async () => (await etat(code)).en_partie, 15000, 'partie lancée');

  const verifierTable = async (quand) => {
    const e = await etat(code);
    const noms = e.joueurs.map(j => j.nom);
    if (new Set(noms).size !== noms.length) throw new Error(`${quand} : doublon ${JSON.stringify(noms)}`);
    if (noms.length !== 3) throw new Error(`${quand} : ${noms.length} places ${JSON.stringify(noms)}`);
    return e;
  };
  const bruno = async () => {
    const e = await etat(code);
    const b = e.joueurs.find(j => j.nom === 'Bruno');
    if (!b) throw new Error('Bruno absent de la table : ' + JSON.stringify(e.joueurs));
    return b;
  };
  const resultats = [];
  const etape = async (nom, f) => {
    try { await f(); resultats.push(['OK', nom]); }
    catch (e) { resultats.push(['ÉCHEC', nom + ' : ' + e.message]); }
  };

  await etape('battement de cœur envoyé', async () => {
    await attendreQue(async () => (await bruno()).battement, 12000, 'battement reçu');
  });

  await etape('coupure réseau → reconnexion automatique', async () => {
    await fetch(`${API}/test/couper/${code}/Bruno`);
    await attendreQue(async () => (await bruno()).parti, 5000, 'Bruno vu parti');
    await attendreQue(async () => { const b = await bruno(); return b && !b.parti; }, 15000, 'Bruno revenu seul');
    await verifierTable('après coupure');
  });

  await etape('téléphone muet → déclaré parti, puis revient seul', async () => {
    await B.evaluate(() => { window.__muet = true; });
    try {
      await attendreQue(async () => (await bruno()).parti, 25000, 'Bruno déclaré parti');
    } finally {
      await B.evaluate(() => { window.__muet = false; });
    }
    await attendreQue(async () => { const b = await bruno(); return b && !b.parti && b.battement; }, 20000, 'Bruno revenu');
    await verifierTable('après silence');
  });

  await etape('retour sur la page → resynchronisation', async () => {
    const avant = (await etat(code)).resync;
    await B.evaluate(() => {
      Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await attendre(2500);
    await B.evaluate(() => {
      Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await attendreQue(async () => (await etat(code)).resync > avant, 5000, 'resync reçu par le serveur');
  });

  await etape('même prénom ailleurs → reprend la place, pas de doublon', async () => {
    const D = await nouvelle();
    await D.goto(SITE + page + '?salon=' + code);
    await D.fill('#pseudo', 'Bruno');
    await D.locator('button', { hasText: /^Rejoindre$/ }).first().click();
    await attendreQue(async () => /autre .cran/i.test(await B.evaluate(() => document.body.innerText)), 10000,
      'ancien écran prévenu');
    await attendre(4000);       // l ancien ecran ne doit pas reprendre la place
    const e = await verifierTable('après reprise');
    if (e.joueurs.find(j => j.nom === 'Bruno').parti) throw new Error('Bruno marqué parti');
    if (/autre .cran/i.test(await D.evaluate(() => document.body.innerText))) throw new Error('le nouvel écran a été éjecté');
    await D.context().close();
  });

  await etape('aucune erreur JavaScript', async () => {
    if (erreurs.length) throw new Error(JSON.stringify([...new Set(erreurs)].slice(0, 5)));
  });

  for (const p of [A, B, C]) await p.context().close();
  return resultats;
}

(async () => {
  const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  let echecs = 0;
  for (const jeu of JEUX) {
    console.log('== ' + jeu);
    let res;
    try { res = await tester(navigateur, jeu); }
    catch (e) { res = [['ÉCHEC', 'mise en place : ' + e.message]]; }
    for (const [s, n] of res) { console.log(`   ${s.padEnd(5)} ${n}`); if (s !== 'OK') echecs++; }
  }
  await navigateur.close();
  process.exit(echecs ? 1 : 0);
})();
