// Partie complète de Qui de nous ? dans de vrais navigateurs (3 joueurs).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/quidenous_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. le réglage (5 cartes, paquet épicé, votes à découvert) part avec le lancement ;
//   2. chacun désigne un joueur et parie, voit son vote, peut le changer ; aucun
//      vote n'apparaît chez les autres avant la révélation ;
//   3. la révélation montre le désigné, les votes (à découvert), les paris ;
//   4. « Carte suivante » enchaîne jusqu'au classement final (titre de la soirée),
//      « Rejouer » relance ;
//   5. aucune erreur JavaScript.
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
    dernier = await test();
    if (dernier) return dernier;
    await attendre(250);
  }
  throw new Error('délai dépassé : ' + quoi);
}

(async () => {
  const navigateurs = [];
  const erreurs = [];
  const nouvelle = async () => {
    const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
    navigateurs.push(navigateur);
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
    await ctx.addInitScript(() => { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 100); });
    // Sans accès aux CDN (machine d essai fermée) : CDN_LOCAL=<dossier> sert three
    // (CDN_LOCAL/three/package/...) et qrious depuis des paquets npm décompressés.
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
    p.on('pageerror', e => erreurs.push(e.message));
    return p;
  };
  const capture = async (p, nom) => {
    if (!CAPTURES) return;
    fs.mkdirSync(CAPTURES, { recursive: true });
    await p.screenshot({ path: path.join(CAPTURES, nom + '.png') });
  };
  const resultats = [];
  const verifier = (cond, quoi) => { resultats.push([cond ? 'OK' : 'ÉCHEC', quoi]); };

  const noms = ['Alice', 'Bruno', 'Chloe'];
  const pages = [];
  for (let k = 0; k < noms.length; k++) pages.push(await nouvelle());
  const [A] = pages;
  await A.goto(SITE + 'quidenous-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'quidenous-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 20000, 'joueurs dans le salon');
  await attendre(800);

  // 1. Réglage : 5 cartes, paquet épicé, votes à découvert.
  await A.locator('button', { hasText: /^5$/ }).click();
  await A.locator('.mode', { hasText: 'Épicé' }).click();
  await A.locator('.mode', { hasText: 'À découvert' }).click();
  await capture(A, '1-attente');
  await A.locator('button', { hasText: /D.marrer la partie/ }).first().click();
  await attendreQue(async () => (await etat(code)).phase === 'vote', 15000, 'phase vote');
  const e0 = await etat(code);
  verifier(e0.qn.nb_manches === 5, '5 cartes demandées, ' + e0.qn.nb_manches + ' reçues');

  // Alice et Bruno désignent Chloe, Chloe désigne Alice.
  // Paris : Alice -> Chloe (bon), Bruno -> Bruno (mauvais), Chloe -> Chloe (bon).
  const cibles = [2, 2, 0];
  const paris = [2, 1, 2];
  // Les clics passent par la page (un clic Playwright attend que tout soit
  // immobile, trop lent ici avec trois scènes 3D pour tenir les 25 s du vote).
  const voter = async (p, cible, pari) => {
    const ok = await p.evaluate(([c, x]) => {
      const t1 = document.querySelector(`.tuile.cible[data-joueur="${c}"]`);
      const t2 = document.querySelector(`.tuile.pari[data-joueur="${x}"]`);
      if (!t1 || !t2) return 'tuiles absentes';
      t1.click(); t2.click();
      const b = [...document.querySelectorAll('#panneau button')].find(e => /Valider mon vote/.test(e.textContent));
      if (!b || b.disabled) return 'bouton Valider inactif';
      b.click();
      return true;
    }, [cible, pari]);
    if (ok !== true) { console.log('panneau :', await p.locator('#panneau').innerText()); throw new Error(ok); }
  };

  for (let m = 0; m < 5; m++) {
    await attendreQue(async () => { const x = await etat(code); return x.phase === 'vote' && x.qn.manche === m && x; }, 30000, 'carte ' + (m + 1))
      .catch(async err => { console.log(JSON.stringify(await etat(code))); for (const p of pages) console.log('---', await p.locator('#panneau').innerText()); throw err; });
    for (let k = 0; k < noms.length; k++) {
      const p = pages[k];
      await p.waitForSelector('.tuile.cible', { timeout: 30000 });
      if (m === 0) verifier(await p.locator(`.tuile.cible[data-joueur="${k}"]`).count() === 0, noms[k] + ' ne peut pas se désigner soi-même');
      if (k === 0 && m === 0) {
        // 2. Alice vote Bruno, voit son vote, puis change d'avis.
        await voter(p, 1, 2);
        await p.waitForSelector('.mon-vote', { timeout: 15000 });
        verifier(/Tu désignes Bruno/.test(await p.locator('.mon-vote').innerText()), 'Alice voit son vote');
        await capture(p, '2-vote');
        await p.evaluate(() => [...document.querySelectorAll('#panneau button')].find(e => /Changer/.test(e.textContent)).click());
        await p.waitForSelector('.tuile.cible');
        verifier(await p.locator('.tuile.cible.choisi[data-joueur="1"]').count() === 1, 'en changeant, son ancien choix est déjà coché');
        const ailleurs = await pages[1].evaluate(() => document.body.innerText);
        verifier(!/Tu désignes|A voté/.test(ailleurs), 'le vote d’Alice n’apparaît pas chez Bruno');
      }
      await voter(p, cibles[k], paris[k]);
    }
    try {
      await attendreQue(async () => (await etat(code)).phase === 'revelation', 40000, 'révélation ' + (m + 1));
    } catch (err) {
      for (let k = 0; k < noms.length; k++) console.log('--- ' + noms[k] + ' :\n' + await pages[k].locator('#panneau').innerText());
      throw err;
    }
    // 3. La révélation : après le vol des jetons, le désigné, les votes et les paris.
    await A.waitForSelector('.designe', { timeout: 30000 });
    const panneau = await A.locator('#panneau').innerText();
    verifier(/Chloe/.test(await A.locator('.designe').innerText()) && /A voté/.test(panneau) && /✓ Chloe/.test(panneau),
      'carte ' + (m + 1) + ' : Chloe désignée, votes à découvert, paris affichés');
    if (m === 0) { await capture(A, '3-revelation'); await capture(pages[2], '3-revelation-chloe'); }
    await Promise.all(pages.map(p => p.locator('button', { hasText: /Carte suivante|Voir le classement/ }).click({ timeout: 20000 }).catch(() => {})));
  }
  await attendreQue(async () => (await etat(code)).phase === 'fin', 30000, 'fin');
  await A.waitForFunction(() => /gagn/.test(document.getElementById('panneau').innerText), null, { timeout: 60000 }).catch(() => {});
  await A.evaluate(() => document.querySelectorAll('body > div').forEach(d => { if (/Touche pour fermer/.test(d.innerText)) d.click(); }));
  await attendre(1500);
  const fin = await A.locator('#panneau').innerText();
  verifier(/gagnent/.test(fin) && /Chloe/.test(fin) && /Titre de la soirée : Chloe/.test(fin), 'fin : Alice et Chloe gagnent, Chloe a le titre');
  const ef = await etat(code);
  verifier(JSON.stringify(ef.qn.points) === '[5,0,5]', 'points 5 / 0 / 5, reçus ' + JSON.stringify(ef.qn.points));
  verifier(JSON.stringify(ef.qn.gorgees) === '[0,7,10]', 'gorgées 0 / 7 / 10, reçues ' + JSON.stringify(ef.qn.gorgees));
  await capture(A, '4-fin');

  // 4. Rejouer.
  await A.locator('button', { hasText: /^Rejouer$/ }).click({ force: true });
  await attendreQue(async () => (await etat(code)).phase === 'vote', 15000, 'nouvelle partie');
  verifier(true, 'Rejouer relance une partie');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
