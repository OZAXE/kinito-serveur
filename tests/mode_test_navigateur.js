// Mode test (pseudo Admin01) : jouer seul contre des bots, dans chaque jeu.
// Prérequis : serveur d'essai (tests/serveur_essai.py) et site sur le port 8765.
// Usage : node tests/mode_test_navigateur.js [jeu ...]
//
// Pour chaque jeu : Admin01 ouvre la page, ajoute deux bots par le panneau
// de l'onglet 🧪, ils s'assoient à la table (côté serveur), Admin01 lance la
// partie, et les bots jouent tout seuls (jeux qui ont un cerveau de bot).
// On vérifie aussi qu'un prénom ordinaire n'a pas l'onglet.
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const PAGES = {
  kinito: 'kinito-3d-jeu.html', beerbattle: 'beerbattle-3d-jeu.html', buckshot: 'buckshot-3d-jeu.html',
  picolopoly: 'picolopoly-3d-jeu.html', menteur: 'menteur-3d-jeu.html', casino: 'casino-3d-jeu.html',
  undercover: 'undercover-3d-jeu.html', telephone: 'telephone-jeu.html', conquete: 'conquete-3d-jeu.html',
  justepinte: 'justepinte-3d-jeu.html', quidenous: 'quidenous-3d-jeu.html', tournee: 'tournee-3d-jeu.html',
  loupgarou: 'loupgarou-3d-jeu.html', pichenette: 'pichenette-3d-jeu.html',
};
// Jeux dont les bots savent jouer (bots.CERVEAUX côté serveur).
// (Casino exclu : la partie s'ouvre au hall, c'est Admin01 qui choisit la table avant que les bots jouent.)
const CERVEAUX = Object.keys(PAGES).filter(j => j !== 'casino');
// Bots ajoutés (le Loup-Garou se joue à 4 au moins).
const NB_BOTS = { loupgarou: 3 };
const JEUX = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(PAGES);
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) { /* on réessaie */ } await attendre(300); }
  return null;
}
const etat = async code => (await fetch(`${API}/test/etat/${code}`)).json();
const cliquerTexte = (cadre, re) => cadre.evaluate((src) => {
  const re = new RegExp(src);
  const b = [...document.querySelectorAll('button, [role=button]')].find(x => re.test(x.textContent) && !x.disabled && x.offsetParent !== null);
  if (b) { b.click(); return true; }
  return false;
}, re.source);

async function contexte(nav, nom, code) {
  const ctx = await nav.newContext({ viewport: { width: 900, height: 800 } });
  // Sans accès aux CDN : CDN_LOCAL=<dossier> (paquets npm three et qrious décompressés).
  if (process.env.CDN_LOCAL) {
    const path = require('path');
    await ctx.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => r.fulfill({
      path: path.join(process.env.CDN_LOCAL, 'three/package', r.request().url().match(/three@[^/]+\/(.*)$/)[1]), contentType: 'application/javascript' }));
    await ctx.route(/qrious\.min\.js$/, (r) => r.fulfill({ path: path.join(process.env.CDN_LOCAL, 'qrious/package/dist/qrious.min.js'), contentType: 'application/javascript' }));
    await ctx.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ body: '', contentType: 'text/css' }));
  }
  await ctx.addInitScript(([n, c]) => {
    if (!sessionStorage.getItem('init')) {
      sessionStorage.setItem('init', '1');
      localStorage.setItem('shield.nom', JSON.stringify(n));
      localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
    }
  }, [nom, code]);
  return ctx;
}

