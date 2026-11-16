// Mode solo : un seul vrai joueur et des joueurs fictifs (tranche 9).
// Prérequis : serveur d'essai (tests/serveur_essai.py) et site sur le port 8765.
// Usage : node tests/solo_navigateur.js [dossier_captures]
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) { /* on réessaie */ } await attendre(250); }
  return null;
}
const clic = (page, texte) => attendreQue(() => page.evaluate((t) => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes(t) && !x.disabled); if (b) { b.click(); return true; } return false; }, texte), 15000);

(async () => {
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const nav = await chromium.launch();
  const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1 });
  await ctx.addInitScript(([c]) => {
    localStorage.setItem('shield.nom', JSON.stringify('Lea'));
    localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
  }, [code]);
  const p = await ctx.newPage();
  const erreurs = [];
  p.on('pageerror', e => erreurs.push(e.message));
  console.log('== solo');
  await p.goto(SITE + 'enquete.html');
  await clic(p, 'Ouvrir une enquête');
  await attendreQue(() => p.evaluate(() => document.querySelectorAll('.portrait').length === 12), 15000);
  await p.evaluate(() => document.querySelector('.portrait').click());
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="joueurs"]').click());
  for (let i = 0; i < 3; i++) { await clic(p, 'Ajouter un joueur fictif'); await attendre(500); }
  const liste = await attendreQue(() => p.evaluate(() => { const t = document.querySelector('.joueurs').innerText; return (t.match(/🤖/g) || []).length === 3 && t; }), 10000);
  verifier(!!liste, 'Léa seule ajoute 3 joueurs fictifs 🤖 : « ' + (liste || '').replace(/\n/g, ' ') + ' »');
  if (CAPTURES) await p.screenshot({ path: CAPTURES + '/solo-salle.png' });
  verifier(!!await clic(p, 'Lancer l’enquête'), '  4 joueurs : elle peut lancer l’enquête');
  verifier(!!await attendreQue(() => p.evaluate(() => !!document.querySelector('.plan3d canvas')), 60000), 'l’enquête commence, maison 3D');
  const r = await (await fetch(API + '/test/bots/' + code + '/3')).json();
  verifier(r.plis > 0, '  les joueurs fictifs jouent : ' + r.plis + ' plis reçus par eux');
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="fil"]').click());
  verifier(!!await attendreQue(() => p.evaluate(() => /pli/.test(document.querySelector('.fil').innerText)), 10000), '  Léa le voit dans le fil du narrateur');
  verifier(await p.evaluate(() => document.getElementById('dossier').children.length === 0), 'aucune trace de console dans la page tant que le dossier est fermé');
  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
