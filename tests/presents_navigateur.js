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
  const page = async (nom, table) => {
    const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1 });
    await ctx.addInitScript(([n, c, t]) => {
      if (sessionStorage.getItem('init')) return;     // une seule fois : la page change ensuite de table
      sessionStorage.setItem('init', '1');
      localStorage.setItem('shield.nom', JSON.stringify(n));
      localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: t || c, serveur: 'local', vu: Date.now() }));
    }, [nom, code, table]);
    const p = await ctx.newPage();
    await p.goto(SITE + 'index.html');
    return p;
  };
  console.log('== dans la soirée');
  const lea = await page('Lea');
  const seule = await attendreQue(() => lea.evaluate(() => { const b = document.querySelector('.presents'); return b && /Lea/.test(b.innerText) && b.innerText; }), 15000);
  verifier(!!seule && /Accueil/.test(seule), 'Léa sur l’accueil se voit dans « Dans la soirée » (Accueil)');
  const t2 = (await (await fetch(API + '/soiree/' + code + '/nouvelle-table')).json()).code;
  const max = await page('Max', t2);
  const arrivee = await attendreQue(() => lea.evaluate(() => { const a = document.querySelector('.presents .arrivee'); return a && a.textContent; }), 20000);
  verifier(arrivee === '👋 Max a rejoint la soirée', 'Max ouvre l’accueil : Léa voit « ' + (arrivee || '') + ' »');
  verifier(await lea.evaluate(() => /Max/.test(document.querySelector('.presents-liste').innerText)), '  et son prénom dans la liste');
  if (CAPTURES) await lea.screenshot({ path: CAPTURES + '/presents.png' });
  verifier(!!await attendreQue(() => max.evaluate(() => /Lea/.test(document.querySelector('.presents-liste').innerText) && !document.querySelector('.presents .arrivee')), 10000),
    'Max voit Léa déjà là, sans annonce d’arrivée (elle était là avant lui)');
  verifier(!!await attendreQue(() => lea.evaluate(() => /Table 2/.test(document.querySelector('.tables').innerText)), 20000),
    'la nouvelle table 2 apparaît toute seule dans la liste des tables de Léa');
  // Rejoindre la table de Max en touchant son prénom
  await attendreQue(() => lea.evaluate(() => { const b = [...document.querySelectorAll('.presents-liste button.present')].find(x => /Max/.test(x.textContent)); if (b) { b.click(); return true; } return false; }), 15000);
  const question = await attendreQue(() => lea.evaluate(() => { const d = document.querySelector('[role=dialog]'); return d && d.innerText; }), 5000);
  verifier(!!question && /table 2 de Max/.test(question), 'Léa touche le prénom de Max : « ' + (question || '').split('\n')[0] + ' »');
  await lea.evaluate(() => [...document.querySelectorAll('[role=dialog] button')].find(x => x.textContent === 'Rejoindre').click());
  verifier(!!await attendreQue(() => lea.evaluate((t) => JSON.parse(localStorage.getItem('shield.soiree')).table === t && /Table 2 · ta table/.test(document.querySelector('.tables').innerText), t2), 10000),
    '  elle confirme : la voilà à la table 2, avec Max');
  // Rejoindre une table en touchant sa ligne
  await lea.evaluate(() => [...document.querySelectorAll('.tables .table.cliquable')].find(x => /Table 1/.test(x.innerText)).click());
  await attendreQue(() => lea.evaluate(() => { const b = [...document.querySelectorAll('[role=dialog] button')].find(x => x.textContent === 'Rejoindre'); if (b) { b.click(); return true; } return false; }), 5000);
  verifier(!!await attendreQue(() => lea.evaluate((c) => JSON.parse(localStorage.getItem('shield.soiree')).table === c, code), 10000),
    'toucher la ligne d’une table (et confirmer) y fait asseoir aussi');
  // Animation de défaite (commune à tous les jeux, dans commun/soiree.js)
  const resultat = { type: 'table_resultat', jeu: 'kinito', evt: 'manche', gagnants: [], perdants: ['Lea'], gorgees: { Lea: 3 } };
  await lea.evaluate((m) => Soiree.traiterMessage(m, 'kinito'), resultat);
  await max.evaluate((m) => Soiree.traiterMessage(m, 'kinito'), resultat);
  const anim = await attendreQue(() => lea.evaluate(() => { const v = document.querySelector('.res-voile.perdu'); return v && v.innerText; }), 5000);
  verifier(!!anim && /Bois 3 gorgées/.test(anim), 'Léa perd la manche : animation de défaite (« ' + (anim || '').replace(/\n/g, ' ').slice(0, 60) + ' »)');
  if (CAPTURES) { await attendre(900); await lea.screenshot({ path: CAPTURES + '/defaite.png' }); }
  const bandeau = await attendreQue(() => max.evaluate(() => { const b = document.querySelector('.res-bandeau'); return b && b.textContent; }), 5000);
  verifier(!!bandeau && /Lea a perdu la manche/.test(bandeau), '  Max voit le bandeau : « ' + (bandeau || '') + ' »');
  verifier(!!await attendreQue(() => lea.evaluate(() => !document.querySelector('.res-voile')), 6000), '  l’animation disparaît seule, sans bloquer le jeu');
  await max.evaluate(() => Soiree.traiterMessage({ type: 'table_resultat', jeu: 'menteur', evt: 'victoire', gagnants: ['Max'], perdants: ['Lea'], gorgees: {} }, 'menteur'));
  verifier(!!await attendreQue(() => max.evaluate(() => !!document.querySelector('.res-voile.gagne')), 5000), 'fin de partie : le gagnant a droit à « Bien joué ! »');
  if (CAPTURES) { await attendre(900); await max.screenshot({ path: CAPTURES + '/victoire.png' }); }
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