async function tester(nav, jeu) {
  console.log('== ' + jeu);
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const ctx = await contexte(nav, 'Admin01', code);
  const p = await ctx.newPage();
  p.setDefaultTimeout(60000);
  const erreurs = [];
  p.on('pageerror', e => erreurs.push('Admin01 : ' + e.message));
  ctx.on('page', q => q.on('pageerror', e => erreurs.push(e.message)));
  await p.goto(SITE + PAGES[jeu] + '?salon=' + code, { timeout: 120000 });
  const onglet = await attendreQue(() => p.evaluate(() => !!document.getElementById('onglet-test')), 30000);
  verifier(onglet, 'onglet 🧪 présent pour Admin01');
  if (!onglet) { await ctx.close(); return; }
  await p.click('#onglet-test');
  verifier(await p.evaluate(() => document.getElementById('mode-test').style.display === 'flex'), 'l’onglet ouvre le panneau des bots');
  const nbBots = NB_BOTS[jeu] || 2;
  for (let i = 0; i < nbBots; i++) { await cliquerTexte(p.mainFrame(), /Ajouter un bot/); await attendre(600); }
  const assis = await attendreQue(async () => { const e = await etat(code); const b = (e.joueurs || []).filter(j => j.bot && !j.parti); return b.length === nbBots && b.map(j => j.nom); }, 30000);
  verifier(!!assis, nbBots + ' bots s’assoient à la table : ' + JSON.stringify(assis));
  if (!assis) console.log('   panneau : ' + await p.evaluate(() => document.getElementById('mode-test').innerText.replace(/\s+/g, ' ')));
  const liste = await attendreQue(() => p.evaluate(n => document.querySelectorAll('#mode-test [title^="Faire partir"]').length === n, nbBots), 10000);
  verifier(!!liste, '  le panneau les liste');
  verifier(await p.evaluate(() => document.querySelectorAll('iframe').length === 0), '  plus de cadres de joueurs test (les bots sont sur le serveur)');
  await cliquerTexte(p.mainFrame(), /^Fermer$/);
  const lance = await attendreQue(async () => {
    if ((await etat(code)).en_partie) return true;
    await cliquerTexte(p.mainFrame(), /D.marrer la partie|D.marrer le quiz|Ouvrir le casino|Lancer la partie|Coup d.envoi/);
    await attendre(1500);
    await cliquerTexte(p.mainFrame(), /^Remplacer$/);
    return false;
  }, 60000);
  verifier(lance, 'Admin01 lance la partie à ' + (nbBots + 1));
  if (CERVEAUX.includes(jeu)) {
    // Admin01 joue aussi (par le cerveau de bot, route d essai) : certains jeux commencent par lui.
    const joue = await attendreQue(async () => {
      if ((await etat(code)).joueurs.some(j => j.bot && j.actions > 0)) return true;
      await fetch(`${API}/test/jouer/${code}/Admin01`);
      return false;
    }, 60000);
    verifier(!!joue, '  les bots jouent tout seuls');
  }
  if (jeu === JEUX[0] && JEUX.length > 1) {
    // La table change de jeu : les bots restent assis.
    const suivant = JEUX[1];
    await p.goto(SITE + PAGES[suivant] + '?salon=' + code, { timeout: 120000 });
    await attendre(3000);
    const e = await etat(code);
    verifier(e.joueurs.filter(j => j.bot && !j.parti).length === nbBots, 'changement de jeu (' + suivant + ') : les ' + nbBots + ' bots restent à la table');
  }
  if (erreurs.length) console.log('   erreurs JS : ' + erreurs.slice(0, 5).join(' | '));
  verifier(erreurs.length === 0, 'aucune erreur JavaScript');
  await ctx.close();
}

(async () => {
  const nav = await chromium.launch();
  // Un prénom ordinaire n'a pas le mode test.
  {
    const code = (await (await fetch(API + '/creer-salon')).json()).code;
    const ctx = await contexte(nav, 'Lea', code);
    const p = await ctx.newPage();
    await p.goto(SITE + 'menteur-3d-jeu.html?salon=' + code, { timeout: 120000 });
    await attendreQue(async () => (await etat(code)).joueurs?.length === 1, 20000);
    await attendre(1500);
    verifier(await p.evaluate(() => !document.getElementById('onglet-test')), 'pas d’onglet 🧪 pour un prénom ordinaire');
    await ctx.close();
  }
  for (const jeu of JEUX) {
    try { await tester(nav, jeu); } catch (e) { verifier(false, jeu + ' : ' + e.message); }
  }
  await nav.close();
  console.log(echecs ? echecs + ' ÉCHEC(S)' : 'Tout passe.');
  process.exit(echecs ? 1 : 0);
})();
