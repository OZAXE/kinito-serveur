// Mode test (pseudo Admin01) : jouer seul avec des joueurs test, dans chaque jeu.
// Prérequis : serveur d'essai (tests/serveur_essai.py) et site sur le port 8765.
// Usage : node tests/mode_test_navigateur.js [jeu ...]
//
// Pour chaque jeu : Admin01 ouvre la page, ajoute deux joueurs test par
// l'onglet 🧪, ils rejoignent la table (3 joueurs côté serveur), Admin01
// lance la partie, puis joue à la place d'un bot (sa page est bien dans la
// partie). On vérifie aussi que la session d'Admin01 n'est pas écrasée par
// celles des bots, et qu'un prénom ordinaire n'a pas l'onglet.
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const PAGES = {
  kinito: 'kinito-3d-jeu.html', beerbattle: 'beerbattle-3d-jeu.html', buckshot: 'buckshot-3d-jeu.html',
  picolopoly: 'picolopoly-3d-jeu.html', menteur: 'menteur-3d-jeu.html', casino: 'casino-3d-jeu.html',
  undercover: 'undercover-3d-jeu.html', telephone: 'telephone-jeu.html', conquete: 'conquete-3d-jeu.html',
  justepinte: 'justepinte-3d-jeu.html', quidenous: 'quidenous-3d-jeu.html', tournee: 'tournee-3d-jeu.html',
};
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
  for (let i = 0; i < 2; i++) { await cliquerTexte(p.mainFrame(), /＋ Bot/); await attendre(400); }
  const trois = await attendreQue(async () => { const e = await etat(code); return e.joueurs && e.joueurs.filter(j => !j.parti).length === 3 && e.joueurs.map(j => j.nom); }, 60000);
  verifier(!!trois, 'deux joueurs test rejoignent la table : ' + JSON.stringify(trois));
  const session = await p.evaluate(() => Object.keys(localStorage).filter(k => /session/.test(k) && !k.startsWith('siege:')).map(k => [k, localStorage.getItem(k)]));
  verifier(session.every(([, v]) => !/Bot/.test(v)), 'la session d’Admin01 n’est pas écrasée par les bots');
  verifier(await p.evaluate(() => JSON.parse(localStorage.getItem('shield.nom')) === 'Admin01'), 'le prénom d’Admin01 reste le sien');
  // Retour à « Moi » et lancement
  await cliquerTexte(p.mainFrame(), /^Moi$/);
  verifier(await p.evaluate(() => document.getElementById('mode-test').style.visibility === 'hidden'), '« Moi » montre sa propre partie');
  const lance = await attendreQue(async () => {
    if ((await etat(code)).en_partie) return true;
    await cliquerTexte(p.mainFrame(), /D.marrer la partie|D.marrer le quiz|Ouvrir le casino|Lancer la partie/);
    await attendre(1500);
    await cliquerTexte(p.mainFrame(), /^Remplacer$/);
    return false;
  }, 60000);
  verifier(lance, 'Admin01 lance la partie à 3');
  // Jouer à la place de Bot 1
  await p.click('#onglet-test');
  await cliquerTexte(p.mainFrame(), /^Bot 1$/);
  const cadre = p.frames().find(f => /siege_test=Bot(%20| )1/.test(f.url()));
  verifier(!!cadre, 'la page de Bot 1 est là');
  if (cadre) {
    const visible = await p.evaluate(() => { const c = document.querySelector('iframe[data-siege="Bot 1"]'); return c && c.getBoundingClientRect().height > 300; });
    verifier(visible, '  elle s’affiche en grand quand on touche « Bot 1 »');
    const nom = await cadre.evaluate(() => Soiree.nom());
    verifier(nom === 'Bot 1', '  elle joue sous le nom « Bot 1 » (' + nom + ')');
  }
  if (jeu === JEUX[0] && JEUX.length > 1) {
    // La table change de jeu : la nouvelle page recrée les bots, qui la rejoignent.
    const suivant = JEUX[1];
    await p.goto(SITE + PAGES[suivant] + '?salon=' + code, { timeout: 120000 });
    const suivent = await attendreQue(() => p.evaluate(() => document.querySelectorAll('iframe[data-siege]').length === 2), 30000);
    verifier(suivent, 'changement de jeu (' + suivant + ') : les deux bots suivent');
    const tous = await attendreQue(async () => {
      const ifr = p.frames().filter(f => /siege_test=/.test(f.url()));
      return ifr.length === 2 && (await Promise.all(ifr.map(f => f.evaluate(() => !!window.Soiree).catch(() => false)))).every(Boolean);
    }, 60000);
    verifier(tous, '  et leurs pages sont chargées');
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
