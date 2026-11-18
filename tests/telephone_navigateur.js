// Partie complète du Gribouillophone dans de vrais navigateurs (3 joueurs, téléphone en portrait).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/telephone_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. réglages et lancement, l'hôte est celui qui lance ;
//   2. phrase, dessin AU DOIGT (événements tactiles du pointeur), description ;
//   3. chacun ne voit que la contribution précédente de son album ;
//   4. révélation pilotée par l'hôte, vote « meilleur moment », vote final ;
//   5. fin de partie avec le bilan des gorgées ; aucune erreur JavaScript.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2] || '';
const attendre = ms => new Promise(r => setTimeout(r, ms));

async function attendreQue(test, delai, quoi) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) {
    const v = await test();
    if (v) return v;
    await attendre(200);
  }
  throw new Error('délai dépassé : ' + quoi);
}

(async () => {
  const navigateur = await chromium.launch();
  const erreurs = [];
  const NOMS = ['Lea', 'Max', 'Zoe'];
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const pages = [];
  for (const nom of NOMS) {
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
    await ctx.addInitScript(n => { localStorage.setItem('shield.nom', JSON.stringify(n)); }, nom);
    const p = await ctx.newPage();
    p.on('pageerror', e => erreurs.push(nom + ' : ' + e.message));
    p.on('console', m => { if (m.type() === 'error' && !/fonts|Failed to load resource/.test(m.text())) erreurs.push(nom + ' console : ' + m.text()); });
    await p.goto(SITE + 'telephone-jeu.html?salon=' + code);
    pages.push(p);
  }
  const capture = async (p, nom) => {
    if (!CAPTURES) return;
    fs.mkdirSync(CAPTURES, { recursive: true });
    await p.screenshot({ path: path.join(CAPTURES, nom + '.png'), fullPage: true });
  };
  const texte = p => p.locator('#vue').innerText();

  // 1. Salle d'attente : 3 joueurs, Léa lance en Flash.
  await attendreQue(async () => (await texte(pages[0])).includes('Zoe'), 20000, 'les trois joueurs dans la salle');
  await pages[0].getByRole('button', { name: 'Flash' }).click();
  await capture(pages[0], '1-salle');
  await pages[0].getByRole('button', { name: 'Lancer la partie' }).click();

  // 2. Phrase de départ.
  for (const [i, p] of pages.entries()) {
    await p.locator('#champ-texte').waitFor();
    await p.locator('#champ-texte').fill('phrase secrete de ' + NOMS[i]);
    if (i === 0) await capture(p, '2-phrase');
    await p.getByRole('button', { name: 'Valider' }).click();
  }

  // 3. Dessin : chacun dessine la phrase reçue (celle du joueur suivant).
  for (const [i, p] of pages.entries()) {
    await p.locator('canvas.toile').waitFor();
    const consigne = await p.locator('.consigne .phrase').innerText();
    const attendu = 'phrase secrete de ' + NOMS[(i + 1) % 3];
    if (consigne !== attendu) throw new Error(`${NOMS[i]} devait dessiner « ${attendu} », il a « ${consigne} »`);
    // Rien d'autre ne fuit : ni sa propre phrase, ni celle du troisième.
    const tout = await texte(p);
    for (const n of NOMS) if (n !== NOMS[(i + 1) % 3] && tout.includes('phrase secrete de ' + n)) throw new Error('fuite de la phrase de ' + n);
    const b = await p.locator('canvas.toile').boundingBox();
    // Tracé au doigt (pointeur tactile) puis à la souris, avec une autre couleur.
    const cdp = await p.context().newCDPSession(p);
    const pts = [[0.2, 0.3], [0.35, 0.5], [0.5, 0.35], [0.7, 0.6], [0.8, 0.4]].map(([x, y]) => ({ x: b.x + x * b.width, y: b.y + y * b.height }));
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [pts[0]] });
    for (const q of pts.slice(1)) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [q] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await p.locator('.pastille').nth(1 + i).click();
    await p.mouse.move(b.x + b.width * 0.2, b.y + b.height * 0.8);
    await p.mouse.down();
    for (let k = 1; k <= 10; k++) await p.mouse.move(b.x + b.width * (0.2 + k * 0.06), b.y + b.height * (0.8 - (k % 2) * 0.1));
    await p.mouse.up();
    if (i === 0) await capture(p, '3-dessin');
    await p.getByRole('button', { name: 'Valider mon dessin' }).click();
  }
  await attendreQue(async () => (await texte(pages[2])).includes('Décris ce dessin'), 15000, 'étape d’écriture');
  const st = await (await fetch(`${API}/test/etat/${code}`)).json();
  const etatServeur = st.td;
  const traits = etatServeur.albums.map(a => a[1] && a[1].contenu.length);
  if (!traits.every(n => n >= 2)) throw new Error('les dessins n’ont pas été reçus en entier : ' + JSON.stringify(traits));

  // 4. Écriture : chacun décrit le dessin reçu.
  for (const [i, p] of pages.entries()) {
    await p.locator('#champ-texte').waitFor();
    const tout = await texte(p);
    if (tout.includes('phrase secrete')) throw new Error('à l’écriture, ' + NOMS[i] + ' voit une phrase d’origine');
    if (i === 0) await capture(p, '4-ecriture');
    await p.locator('#champ-texte').fill('description de ' + NOMS[i]);
    await p.getByRole('button', { name: 'Valider' }).click();
  }

  // 5. Révélation : seul l'hôte (Léa) a les commandes.
  await attendreQue(async () => (await texte(pages[0])).includes('Lancer la révélation'), 15000, 'albums prêts');
  if ((await texte(pages[1])).includes('Lancer la révélation')) throw new Error('Max a les commandes de l’hôte');
  await pages[0].getByRole('button', { name: 'Lancer la révélation' }).click();
  for (let album = 0; album < 3; album++) {
    for (let k = 0; k < 3; k++) {
      await attendreQue(async () => (await pages[0].locator('.contribution').count()) === k + 1, 10000, `album ${album} contribution ${k}`);
      if (album === 0 && k === 1) {
        await pages[1].getByRole('button', { name: 'Réagir 😂' }).click();
        await attendre(1600);
        await capture(pages[1], '5-revelation');
      }
      await pages[0].getByRole('button', { name: /Suivant|Passer au vote/ }).click();
    }
    // Vote : chacun touche la première contribution qu'il peut choisir.
    for (const p of pages) {
      await attendreQue(async () => (await p.locator('.contribution.votable').count()) > 0, 10000, 'vote d’album');
      await p.locator('.contribution.votable').first().click();
    }
    await attendreQue(async () => /Album suivant|Vote final/.test(await texte(pages[0])), 10000, 'résultat de l’album');
    if (album === 0) await capture(pages[0], '6-resultat');
    await pages[0].getByRole('button', { name: /Album suivant|Vote final/ }).click();
  }

  // 6. Vote final puis fin.
  for (const p of pages) {
    await attendreQue(async () => (await p.locator('.galerie .contribution.votable').count()) > 0, 10000, 'vote final');
    await p.locator('.galerie .contribution.votable').first().click();
  }
  await attendreQue(async () => (await texte(pages[1])).includes('Fin de la partie'), 10000, 'fin de partie');
  await attendre(400);
  await capture(pages[1], '7-fin');
  const fin = await texte(pages[1]);
  if (!fin.includes('Gorgées de la partie') || !fin.includes('Meilleur moment')) throw new Error('bilan incomplet');

  if (erreurs.length) throw new Error('erreurs JavaScript :\n' + erreurs.join('\n'));
  console.log('OK : partie complète à 3 joueurs');
  await navigateur.close();
})().catch(async e => { console.error('ÉCHEC :', e.message); process.exit(1); });
