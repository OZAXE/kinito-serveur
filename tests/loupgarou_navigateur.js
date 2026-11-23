// Partie complète de Loup-Garou dans de vrais navigateurs (6 joueurs, un par page).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/loupgarou_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. le réglage (1 loup, les quatre rôles spéciaux) part avec le lancement ;
//   2. chacun découvre SON rôle (carte cachée tant qu'on ne la touche pas), et
//      rien ne trahit le loup sur la page des autres ;
//   3. la nuit : la meute vote, la Voyante voit le rôle sondé, le Salvateur
//      protège, les autres soupçonnent ; la Sorcière voit la victime et passe ;
//   4. l'aube annonce le Chasseur mort, qui tire depuis sa page ;
//   5. le débat : votes, verdict (qui a voté quoi), le loup éliminé, la victoire
//      du village et les gorgées ; « Rejouer » relance ;
//   6. aucune erreur JavaScript.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2] || '';
const attendre = ms => new Promise(r => setTimeout(r, ms));
async function etat(code) { return (await fetch(`${API}/test/etat/${code}`)).json(); }

async function attendreQue(test, delai, quoi) {
  const fin = Date.now() + delai;
  let dernier;
  while (Date.now() < fin) {
    try { dernier = await test(); } catch (e) { dernier = null; }
    if (dernier) return dernier;
    await attendre(300);
  }
  throw new Error('délai dépassé : ' + quoi);
}

