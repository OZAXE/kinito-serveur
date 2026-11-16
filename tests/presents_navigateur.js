// Accueil : la liste « Dans la soirée » (qui est là, et où) et l'annonce des arrivées.
// Prérequis : serveur d'essai (tests/serveur_essai.py) et site sur le port 8765.
// Usage : node tests/presents_navigateur.js [dossier_captures]
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) { /* on réessaie */ } await attendre(300); }
  return null;
}

(async () => {
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const nav = await chromium.launch();
  const page = async (nom) => {
    const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1 });
    await ctx.addInitScript(([n, c]) => {
      localStorage.setItem('shield.nom', JSON.stringify(n));
      localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
    }, [nom, code]);
    const p = await ctx.newPage();
    await p.goto(SITE + 'index.html');
    return p;
  };
  console.log('== dans la soirée');
  const lea = await page('Lea');
  const seule = await attendreQue(() => lea.evaluate(() => { const b = document.querySelector('.presents'); return b && /Lea/.test(b.innerText) && b.innerText; }), 15000);
  verifier(!!seule && /Accueil/.test(seule), 'Léa sur l’accueil se voit dans « Dans la soirée » (Accueil)');
  const max = await page('Max');
  const arrivee = await attendreQue(() => lea.evaluate(() => { const a = document.querySelector('.presents .arrivee'); return a && a.textContent; }), 20000);
  verifier(arrivee === '👋 Max a rejoint la soirée', 'Max ouvre l’accueil : Léa voit « ' + (arrivee || '') + ' »');
  verifier(await lea.evaluate(() => /Max/.test(document.querySelector('.presents-liste').innerText)), '  et son prénom dans la liste');
  if (CAPTURES) await lea.screenshot({ path: CAPTURES + '/presents.png' });
  verifier(!!await attendreQue(() => max.evaluate(() => /Lea/.test(document.querySelector('.presents-liste').innerText) && !document.querySelector('.presents .arrivee')), 10000),
    'Max voit Léa déjà là, sans annonce d’arrivée (elle était là avant lui)');
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
