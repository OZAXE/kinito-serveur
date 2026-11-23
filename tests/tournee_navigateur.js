// Partie complète de La Tournée dans de vrais navigateurs (3 joueurs).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/tournee_navigateur.js [dossier_captures]
//         PLATEAU=terrasse|pub|cave (défaut cave, le plus chargé)
//
// On vérifie :
//   1. le réglage (5 tours, le plateau) part avec le lancement ;
//   2. au premier tour, objets (Double shot, Main baladeuse bloquée par l'Ange
//      gardien) et boutique ; chacun lance son dé, choisit son chemin aux embranchements, les pions
//      avancent, le journal du tour s'affiche ;
//   3. le mini-jeu s'ouvre en plein écran sur chaque téléphone (consignes, puis
//      le jeu), se joue au doigt, et le score part au serveur ;
//   4. les résultats du mini-jeu et le classement, « Tour suivant » jusqu'à la
//      fin, « Rejouer » relance ;
//   5. aucune erreur JavaScript.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2] || '';
const PLATEAU = process.env.PLATEAU || 'cave';
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
  const [A, B, C] = pages;
  await A.goto(SITE + 'tournee-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'tournee-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 20000, 'joueurs dans le salon');
  await attendre(800);

  // 1. Réglage : 5 tours et le plateau.
  await A.locator('button', { hasText: /^5$/ }).click();
  await A.evaluate(pl => document.querySelector('[data-plateau="' + pl + '"]').click(), PLATEAU);
  await capture(A, '1-attente');
  await A.locator('button', { hasText: /D.marrer la partie/ }).first().click();
  await attendreQue(async () => (await etat(code)).phase === 'des', 15000, 'phase des');
  verifier((await etat(code)).tr.nb_tours === 5, '5 tours demandés');
  verifier((await etat(code)).tr.plateau === PLATEAU, 'plateau ' + PLATEAU + ' demandé');
  await A.waitForTimeout(2500);
  await capture(A, '1b-plateau-' + PLATEAU);
  let cheminsVus = 0;

  // Joue le mini-jeu au doigt, quel qu'il soit, jusqu'à ce que le calque se ferme.
  const jouerMinijeu = async (p, id) => {
    const box = await p.evaluate(() => { const r = document.getElementById('calque-minijeu').getBoundingClientRect(); return { l: r.width, h: r.height }; });
    const x = box.l / 2;
    for (let essai = 0; essai < 3; essai++) {
      if (!(await p.evaluate(() => !!document.querySelector('#calque-minijeu canvas')))) break;
      if (id === 'degaine') {
        await p.mouse.click(x, box.h / 2);
        await attendre(1400);
      } else if (id === 'remplis_pinte') {
        await p.mouse.move(x, box.h / 2);
        await p.mouse.down();
        await attendre(1700);
        await p.mouse.up();
        await attendre(1600);
      } else {
        await p.mouse.move(x, box.h - 90);
        await p.mouse.down();
        for (let k = 1; k <= 5; k++) { await p.mouse.move(x, box.h - 90 - k * 40); await attendre(15); }
        await p.mouse.up();
        await attendre(3500);
      }
    }
  };

  // La machine d essai (trois navigateurs 3D sur peu de processeurs) peut prendre
  // du retard sur les minuteurs du serveur : on suit le tour du serveur plutôt
  // que de compter les nôtres.

  // Cliquer un bouton du panneau d'un seul geste (le panneau peut se redessiner entre deux appels).
  const cliquer = (p, selecteur, motif, delai = 20000) => p.waitForFunction(([sel, src]) => {
    const b = [...document.querySelectorAll(sel)].find(b => !b.disabled && (!src || new RegExp(src).test(b.textContent)));
    if (b) b.click();
    return !!b;
  }, [selecteur, motif ? motif.source : null], { timeout: delai, polling: 250 });

  const minijeuxVus = new Set();
  let scoresRecus = 0, resultatsVus = false, tour = 1;
  while (tour <= 5) {
    const ed = await attendreQue(async () => { const x = await etat(code); return (x.phase === 'fin' || (x.phase === 'des' && x.tr.tour >= tour)) && x; }, 90000, 'dés du tour ' + tour)
      .catch(async err => { const x = await etat(code); console.log(x.phase, JSON.stringify(x.tr)); for (const p of pages) console.log('---', await p.locator('#panneau').innerText()); throw err; });
    if (ed.phase === 'fin') break;
    tour = ed.tr.tour;
    // 2 bis. Au premier tour : des objets dans les poches (Double shot pour A, Main
    // baladeuse pour C contre l'Ange gardien de A) et B juste avant une boutique.
    if (tour === 1) {
      verifier((await (await fetch(`${API}/test/tr_preparer/${code}`)).json()).ok, 'objets et boutique préparés');
      await A.waitForFunction(() => [...document.querySelectorAll('#panneau .poche button')].some(b => /Double shot/.test(b.textContent)), null, { timeout: 20000 });
      await capture(A, '2b-poche');
      await cliquer(A, '#panneau .poche button', /Double shot/);
      await attendreQue(async () => ((await etat(code)).tr.objets[0] || []).length === 1, 15000, 'Double shot utilisé');
      await C.waitForFunction(() => [...document.querySelectorAll('#panneau .poche button')].some(b => /Main baladeuse/.test(b.textContent)), null, { timeout: 20000 });
      await cliquer(C, '#panneau .poche button', /Main baladeuse/);
      await C.waitForFunction(() => [...document.querySelectorAll('#panneau button')].some(b => /^Alice/.test(b.textContent)), null, { timeout: 15000 });
      await capture(C, '2c-cible');
      await cliquer(C, '#panneau button', /^Alice/);
      await attendreQue(async () => { const x = await etat(code); return x.tr.objets[0].length === 0 && x.tr.objets[2].length === 0; }, 20000, 'Ange gardien de A')
        .catch(async err => { console.log(JSON.stringify((await etat(code)).tr.objets), '---', await C.locator('#panneau').innerText()); throw err; });
      verifier((await etat(code)).tr.pieces[0] === 40, "l'Ange gardien bloque la Main baladeuse");
    }
    // 2. Les dés : chacun lance.
    await Promise.all(pages.map(async p => {
      await p.waitForFunction(() => [...document.querySelectorAll('#panneau button')].some(b => /Lancer/.test(b.textContent)), null, { timeout: 30000 }).catch(() => {});
      await p.evaluate(() => { const b = [...document.querySelectorAll('#panneau button')].find(b => /Lancer/.test(b.textContent)); if (b) b.click(); });
      // À un embranchement, des boutons de chemin apparaissent : on prend le dernier.
      const vu = await p.waitForSelector('#panneau [data-choix]', { timeout: 4000 }).then(() => true).catch(() => false);
      if (vu) {
        cheminsVus++;
        if (cheminsVus === 1) await capture(p, '2a-chemins');
        await p.evaluate(() => { const b = [...document.querySelectorAll('#panneau [data-choix]')]; b[b.length - 1].click(); });
      }
    }));
    await attendreQue(async () => (await etat(code)).phase !== 'des', 90000, 'déplacement ' + tour);
    // La boutique arrive juste après les pions : on la guette pendant qu'on lit le journal.
    const boutiqueVue = tour === 1 ? attendreQue(async () => (await etat(code)).phase === 'boutique', 60000, 'boutique')
      .catch(async err => { const x = await etat(code); console.log(x.phase, JSON.stringify(x.tr.positions), JSON.stringify(x.tr.des), JSON.stringify(x.tr.pieces)); throw err; }) : null;
    if (tour === 1) {
      const e = await etat(code);
      verifier(Object.keys(e.tr.des).length === 3, 'les trois dés sont lancés : ' + JSON.stringify(e.tr.des));
      verifier(e.tr.des['0'] >= 2, 'Double shot : deux dés pour A (' + e.tr.des['0'] + ')');
      const lignes = await A.waitForSelector('.journal', { timeout: 12000 }).then(() => A.locator('.journal div').count()).catch(() => 0);
      verifier(lignes === 3, 'le journal du tour a une ligne par joueur (' + lignes + ')');
      await capture(A, '2-deplacement');
    }
    // 2 ter. La boutique : B est passé devant, il achète le premier objet du rayon.
    if (tour === 1) {
      await boutiqueVue;
      await B.waitForSelector('#panneau .rayon button:not([disabled])', { timeout: 40000 })
        .catch(async err => { const x = await etat(code); console.log(x.phase, JSON.stringify(x.tr.boutiques), '---', await B.locator('#panneau').innerText()); console.log(erreurs.join('\n')); throw err; });
      await capture(B, '2d-boutique');
      // Le panneau peut se redessiner entre deux appels : on reprend le bouton au moment du clic.
      await cliquer(B, '#panneau .rayon button');
      await attendreQue(async () => ((await etat(code)).tr.objets[1] || []).length === 1, 15000, 'achat de B');
      verifier(true, 'B achète un objet en boutique');
    }
    // 3. Le mini-jeu.
    const em = await attendreQue(async () => { const x = await etat(code); return x.phase === 'minijeu' && x; }, 30000, 'mini-jeu ' + tour);
    const id = em.tr.minijeu.id;
    minijeuxVus.add(id);
    const ouverts = await Promise.all(pages.map(p => p.waitForSelector('#calque-minijeu.ouvert', { timeout: 15000 }).then(() => true).catch(() => false)));
    if (tour === 1) verifier(ouverts.every(Boolean), 'le mini-jeu ' + id + ' s’ouvre en plein écran chez les trois');
    await Promise.all(pages.map(p => p.waitForSelector('#calque-minijeu canvas', { timeout: 20000 }).catch(() => null)));
    if (minijeuxVus.size <= 3) await capture(A, '3-minijeu-' + id);
    // Pendant qu'on joue, on note les scores qui arrivent au serveur.
    let jeu = true;
    const guet = (async () => {
      while (jeu) {
        const x = await etat(code);
        if (x.phase === 'minijeu') scoresRecus = Math.max(scoresRecus, Object.keys(x.tr.scores || {}).length);
        await attendre(300);
      }
    })();
    await Promise.all(pages.map(p => jouerMinijeu(p, id)));
    await attendreQue(async () => (await etat(code)).phase !== 'minijeu', 60000, 'résultats ' + tour);
    jeu = false;
    await guet;
    { const x = await etat(code); if (x.phase !== 'des') scoresRecus = Math.max(scoresRecus, Object.keys(x.tr.scores || {}).length); }
    if (!resultatsVus && (await etat(code)).phase === 'resultats') {
      const pan = await attendreQue(async () => { const t = await A.locator('#panneau').innerText(); return /Classement/.test(t) && t; }, 20000, 'panneau des résultats').catch(() => '');
      if (/🥇/.test(pan)) { resultatsVus = true; await capture(A, '4-resultats'); }
    }
    await Promise.all(pages.map(p => p.evaluate(() => { const b = [...document.querySelectorAll('#panneau button')].find(x => /Tour suivant|classement final/.test(x.textContent)); if (b) b.click(); })));
    tour++;
  }
  verifier(scoresRecus > 0, 'les scores des mini-jeux arrivent au serveur (' + scoresRecus + ' au mieux sur un mini-jeu)');
  verifier(resultatsVus, 'les résultats du mini-jeu et le classement s’affichent');
  console.log('choix de chemin proposés : ' + cheminsVus);
  verifier(minijeuxVus.size >= 2, 'mini-jeux joués : ' + [...minijeuxVus].join(', '));
  await attendreQue(async () => (await etat(code)).phase === 'fin', 40000, 'fin');
  await A.waitForFunction(() => /gagne/.test(document.getElementById('panneau').innerText), null, { timeout: 60000 }).catch(() => {});
  await A.evaluate(() => document.querySelectorAll('body > div').forEach(d => { if (/Touche pour fermer/.test(d.innerText)) d.click(); }));
  await attendre(1500);
  const fin = await A.locator('#panneau').innerText();
  verifier(/gagne/.test(fin) && /🏆/.test(fin), 'classement final affiché');
  await capture(A, '5-fin');

  // 4. Rejouer.
  await A.evaluate(() => [...document.querySelectorAll('#panneau button')].find(b => /^Rejouer$/.test(b.textContent)).click());
  await attendreQue(async () => (await etat(code)).phase === 'des', 15000, 'nouvelle partie');
  verifier(true, 'Rejouer relance une partie');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
