// Mode objets de Pichenette FC dans de vrais navigateurs (1 contre 1, 2 joueurs).
//
// Prérequis : comme tests/pichenette_navigateur.js (serveur d essai + pages sur :8765).
// Usage : node tests/pichenette_objets_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. le choix du mode « Objets » en salle d'attente, la tireuse visible sur le terrain ;
//   2. l'activation secrète : Glaçon sur une capsule adverse, Sous-bock posé au doigt ; l'adversaire
//      sait seulement qu'un objet se prépare ; les deux sont dévoilés et annoncés au tir ;
//   3. la capsule gelée ne se tire pas au tour suivant ;
//   4. la tireuse ramassée par une capsule : annonce, objet dans la réserve ;
//   5. Cul sec sur l'adversaire choisi (bouton à son prénom) : 2 gorgées, annonce chez lui ;
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

  const noms = ['Alice', 'Bruno'];
  const pages = [await nouvelle(), await nouvelle()];
  const [A, B] = pages;
  await A.goto(SITE + 'pichenette-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  await B.goto(SITE + 'pichenette-3d-jeu.html?salon=' + code);
  await B.fill('#pseudo', noms[1]);
  await B.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  await attendreQue(async () => (await etat(code)).joueurs?.length === 2, 30000, 'joueurs dans le salon');
  await attendre(800);

  // 1. Mode objets.
  verifier(await cliquer(A, /Objets/), 'bouton du mode Objets en salle d attente');
  await capture(A, 'o1-attente');
  await cliquer(A, /Coup d.envoi/);
  await attendreQue(async () => (await etat(code)).phase === 'formation', 20000, 'phase formation');
  let e = await etat(code);
  verifier(e.pf.mode === 'objets', 'partie lancée en mode objets');
  const blonde = e.pf.equipes[0][0], brune = e.pf.equipes[1][0];
  const P = i => pages[i];
  await Promise.all(pages.map(p => p.waitForSelector('.formation', { timeout: 60000 })));
  await cliquer(P(blonde), /Valider les formations/);
  await cliquer(P(brune), /Valider les formations/);
  await attendreQue(async () => (await etat(code)).phase === 'visee', 20000, 'coup d envoi');
  const pret = p => p.waitForFunction(() => { const v = window.pfTest.visee(); return v.enVisee && !v.file && !v.anims && v.camera < 0.01; }, null, { timeout: 40000 }).catch(() => null);
  await Promise.all(pages.map(pret));
  e = await etat(code);
  verifier(!!e.pf.pastille, 'une tireuse sur le terrain dès le premier tour');
  const vues = await Promise.all(pages.map(p => p.waitForFunction(() => window.pfTest.objets().pastille, null, { timeout: 15000 }).then(() => true).catch(() => false)));
  verifier(vues.every(Boolean), 'chaque page montre la tireuse');

  // 2. Réserves : Glaçon et Cul sec pour les Blondes, Sous-bock pour les Brunes.
  verifier((await (await fetch(`${API}/test/pf_objets/${code}?blondes=glacon,cul_sec&brunes=sous_bock`)).json()).ok, 'réserves remplies');
  await P(blonde).waitForFunction(() => /Glaçon/.test(document.getElementById('panneau').innerText), null, { timeout: 20000 });
  await P(brune).waitForFunction(() => /Sous-bock/.test(document.getElementById('panneau').innerText), null, { timeout: 20000 });
  await capture(P(blonde), 'o2-reserve');
  await cliquer(P(blonde), /Glaçon/);
  await P(blonde).waitForFunction(() => window.pfTest.objets().choix === 'glacon', null, { timeout: 10000 });
  const cible = 7;
  const c7 = await P(blonde).evaluate(k => window.pfTest.ecran(k), cible);
  await P(blonde).mouse.click(c7.x, c7.y);
  await attendreQue(async () => { const x = await etat(code); return x.pf.activations && x.pf.activations['0'] && x.pf.activations['0'].cible === cible; }, 10000, 'glaçon activé');
  verifier(true, 'Glaçon activé sur la capsule adverse touchée');
  const devine = await P(brune).waitForFunction(() => /préparent un objet/.test(document.getElementById('panneau').innerText), null, { timeout: 15000 }).then(() => true).catch(() => false);
  verifier(devine, 'l adversaire sait qu un objet se prépare');
  // La réserve est publique (icônes), l'objet activé non.
  const fuite = await P(brune).evaluate(() => /Glaçon|activé/.test(document.getElementById('panneau').innerText) || !!window.pfTest.objets().activation);
  verifier(!fuite, 'l adversaire ne sait pas lequel');
  await cliquer(P(brune), /Sous-bock/);
  await P(brune).waitForFunction(() => window.pfTest.objets().choix === 'sous_bock', null, { timeout: 10000 });
  const bal = await P(brune).evaluate(() => window.pfTest.ecran('balle'));
  await P(brune).mouse.click(bal.x + 90, bal.y - 40);
  await attendreQue(async () => { const x = await etat(code); return x.pf.activations && x.pf.activations['1'] && x.pf.activations['1'].objet === 'sous_bock'; }, 10000, 'sous-bock activé');
  verifier(true, 'Sous-bock posé au doigt');
  const tour1 = (await etat(code)).pf.tour;
  await cliquer(P(blonde), /Passer mon tour/);
  await cliquer(P(brune), /Passer mon tour/);
  await attendreQue(async () => { const x = await etat(code); return x.phase === 'visee' && x.pf.tour === tour1 + 1; }, 40000, 'tour 2');
  e = await etat(code);
  verifier(e.pf.reserves[0].join() === 'cul_sec' && e.pf.reserves[1].length === 0, 'objets consommés ' + JSON.stringify(e.pf.reserves));
  verifier(e.pf.effets.some(x => x.type === 'glacon' && x.palet === cible) && e.pf.effets.some(x => x.type === 'sous_bock'), 'effets en place');
  const annonces = await Promise.all(pages.map(p => p.waitForFunction(() => window.pfTest.annonces().some(a => /GLAÇON/.test(a) && /SOUS-BOCK/.test(a)), null, { timeout: 30000 }).then(() => true).catch(() => false)));
  verifier(annonces.every(Boolean), 'les deux objets dévoilés et annoncés au tir sur chaque page');
  await capture(P(brune), 'o3-effets');

  // 3. La capsule gelée ne se tire pas.
  await pret(P(brune));
  await tirer(P(brune), cible, 60);
  await attendre(800);
  verifier(!((await etat(code)).pf.visees[String(brune)] || []).some(v => v.palet === cible), 'la capsule gelée ne se tire pas');

  // 4. La tireuse : la capsule 0 des Blondes fonce dessus.
  verifier((await (await fetch(`${API}/test/pf_objets/${code}?blondes=cul_sec&pastille=1`)).json()).ok, 'tireuse placée devant la capsule 0');
  await P(blonde).waitForFunction(() => { const v = window.pfTest.visee(); const p = window.pfTest.positions()[0]; return v.enVisee && !v.anims && p && Math.abs(p[1] + 0.45) < 1e-3; }, null, { timeout: 20000 });
  await attendre(400);
  await tirer(P(blonde), 0, 150);
  await attendreQue(async () => ((await etat(code)).pf.visees[String(blonde)] || []).length === 1, 10000, 'visée vers la tireuse');
  const tour2 = (await etat(code)).pf.tour;
  await cliquer(P(blonde), /Valider mes tirs/);
  await cliquer(P(brune), /Passer mon tour|Valider mes tirs/);
  await attendreQue(async () => { const x = await etat(code); return x.phase === 'visee' && x.pf.tour === tour2 + 1; }, 40000, 'tour 3');
  e = await etat(code);
  verifier(e.pf.reserves[0].length === 2 && !e.pf.pastille, 'tireuse ramassée : un objet de plus pour les Blondes ' + JSON.stringify(e.pf.reserves));
  const tireuse = await P(brune).waitForFunction(() => window.pfTest.annonces().some(a => /TIREUSE/.test(a)), null, { timeout: 30000 }).then(() => true).catch(() => false);
  verifier(tireuse, 'annonce de la tireuse');

  // 5. Cul sec sur Bruno.
  await pret(P(blonde));
  const g0 = e.pf.gorgees[brune];
  await cliquer(P(blonde), /Cul sec/);
  await P(blonde).waitForFunction(n => [...document.querySelectorAll('#panneau button')].some(b => b.textContent.trim() === n), noms[brune], { timeout: 10000 });
  await cliquer(P(blonde), new RegExp('^' + noms[brune] + '$'));
  await attendreQue(async () => { const x = await etat(code); return x.pf.activations && x.pf.activations['0'] && x.pf.activations['0'].cible === brune; }, 10000, 'cul sec activé');
  await cliquer(P(blonde), /Passer mon tour/);
  await cliquer(P(brune), /Passer mon tour/);
  await attendreQue(async () => (await etat(code)).pf.gorgees[brune] === g0 + 2, 30000, 'cul sec');
  verifier(true, 'Cul sec : 2 gorgées pour l adversaire choisi');
  const bu = await P(brune).waitForFunction(() => window.pfTest.annonces().some(a => /CUL SEC.*tu bois 2/.test(a)), null, { timeout: 30000 }).then(() => true).catch(() => false);
  verifier(bu, 'la victime du Cul sec le voit');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  for (const [r, q] of resultats) console.log(r.padEnd(6), q);
  const echecs = resultats.filter(([r]) => r !== 'OK').length;
  console.log(echecs ? `${echecs} échec(s)` : 'Tout est bon.');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
