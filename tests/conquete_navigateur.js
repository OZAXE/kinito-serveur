// Royaumes : partie complète à 3 navigateurs (un téléphone par joueur), en
// vitesse Flash, en touchant vraiment la carte (pas de raccourci côté page).
// Prérequis : python tests/serveur_essai.py (serveur sur 127.0.0.1:8000) et
// le site servi sur http://localhost:8765 (python -m http.server 8765 dans table-de-jeu).
// Usage : node tests/conquete_navigateur.js [captures]
const { chromium } = require('playwright');

const SITE = process.env.SITE || 'http://localhost:8765';
const CAPTURES = process.argv.includes('captures');
const DOSSIER = process.env.DOSSIER || '.';
const NOMS = ['Ana', 'Bob', 'Cleo'];

const attendre = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const options = { args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] };
  // Derrière un proxy (conteneur), le CDN de Three.js passe par lui ; le site et le serveur restent locaux.
  if (process.env.HTTPS_PROXY) options.proxy = { server: process.env.HTTPS_PROXY, bypass: 'localhost,127.0.0.1' };
  const navigateur = await chromium.launch(options);
  const code = (await (await fetch('http://127.0.0.1:8000/creer-salon')).json()).code;
  const pages = [];
  const erreurs = [];
  for (const nom of NOMS) {
    // Taille d'un téléphone, mais 1 pixel par point et version légère : trois
    // scènes 3D sans carte graphique, sinon la machine de test s'essouffle.
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
    await ctx.addInitScript(n => {
      localStorage.setItem('shield.nom', JSON.stringify(n));
      localStorage.setItem('shield.leger', 'true');
      localStorage.setItem('shield.leger.propose', '1');
    }, nom);
    const page = await ctx.newPage();
    page.on('pageerror', e => erreurs.push(nom + ' : ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/favicon|service-worker/.test(m.text())) erreurs.push(nom + ' (console) : ' + m.text()); });
    await page.goto(SITE + '/conquete-3d-jeu.html?salon=' + code, { waitUntil: 'domcontentloaded' });
    pages.push(page);
    if (process.env.BAVARD) console.log('  page ouverte :', nom);
    await attendre(600);
  }
  const A = pages[0];
  await A.waitForSelector('text=Démarrer la partie', { timeout: 15000 });
  await A.click('.mode:has-text("Flash")');
  if (CAPTURES) await A.screenshot({ path: DOSSIER + '/royaumes-attente.png' });
  await A.click('text=Démarrer la partie');
  await A.waitForFunction(() => window.royaumesEssai.etat() && window.royaumesEssai.etat().phase === 'jeu', null, { timeout: 15000 });
  await attendre(1500);

  const vus = new Set();
  let coups = 0;
  for (let w = 0; w < 1200; w++) {
    const etat = await A.evaluate(() => window.royaumesEssai.etat());
    if (etat.phase === 'fin') break;
    const page = pages[etat.joueur];
    const vue = await page.evaluate(() => window.royaumesEssai.etat());
    if (!vue || vue.version !== etat.version) { await attendre(150); continue; }
    const cle = vue.etape + vue.joueur + vue.manche;
    if (CAPTURES && !vus.has(vue.etape) && (vue.manche >= 2 || vue.etape === 'choix')) {
      vus.add(vue.etape);
      await attendre(500);
      await page.screenshot({ path: DOSSIER + '/royaumes-' + vue.etape + '.png' });
    }
    if (vue.etape === 'choix') {
      await page.click('.combi button >> nth=0');
    } else if (vue.etape === 'action' || vue.etape === 'conquete') {
      const ok = Object.entries(vue.possibles).filter(([, x]) => x.cout <= vue.main_dispo).map(([r]) => Number(r));
      if (ok.length && vue.tour.conquetes < 4) {
        const r = ok[Math.floor(Math.random() * ok.length)];
        const pt = await page.evaluate(r => window.royaumesEssai.ecran(r), r);
        // Deux touchers : choisir, puis conquérir (comme un joueur pressé)
        await page.mouse.click(pt.x, pt.y);
        await attendre(250);
        await page.mouse.click(pt.x, pt.y);
      } else {
        await page.click('#panneau button:has-text("Terminer mes conquêtes"), #panneau button:has-text("Ne rien conquérir")');
      }
    } else if (vue.etape === 'redeploiement') {
      const repartir = await page.$('#panneau button:has-text("Répartir le reste")');
      if (repartir) await repartir.click();
      await page.click('#panneau button:has-text("Valider")');
    }
    coups++;
    await attendre(350);
  }
  const fin = await A.evaluate(() => window.royaumesEssai.etat());
  if (fin.phase !== 'fin') throw new Error('la partie ne s est pas terminee');
  await attendre(1500);
  const bilan = await A.evaluate(() => document.getElementById('panneau').innerText);
  if (!/règne sur la carte/.test(bilan)) throw new Error('pas d ecran de fin : ' + bilan);
  if (CAPTURES) await A.screenshot({ path: DOSSIER + '/royaumes-fin.png' });
  // Tous les écrans ont le même état final
  for (const p of pages) {
    const e = await p.evaluate(() => window.royaumesEssai.etat());
    if (e.version !== fin.version) throw new Error('etats differents entre les joueurs');
  }
  await navigateur.close();
  if (erreurs.length) { console.log(erreurs.join('\n')); throw new Error(erreurs.length + ' erreur(s) dans les pages'); }
  console.log(coups + ' actions jouees en touchant la carte ; classement : ' +
    fin.resultat.classement.map(c => NOMS[c.joueur] + ' ' + c.pieces).join(', '));
  console.log('OK');
}

main().catch(e => { console.error(e); process.exit(1); });
