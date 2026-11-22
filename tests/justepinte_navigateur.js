// Partie complète du Juste Pinte dans de vrais navigateurs (3 joueurs).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/justepinte_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. le réglage (5 questions, catégories) part avec le lancement ;
//   2. chacun tape sa réponse, la voit, peut la modifier ; la bonne réponse
//      n'apparaît nulle part avant la révélation ;
//   3. la révélation montre la bonne réponse, le classement, « dans le mille » ;
//   4. « Question suivante » enchaîne jusqu'au classement final, « Rejouer » relance ;
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
  await A.goto(SITE + 'justepinte-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'justepinte-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 20000, 'joueurs dans le salon');
  await attendre(800);

  // 1. Réglage : 5 questions, sans « Insolite ».
  await A.locator('button', { hasText: /^5$/ }).click();
  await A.locator('.mode', { hasText: 'Insolite' }).click();
  await capture(A, '1-attente');
  await A.locator('button', { hasText: /D.marrer le quiz/ }).first().click();
  await attendreQue(async () => (await etat(code)).phase === 'question', 15000, 'phase question');
  const e0 = await etat(code);
  verifier(e0.jp.nb_manches === 5, '5 questions demandées, ' + e0.jp.nb_manches + ' reçues');

  // Clics et saisies par la page : un clic Playwright attend que tout soit
  // immobile, trop lent avec trois scènes 3D sur la machine d essai. Et on suit
  // la manche du serveur : si la machine prend du retard, une question peut
  // passer sans nous, on reprend à la suivante.
  const repondre = (p, v) => p.evaluate(v => {
    const champ = document.getElementById('reponse');
    const b = [...document.querySelectorAll('#panneau button')].find(x => /^Valider$/.test(x.textContent.trim()));
    if (!champ || !b) return false;
    champ.value = v;
    champ.dispatchEvent(new Event('input', { bubbles: true }));
    b.click();
    return true;
  }, v);
  let m = 0, jouees = 0;
  while (m < 5) {
    const e = await attendreQue(async () => { const x = await etat(code); return (x.phase === 'fin' || (x.phase === 'question' && x.jp.manche >= m)) && x; }, 60000, 'question ' + (m + 1))
      .catch(async err => { console.log(JSON.stringify(await etat(code))); for (const p of pages) console.log('---', await p.locator('#panneau').innerText()); throw err; });
    if (e.phase === 'fin') break;
    m = e.jp.manche;
    const bonne = e.jp.reponse;
    await Promise.all(pages.map(p => p.waitForSelector('#reponse', { timeout: 30000 }).catch(() => null)));
    if (jouees === 0 && m === 0) {
      // 2. Rien ne trahit la réponse ; Alice répond faux, puis corrige.
      const p = A;
      const texte = await p.evaluate(() => document.body.innerText);
      verifier(!texte.includes('✓ '), 'pas de bonne réponse affichée avant la révélation');
      await repondre(p, '1');
      await p.waitForSelector('.ma-reponse', { timeout: 15000 });
      verifier(/Ta réponse/.test(await p.locator('.ma-reponse').innerText()), 'Alice voit sa réponse');
      await capture(p, '2-question');
      await p.evaluate(() => [...document.querySelectorAll('#panneau button')].find(b => /Modifier/.test(b.textContent)).click());
      await p.waitForSelector('#reponse');
    }
    // Alice tape la bonne réponse (dans le mille), les autres s'en écartent.
    // Alice d'abord : la dernière réponse déclenche la révélation.
    await repondre(A, String(bonne).replace('.', ','));
    await Promise.all(pages.slice(1).map((p, j) => { const k = j + 1; return repondre(p, String(bonne * (1 + 0.3 * k) + k).replace('.', ',')); }));
    await attendreQue(async () => { const x = await etat(code); return x.phase !== 'question' || x.jp.manche !== m; }, 40000, 'révélation ' + (m + 1));
    // 3. La révélation : après la chute des jetons, la bonne réponse et le classement.
    const vu = await A.waitForSelector('.bonne', { timeout: 20000 }).then(() => true).catch(() => false);
    const panneau = await A.locator('#panneau').innerText();
    if (vu && /🎯/.test(panneau) && /Alice/.test(panneau)) jouees++;
    if (jouees === 1 && vu) await capture(A, '3-revelation');
    await Promise.all(pages.map(p => p.evaluate(() => { const b = [...document.querySelectorAll('#panneau button')].find(b => /Question suivante|Voir le classement/.test(b.textContent)); if (b) b.click(); })));
    m++;
  }
  verifier(jouees >= 3, 'Alice dans le mille et classement affiché sur ' + jouees + ' manche(s) sur 5');
  await attendreQue(async () => (await etat(code)).phase === 'fin', 30000, 'fin');
  // (le panneau final arrive après la fin des animations de la dernière manche)
  await A.waitForFunction(() => /gagne/.test(document.getElementById('panneau').innerText), null, { timeout: 60000 }).catch(() => {});
  await A.evaluate(() => document.querySelectorAll('body > div').forEach(d => { if (/Touche pour fermer/.test(d.innerText)) d.click(); }));
  await attendre(1500);
  const fin = await A.locator('#panneau').innerText();
  verifier(/Alice/.test(fin) && /gagne/.test(fin), 'classement final : Alice gagne');
  const ef = await etat(code);
  // 3 points dans le mille + 1 de bonus par manche (une manche vue à la révélation compte forcément).
  verifier(ef.jp.points[0] % 4 === 0 && ef.jp.points[0] >= 4 * jouees, 'Alice : (3 + 1) par manche, au moins ' + 4 * jouees + ' points, a ' + ef.jp.points[0]);
  await capture(A, '4-fin');

  // 4. Rejouer.
  await A.locator('button', { hasText: /^Rejouer$/ }).click({ force: true });
  await attendreQue(async () => (await etat(code)).phase === 'question', 15000, 'nouvelle partie');
  verifier(true, 'Rejouer relance une partie');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
