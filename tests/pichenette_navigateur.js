// Partie de Pichenette FC dans de vrais navigateurs (2 contre 2, 4 joueurs).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/pichenette_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. le lancement (3 buts), les équipes 2 contre 2, chacun voit son but en bas (les Brunes ont le terrain retourné) ;
//   2. les formations : chaque équipe choisit et valide, le coup d'envoi part de la défense choisie ;
//   3. la visée au doigt (lance-pierre) : la flèche apparaît, le coéquipier la voit, pas l'adversaire ;
//      une capsule déjà prise par un coéquipier ne se prend pas ;
//   4. tout le monde valide : le tir part, chaque page rejoue la trajectoire et finit aux positions du serveur ;
//   5. trois buts (bouchon placé devant la cage par /test/pf_preparer) : gorgées, victoire, panneau de fin, Rejouer ;
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
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true });
    await ctx.addInitScript(() => { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 60); });
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
  const cliquer = (p, re) => p.evaluate(src => {
    const r = new RegExp(src);
    const b = [...document.querySelectorAll('#panneau button, #panneau .formation')].find(x => r.test(x.textContent.trim()) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, re.source);
  // Le lance-pierre : on pose le doigt sur la capsule et on tire « vers soi » (vers le bas de l'écran).
  const tirer = async (p, k, pixels, dxPix = 0) => {
    const c = await p.evaluate(k => window.pfTest.ecran(k), k);
    await p.mouse.move(c.x, c.y);
    await p.mouse.down();
    for (let s = 1; s <= 4; s++) await p.mouse.move(c.x - dxPix * s / 4, c.y + pixels * s / 4);
    await p.mouse.up();
  };

  const noms = ['Alice', 'Bruno', 'Chloe', 'David'];
  const pages = [];
  for (let k = 0; k < noms.length; k++) pages.push(await nouvelle());
  const [A] = pages;
  await A.goto(SITE + 'pichenette-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'pichenette-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 30000, 'joueurs dans le salon');
  await attendre(1000);
  await capture(A, '1-attente');

  // 1. Lancement : 3 buts.
  await cliquer(A, /^3$/);
  await cliquer(A, /Coup d.envoi/);
  await attendreQue(async () => (await etat(code)).phase === 'formation', 20000, 'phase formation');
  let e = await etat(code);
  verifier(e.pf.buts_victoire === 3, 'partie en 3 buts');
  verifier(e.pf.equipes[0].length === 2 && e.pf.equipes[1].length === 2 && e.pf.spectateurs.length === 0, 'équipes 2 contre 2');
  const equipeDe = i => (e.pf.equipes[0].includes(i) ? 0 : 1);
  const P = i => pages[i];

  // 2. Formations : les Blondes prennent le Bunker en défense, les Brunes la Herse.
  await Promise.all(pages.map(p => p.waitForSelector('.formation', { timeout: 60000 })));
  const chef = [e.pf.equipes[0][0], e.pf.equipes[1][0]];
  const autre = [e.pf.equipes[0][1], e.pf.equipes[1][1]];
  await P(chef[0]).evaluate(() => { const t = [...document.querySelectorAll('.formations')][1]; [...t.children].find(x => /Bunker/.test(x.textContent)).click(); });
  await P(chef[1]).evaluate(() => { const t = [...document.querySelectorAll('.formations')][1]; [...t.children].find(x => /Herse/.test(x.textContent)).click(); });
  await attendreQue(async () => { const x = await etat(code); return x.pf.formations && x.pf.formations[0].defense === 'bunker' && x.pf.formations[1].defense === 'herse'; }, 15000, 'formations choisies');
  // Le coéquipier voit le choix de son chef (formation de son équipe surlignée).
  const vuParCoequipier = await P(autre[0]).waitForFunction(() => {
    const t = [...document.querySelectorAll('.formations')][1];
    const c = t && [...t.children].find(x => x.classList.contains('choisi'));
    return c && /Bunker/.test(c.textContent);
  }, null, { timeout: 20000 }).then(() => true).catch(() => false);
  verifier(vuParCoequipier, 'le coéquipier voit la formation choisie par son équipe');
  await capture(P(chef[0]), '2-formations');
  await cliquer(P(chef[0]), /Valider les formations/);
  await cliquer(P(autre[1]), /Valider les formations/);
  await attendreQue(async () => (await etat(code)).phase === 'visee', 20000, 'coup d envoi');
  e = await etat(code);
  verifier(Math.abs(e.pf.palets[0][1] - -0.73) < 1e-6 && Math.abs(e.pf.palets[6][0] - 0.1) < 1e-6, 'coup d envoi depuis les défenses choisies (Bunker, Herse)');

  // Orientation : les Brunes ont le terrain retourné.
  await attendre(1500);
  const orient = await Promise.all(pages.map(p => p.evaluate(() => window.pfTest.orientation())));
  verifier(orient.every((o, i) => Math.abs(o - (equipeDe(i) === 1 ? Math.PI : 0)) < 1e-6), 'chacun voit son but en bas');

  // 3. Visée : chaque joueur tire sa capsule de devant (la 5 ou la 4 de son équipe).
  await Promise.all(pages.map(p => p.waitForFunction(() => /Tire 1 capsule/.test(document.getElementById('panneau').innerText), null, { timeout: 30000 })));
  const capsuleDe = i => { const eq = equipeDe(i); return eq * 6 + (e.pf.equipes[eq][0] === i ? 5 : 4); };
  const pret = p => p.waitForFunction(() => { const v = window.pfTest.visee(); return v.enVisee && !v.file && !v.anims && v.camera < 0.01; }, null, { timeout: 40000 }).catch(() => null);
  await Promise.all(pages.map(pret));
  await tirer(P(chef[0]), capsuleDe(chef[0]), 70, 15);
  await attendreQue(async () => ((await etat(code)).pf.visees[String(chef[0])] || []).length === 1, 10000, 'visée reçue');
  const b0 = await P(chef[0]).evaluate(() => window.pfTest.brouillons());
  verifier(b0.length === 1 && b0[0].palet === capsuleDe(chef[0]) && b0[0].dy > 0.5, 'la visée part vers le but adverse (tirer vers soi) ' + JSON.stringify(b0));
  // Le coéquipier voit la flèche, l'adversaire non.
  const flecheCoeq = await P(autre[0]).waitForFunction(() => window.pfTest.fleches() >= 1, null, { timeout: 15000 }).then(() => true).catch(() => false);
  verifier(flecheCoeq, 'le coéquipier voit la flèche de son équipe');
  verifier(await P(chef[1]).evaluate(() => window.pfTest.fleches()) === 0, 'l adversaire ne voit aucune flèche');
  // Prendre la capsule du coéquipier : impossible (la page ne la propose pas).
  await tirer(P(autre[0]), capsuleDe(chef[0]), 70);
  await attendre(800);
  verifier(!((await etat(code)).pf.visees[String(autre[0])] || []).length, 'la capsule d un coéquipier ne se prend pas');
  await capture(P(chef[0]), '3-visee');
  // Tout le monde vise puis valide.
  for (const i of [autre[0], chef[1], autre[1]]) await tirer(P(i), capsuleDe(i), 60, (i % 2 ? 20 : -20));
  await attendreQue(async () => { const x = await etat(code); return [autre[0], chef[1], autre[1]].every(i => (x.pf.visees[String(i)] || []).length === 1); }, 15000, 'toutes les visées')
    .catch(async err => { const x = await etat(code); console.log(JSON.stringify({ visees: x.pf.visees, eq: x.pf.equipes, phase: x.phase, palets: x.pf.palets })); for (const p of pages) console.log('---', await p.locator('#panneau').innerText(), await p.locator('#rappel').innerText()); throw err; });
  const tour1 = (await etat(code)).pf.tour;
  await Promise.all(pages.map(p => cliquer(p, /Valider mes tirs/)));
  // 4. Le tir : replay, puis tour suivant, et chaque page finit aux positions du serveur.
  await attendreQue(async () => { const x = await etat(code); return x.phase === 'visee' && x.pf.tour === tour1 + 1; }, 40000, 'tour 2');
  e = await etat(code);
  await Promise.all(pages.map(p => p.waitForFunction(() => !window.pfTest.replay() && /Tire 1 capsule/.test(document.getElementById('panneau').innerText), null, { timeout: 40000 }).catch(() => null)));
  await attendre(1500);
  const ecarts = await Promise.all(pages.map(p => p.evaluate(attendu => {
    const vu = window.pfTest.positions();
    return Math.max(...attendu.map((q, k) => Math.hypot(q[0] - vu[k][0], q[1] - vu[k][1])));
  }, e.pf.palets)));
  verifier(ecarts.every(x => x < 0.002), 'chaque page a rejoué le tir et finit aux positions du serveur (écart max ' + Math.max(...ecarts).toFixed(4) + ')');

  // 5. Trois buts des Blondes : le chef des Blondes tire la capsule 5 droit devant, les autres passent.
  for (let but = 1; but <= 3; but++) {
    const avant = await etat(code);
    const g0 = avant.pf.gorgees.slice();
    const prep = await (await fetch(`${API}/test/pf_preparer/${code}`)).json();
    verifier(prep.ok, 'bouchon placé devant la cage (but ' + but + ')');
    // La page peut avoir du retard (replay du but précédent, annonce) : on attend qu'elle soit à jour.
    await P(chef[0]).waitForFunction(() => {
      const v = window.pfTest.visee(), c = window.pfTest.ecran(5), b = window.pfTest.ecran('balle');
      return v.enVisee && !v.file && !v.anims && v.camera < 0.01 && c && b && Math.abs(c.x - b.x) < 3 && b.y < c.y;
    }, null, { timeout: 40000 }).catch(() => null);
    await attendre(500);
    await tirer(P(chef[0]), 5, 160);
    await attendreQue(async () => ((await etat(code)).pf.visees[String(chef[0])] || []).length === 1, 10000, 'visée du but')
      .catch(async err => { console.log(JSON.stringify((await etat(code)).pf), await P(chef[0]).evaluate(() => JSON.stringify({ b: window.pfTest.brouillons(), c: window.pfTest.ecran(5), bal: window.pfTest.ecran('balle'), pos: window.pfTest.positions()[5], r: document.getElementById('rappel').innerText, p: document.getElementById('panneau').innerText }))); await capture(P(chef[0]), 'x-echec'); throw err; });
    await cliquer(P(chef[0]), /Valider mes tirs/);
    for (const i of [autre[0], chef[1], autre[1]]) await cliquer(P(i), /Passer mon tour/);
    const x = await attendreQue(async () => { const y = await etat(code); return (y.pf.score[0] === but) && y; }, 30000, 'but ' + but)
      .catch(async err => { console.log(JSON.stringify((await etat(code)).pf)); throw err; });
    if (but === 1) {
      await P(chef[0]).waitForSelector('.annonce-pf', { timeout: 20000 }).catch(() => null);
      await capture(P(chef[0]), '4-but');
      const brune = await P(chef[1]).waitForFunction(() => window.pfTest.annonces().some(a => /BUUUT.*tu bois 1/.test(a)), null, { timeout: 30000 }).then(() => true).catch(() => false);
      verifier(brune, 'une Brune voit qu elle boit 1 gorgée');
    }
    const fin = but === 3 ? 5 : 0;
    verifier(e.pf.equipes[1].every(j => x.pf.gorgees[j] - g0[j] === 1 + fin) && e.pf.equipes[0].every(j => x.pf.gorgees[j] === g0[j]),
      'gorgées du but ' + but + (fin ? ' (+5 pour la défaite)' : ''));
    if (but < 3) await attendreQue(async () => (await etat(code)).phase === 'visee', 30000, 'tour après le but ' + but);
  }
  await attendreQue(async () => (await etat(code)).phase === 'fin', 20000, 'fin');
  const victoire = await P(chef[0]).waitForFunction(() => /Victoire des Blondes/.test(document.getElementById('panneau').innerText), null, { timeout: 40000 }).then(() => true).catch(() => false);
  verifier(victoire, 'panneau de fin : victoire des Blondes');
  await attendre(3500);
  await capture(P(chef[1]), '5-fin');

  // Rejouer : nouvelle partie, de nouveau les formations.
  await cliquer(A, /^Rejouer$/);
  await attendreQue(async () => (await etat(code)).phase === 'formation', 20000, 'rejouer');
  verifier(true, 'Rejouer relance une partie');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  for (const [r, q] of resultats) console.log(r.padEnd(6), q);
  const echecs = resultats.filter(([r]) => r !== 'OK').length;
  console.log(echecs ? `${echecs} échec(s)` : 'Tout est bon.');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
