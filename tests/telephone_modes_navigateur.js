// Gribouillophone : modes Knock-Off et Speedrun dans de vrais navigateurs (3 joueurs, téléphone).
//
// Prérequis : comme tests/telephone_navigateur.js (serveur_essai.py + site sur localhost:8765).
// Usage : node tests/telephone_modes_navigateur.js [dossier_captures]
//
// On vérifie :
//   Knock-Off : même phrase pour tous, écran « Mémorise ce dessin » puis copie SANS modèle,
//               albums de 4 éléments (phrase + 3 dessins), partie jusqu'au bout ;
//   Speedrun  : minuteur de dessin court (Flash : 8 s), 2 étapes par défaut à 3, partie jusqu'au bout.
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

async function dessiner(p, decalage) {
  const b = await p.locator('canvas.toile').boundingBox();
  await p.mouse.move(b.x + b.width * 0.2, b.y + b.height * (0.3 + decalage));
  await p.mouse.down();
  for (let k = 1; k <= 8; k++) await p.mouse.move(b.x + b.width * (0.2 + k * 0.08), b.y + b.height * (0.3 + decalage + (k % 2) * 0.1));
  await p.mouse.up();
}

async function partie(navigateur, mode, erreurs) {
  const NOMS = ['Ana', 'Ben', 'Cle'];
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const pages = [];
  for (const nom of NOMS) {
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
    await ctx.addInitScript(n => { localStorage.setItem('shield.nom', JSON.stringify(n)); localStorage.removeItem('telephone.reglages'); }, nom);
    const p = await ctx.newPage();
    p.on('pageerror', e => erreurs.push(mode + ' ' + nom + ' : ' + e.message));
    await p.goto(SITE + 'telephone-jeu.html?salon=' + code);
    pages.push(p);
  }
  const capture = async (p, nom) => {
    if (!CAPTURES) return;
    fs.mkdirSync(CAPTURES, { recursive: true });
    await p.screenshot({ path: path.join(CAPTURES, mode + '-' + nom + '.png'), fullPage: true });
  };
  const texte = p => p.locator('#vue').innerText();
  await attendreQue(async () => (await texte(pages[0])).includes('Cle'), 20000, 'trois joueurs');
  await pages[0].getByRole('button', { name: mode === 'knockoff' ? 'Knock-Off' : 'Speedrun' }).click();
  await pages[0].getByRole('button', { name: 'Flash' }).click();
  await capture(pages[0], '1-salle');
  await pages[0].getByRole('button', { name: 'Lancer la partie' }).click();

  const vus = { vision: false, copie: false, minuteurCourt: null };
  const fait = new Set();
  const fin = Date.now() + 240000;
  while (Date.now() < fin) {
    if ((await texte(pages[0])).includes('Fin de la partie')) break;
    for (const [i, p] of pages.entries()) {
      const t = await texte(p);
      const st = await p.evaluate(() => ({ phase: etat && etat.phase, etape: etat && etat.etape, hote: etat && etat.hote === MOI }));
      const cle = `${st.phase}:${st.etape}:${i}`;
      if (t.includes('Mémorise ce dessin')) {
        if (!vus.vision) { vus.vision = true; await capture(p, '2-memorise'); }
        continue;
      }
      if (t.includes('Redessine-le de mémoire') && !fait.has(cle)) {
        // Le modèle a disparu : un seul canevas, celui où l'on dessine.
        if ((await p.locator('canvas').count()) !== 1) throw new Error('le modèle est encore visible pendant la copie');
        if (!vus.copie) { vus.copie = true; await capture(p, '3-copie'); }
        fait.add(cle);
        await dessiner(p, 0.1 * i);
        await p.getByRole('button', { name: 'Valider ma copie' }).click();
      } else if (t.includes('Valider mon dessin') && !fait.has(cle)) {
        fait.add(cle);
        if (mode === 'speedrun' && vus.minuteurCourt === null) {
          vus.minuteurCourt = Number((await p.locator('#minuteur').innerText()).replace(/\D/g, ''));
          await capture(p, '2-dessin');
        }
        await dessiner(p, 0.1 * i);
        await p.getByRole('button', { name: 'Valider mon dessin' }).click();
      } else if ((await p.locator('#champ-texte').count()) && !fait.has(cle)) {
        fait.add(cle);
        await p.locator('#champ-texte').fill(`${mode} ${st.etape} ${NOMS[i]}`);
        await p.getByRole('button', { name: 'Valider' }).click();
      } else if (st.hote && t.includes('Lancer la révélation')) {
        await p.getByRole('button', { name: 'Lancer la révélation' }).click();
      } else if (st.hote && (await p.getByRole('button', { name: /^(Suivant|Passer au vote|Album suivant|Vote final)$/ }).count())) {
        await p.getByRole('button', { name: /^(Suivant|Passer au vote|Album suivant|Vote final)$/ }).click({ timeout: 3000 }).catch(() => {});
      } else if (st.phase === 'vote_album' || st.phase === 'vote_final') {
        const deja = await p.locator('.contribution.mon-vote').count();
        const choix = p.locator('.contribution.votable');
        if (!deja && await choix.count()) await choix.first().click({ timeout: 3000 }).catch(() => {});
      }
    }
    await attendre(150);
  }
  if (!(await texte(pages[0])).includes('Fin de la partie')) throw new Error(mode + ' : la partie ne finit pas');
  const st = await (await fetch(`${API}/test/etat/${code}`)).json();
  const albums = st.td.albums;
  if (mode === 'knockoff') {
    if (!vus.vision || !vus.copie) throw new Error('Knock-Off : écrans de mémorisation et de copie non vus');
    if (new Set(albums.map(a => a[0].contenu)).size !== 1) throw new Error('Knock-Off : phrases de départ différentes');
    if (!albums.every(a => a.length === 4 && a.slice(1).every(c => c.type === 'dessin'))) throw new Error('Knock-Off : albums mal formés');
  } else {
    if (!(vus.minuteurCourt > 0 && vus.minuteurCourt <= 8)) throw new Error('Speedrun : minuteur de dessin ' + vus.minuteurCourt + ' s');
    if (!albums.every(a => a.length === 3)) throw new Error('Speedrun : 2 étapes attendues après la phrase');
  }
  await capture(pages[0], '4-fin');
  for (const p of pages) await p.context().close();
  console.log('OK : ' + mode);
}

(async () => {
  const navigateur = await chromium.launch();
  const erreurs = [];
  await partie(navigateur, 'knockoff', erreurs);
  await partie(navigateur, 'speedrun', erreurs);
  if (erreurs.length) throw new Error('erreurs JavaScript :\n' + erreurs.join('\n'));
  await navigateur.close();
})().catch(e => { console.error('ÉCHEC :', e.message); process.exit(1); });