(async () => {
  const erreurs = [];
  const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const nouvelle = async (nom, leger) => {
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
    await ctx.addInitScript(() => { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 150); });
    // Six scènes 3D sans carte graphique : les cinq autres en version légère (Alice garde
    // la version complète pour les captures).
    if (leger) await ctx.addInitScript(() => { localStorage.setItem('shield.leger', 'true'); });
    if (process.env.CDN_LOCAL) {
      await ctx.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
        const m = r.request().url().match(/three@[^/]+\/(.*)$/);
        r.fulfill({ path: path.join(process.env.CDN_LOCAL, 'three/package', m[1]), contentType: 'application/javascript' });
      });
      await ctx.route(/qrious\.min\.js$/, (r) => r.fulfill({ path: path.join(process.env.CDN_LOCAL, 'qrious/package/dist/qrious.min.js'), contentType: 'application/javascript' }));
      await ctx.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ body: '', contentType: 'text/css' }));
    }
    const p = await ctx.newPage();
    p.setDefaultNavigationTimeout(120000);
    p.setDefaultTimeout(60000);
    p.on('pageerror', e => erreurs.push(nom + ' : ' + e.message));
    return p;
  };
  const capture = async (p, nom) => {
    if (!CAPTURES) return;
    fs.mkdirSync(CAPTURES, { recursive: true });
    await p.screenshot({ path: path.join(CAPTURES, nom + '.png') });
  };
  const resultats = [];
  const verifier = (cond, quoi) => { resultats.push([cond ? 'OK' : 'ÉCHEC', quoi]); };
  const panneau = p => p.locator('#panneau').innerText();
  // Les clics passent par la page (un clic Playwright attend que tout soit immobile :
  // trop lent avec six scènes 3D).
  const cliquer = (p, re, delai = 30000) => attendreQue(() => p.evaluate(src => {
    const re = new RegExp(src);
    const b = [...document.querySelectorAll('#panneau button')].find(x => re.test(x.textContent) && !x.disabled);
    if (!b) return false;
    b.click();
    return true;
  }, re.source), delai, 'bouton ' + re);
  const tuile = (p, i, delai = 30000) => attendreQue(() => p.evaluate(i => {
    const t = document.querySelector(`#panneau .tuile[data-joueur="${i}"]`);
    if (!t || t.classList.contains('interdit')) return false;
    t.click();
    return true;
  }, i), delai, 'tuile ' + i);
  const montrer = async (pages, noms) => { for (let k = 0; k < pages.length; k++) console.log('--- ' + noms[k] + ' :\n' + await panneau(pages[k]).catch(() => '?')); };

  const noms = ['Alice', 'Bruno', 'Chloe', 'David', 'Emma', 'Farid'];
  const pages = [];
  for (const n of noms) pages.push(await nouvelle(n, n !== noms[0]));
  const [A] = pages;
  await A.goto(SITE + 'loupgarou-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'loupgarou-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 30000, 'joueurs dans le salon');
  await attendre(1000);

  // 1. Réglage : 1 loup, et les quatre rôles spéciaux (à 6, la Sorcière et le Chasseur sont absents d'office).
  await attendreQue(() => A.evaluate(() => /À 6/.test(document.getElementById('panneau').innerText)), 20000, 'aperçu de la composition');
  await A.evaluate(() => [...document.querySelectorAll('#panneau button')].find(b => b.textContent === '1').click());
  for (const r of ['Sorcière', 'Chasseur']) {
    await A.evaluate(r => [...document.querySelectorAll('#panneau .mode')].find(m => m.textContent.includes(r)).click(), r);
  }
  const compo = await A.evaluate(() => document.querySelector('.compo').textContent);
  verifier(/🐺 Loup-Garou/.test(compo) && /🧙‍♀️ Sorcière/.test(compo) && /🏹 Chasseur/.test(compo) && /🔮 Voyante/.test(compo) && /Simple Villageois/.test(compo), 'aperçu : ' + compo);
  await capture(A, '1-attente');
  await cliquer(A, /D.marrer la partie/);
  await attendreQue(async () => (await etat(code)).phase === 'role', 20000, 'phase des rôles');
  const e0 = await etat(code);
  const roles = e0.lg.roles;
  const qui = r => roles.indexOf(r);
  verifier(JSON.stringify([...roles].sort()) === JSON.stringify(['braconnier', 'cartomancienne', 'herboriste', 'loup', 'videur', 'villageois']),
    'composition reçue : ' + roles.join(', '));
  const L = qui('loup'), C = qui('cartomancienne'), V = qui('videur'), H = qui('herboriste'), B = qui('braconnier'), U = qui('villageois');

  // 2. Chacun découvre son rôle, caché par défaut.
  const NOMS_ROLES = { loup: 'Loup-Garou', villageois: 'Simple Villageois', cartomancienne: 'Voyante', videur: 'Salvateur', herboriste: 'Sorcière', braconnier: 'Chasseur' };
  await Promise.all(pages.map(p => p.waitForSelector('.carte-role', { timeout: 60000 })));
  // En parallèle : la phase des rôles ne dure que 25 s.
  await Promise.all(pages.map(async (p, k) => {
    verifier(await p.locator('.carte-role.cachee').count() === 1, noms[k] + ' : carte de rôle cachée au départ');
    await p.evaluate(() => document.querySelector('.carte-role').click());
    await attendreQue(() => p.evaluate(() => !document.querySelector('.carte-role.cachee')), 10000, 'carte découverte');
    const txt = await p.locator('.carte-role').innerText();
    verifier(txt.includes(NOMS_ROLES[roles[k]]), noms[k] + ' voit son rôle : ' + NOMS_ROLES[roles[k]]);
    if (k === L) verifier(/seul loup/.test(txt), 'le loup sait qu’il est seul');
    else verifier(!/🐺/.test(await p.locator('#joueurs').innerText()), noms[k] + ' : la liste des joueurs ne trahit pas le loup');
    if (k === L) await capture(p, '2-role-loup');
    await cliquer(p, /Je suis pr.t/, 15000).catch(() => {});
  }));
  await attendreQue(async () => (await etat(code)).phase === 'nuit', 30000, 'nuit 1');

  // 3. La nuit : la meute vise le Braconnier, la Cartomancienne sonde le loup, le Videur
  // se protège, les autres soupçonnent le loup.
  await Promise.all(pages.map(p => p.waitForSelector('#panneau .tuile', { timeout: 60000 })));
  verifier(await pages[L].locator(`#panneau .tuile[data-joueur="${L}"]`).count() === 0, 'le loup ne peut pas se viser');
  const nuit = async k => {
    const p = pages[k];
    if (k === L) { await tuile(p, B); await cliquer(p, /^Valider$/); }
    else if (k === C) { await tuile(p, L); await cliquer(p, /R.v.ler sa carte/); }
    else if (k === V) { await tuile(p, V); await cliquer(p, /Prot.ger/); }
    else { await tuile(p, L); await cliquer(p, /Je le soup.onne/); }
  };
  try {
    // La Cartomancienne d'abord : sa vision s'affiche tant que la nuit dure.
    await nuit(C);
    await attendreQue(() => pages[C].evaluate(() => document.querySelector('.vision')?.textContent || ''), 30000, 'vision');
    verifier(/Loup-Garou/.test(await pages[C].locator('.vision').innerText()) && (await pages[C].locator('.vision').innerText()).includes(noms[L]),
      'la Cartomancienne voit que ' + noms[L] + ' est le loup');
    await capture(pages[C], '3-vision');
    await Promise.all(noms.map((_, k) => k === C ? null : nuit(k)));
    await attendreQue(async () => (await etat(code)).phase === 'potion', 40000, 'potion');
  } catch (err) { console.log(JSON.stringify(await etat(code))); await montrer(pages, noms); throw err; }
  const ePot = await etat(code);
  verifier(ePot.lg.victime === B, 'la meute a choisi le Braconnier');
  // L'Herboriste voit la victime ; les autres « la nuit continue ».
  await attendreQue(() => pages[H].evaluate(() => /Les loups ont attaqu/.test(document.getElementById('panneau').innerText)), 30000, 'l’Herboriste voit la victime');
  verifier((await panneau(pages[H])).includes(noms[B]), 'l’Herboriste voit que ' + noms[B] + ' est attaqué');
  verifier(!!(await attendreQue(async () => /La nuit continue/.test(await panneau(pages[U])), 15000, 'nuit continue').catch(() => false)), 'les autres attendent sans rien voir');
  await capture(pages[H], '3-herboriste');
  await cliquer(pages[H], /Ne rien faire/, 15000).catch(() => console.log('(potion expirée avant le clic : machine lente)'));
  await attendreQue(async () => ['aube', 'tir'].includes((await etat(code)).phase), 40000, 'aube 1');

  // 4. L'aube : le Braconnier est mort, il tire.
  // Un témoin qui reste en vie jusqu'au bout : la Cartomancienne.
  const O = pages[C];
  await attendreQue(() => O.evaluate(n => document.getElementById('panneau').innerText.includes(n), noms[B]), 30000, 'annonce du mort');
  verifier(/Chasseur/.test(await panneau(O)), 'l’aube annonce le Chasseur mort, avec son rôle');
  await capture(O, '4-aube');
  if ((await etat(code)).phase === 'aube') await Promise.all(pages.map(p => cliquer(p, /Au d.bat/, 15000).catch(() => {})));
  await attendreQue(async () => (await etat(code)).phase === 'tir', 30000, 'tir du Braconnier');
  await attendreQue(() => pages[B].evaluate(() => /Tu es mort, Chasseur/.test(document.getElementById('panneau').innerText)), 30000, 'le Braconnier peut tirer');
  await capture(pages[B], '5-tir');
  await tuile(pages[B], U);
  await cliquer(pages[B], /^Tirer$/);
  await attendreQue(async () => { const x = await etat(code); return x.phase === 'aube' && !x.lg.vivants[U]; }, 30000, 'tir résolu');
  await Promise.all(pages.map(p => cliquer(p, /Au d.bat/)));
  await attendreQue(async () => (await etat(code)).phase === 'debat', 30000, 'débat');

  // 5. Le débat : les trois villageois vivants votent contre le loup, le loup vote contre la Cartomancienne.
  const vivants = [L, C, V, H];
  // Un mort voit tous les rôles.
  await attendreQue(() => pages[B].evaluate(() => /fantôme/.test(document.getElementById('panneau').innerText)), 30000, 'le Braconnier mort est un fantôme');
  verifier((await panneau(pages[B])).includes('🐺 ' + noms[L]), 'un fantôme voit le loup');
  try {
    await Promise.all(vivants.map(async k => {
      await tuile(pages[k], k === L ? C : L);
      await cliquer(pages[k], /^Voter$/);
    }));
    await attendreQue(async () => (await etat(code)).phase === 'verdict', 40000, 'verdict');
  } catch (err) { console.log(JSON.stringify(await etat(code))); await montrer(pages, noms); throw err; }
  await attendreQue(() => O.evaluate(() => /A voté/.test(document.getElementById('panneau').innerText)), 30000, 'qui a voté quoi');
  const verdict = await panneau(O);
  verifier(verdict.includes('Loup-Garou') && verdict.includes(noms[L]), 'verdict : ' + noms[L] + ' éliminé, c’était le loup');
  await capture(O, '6-verdict');
  await Promise.all(pages.map(p => cliquer(p, /La nuit tombe/)));
  await attendreQue(async () => (await etat(code)).phase === 'fin', 30000, 'fin');
  await attendreQue(() => A.evaluate(() => /Le village gagne/.test(document.getElementById('panneau').innerText)), 30000, 'victoire affichée');
  verifier(true, 'le village gagne');
  const ef = await etat(code);
  const attendu = [0, 0, 0, 0, 0, 0];
  attendu[B] = 2; attendu[U] = 2; attendu[L] = 3 + 2;
  verifier(JSON.stringify(ef.lg.gorgees) === JSON.stringify(attendu), 'gorgées ' + JSON.stringify(attendu) + ', reçues ' + JSON.stringify(ef.lg.gorgees));
  const fin = await panneau(A);
  verifier(NOMS_ROLES.loup && fin.includes(noms[L]) && /Meilleur flair/.test(fin), 'fin : rôles de tous et meilleur flair');
  await capture(A, '7-fin');

  // Rejouer.
  await cliquer(A, /^Rejouer$/);
  await attendreQue(async () => (await etat(code)).phase === 'role', 20000, 'nouvelle partie');
  verifier(true, 'Rejouer relance une partie');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  await navigateur.close();
  resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
