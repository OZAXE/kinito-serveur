// Partie complète d'Undercover dans de vrais navigateurs (4 joueurs).
//
// Prérequis :
//   - serveur d essai :  python tests/serveur_essai.py   (127.0.0.1:8000)
//   - pages du site servies sur http://localhost:8765 (dossier table-de-jeu)
// Usage : node tests/undercover_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. chacun voit SON mot (voilé tant qu'il ne touche pas la carte) : les
//      Civils le même, l'Undercover le sien, Mr White aucun ;
//   2. la parole passe de téléphone en téléphone (« J'ai donné mon indice ») ;
//   3. le vote élimine, le rôle est révélé, Mr White peut deviner ;
//   4. la partie va jusqu'au bout, le bilan montre les deux mots ;
//   5. « Rejouer » relance avec les points conservés ;
//   6. aucune erreur JavaScript.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2] || '';
const attendre = ms => new Promise(r => setTimeout(r, ms));
async function etat(code) { return (await fetch(`${API}/test/etat/${code}`)).json(); }

// Clic qui ne bloque pas : le panneau se reconstruit à chaque état reçu.
async function cliquer(loc) {
  try { await loc.click({ timeout: 4000 }); return true; } catch (e) { return false; }
}

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
  // Un navigateur par joueur : dans un seul, les scènes 3D se partagent un
  // même processus graphique logiciel et chaque page attend la précédente.
  const navigateurs = [];
  const erreurs = [];
  const nouvelle = async (hd) => {
    const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
    navigateurs.push(navigateur);
    const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: hd ? 2 : 1 });
    // Sans carte graphique, cinq scènes 3D à 60 images/s s'étouffent : 10 suffisent au test.
    await ctx.addInitScript(() => { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 100); });
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
  const ok = (quoi) => resultats.push(['OK', quoi]);
  const verifier = (cond, quoi) => { resultats.push([cond ? 'OK' : 'ÉCHEC', quoi]); };

  const noms = ['Alice', 'Bruno', 'Chloe', 'Dylan', 'Emma'];
  const pages = [];
  // Seule Alice est en haute définition (ses écrans servent de captures).
  for (let k = 0; k < noms.length; k++) pages.push(await nouvelle(k === 0));
  const [A] = pages;
  await A.goto(SITE + 'undercover-3d-jeu.html');
  await A.fill('#pseudo', noms[0]);
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  for (let k = 1; k < noms.length; k++) {
    const p = pages[k];
    await p.goto(SITE + 'undercover-3d-jeu.html?salon=' + code);
    await p.fill('#pseudo', noms[k]);
    await p.locator('button', { hasText: /^Rejoindre$/ }).first().click();
  }
  await attendreQue(async () => (await etat(code)).joueurs?.length === noms.length, 20000, 'joueurs dans le salon');
  await attendre(800);

  // Réglage : conseillé pour 5 = 1 Undercover + 1 Mr White ; on choisit « Mélange ».
  const txtA = await A.evaluate(() => document.getElementById('panneau').innerText);
  verifier(/Réglage conseillé pour 5 joueurs/.test(txtA), 'réglage conseillé affiché pour 5 joueurs');
  await A.locator('.mode', { hasText: 'Mélange' }).click();
  await capture(A, '1-attente');
  await A.locator('button', { hasText: /D.marrer la partie/ }).first().click();
  await attendreQue(async () => (await etat(code)).phase === 'lecture', 15000, 'phase lecture');
  await attendre(1500);

  // 1. Le mot : voilé, puis révélé au toucher, et conforme au serveur.
  const e0 = await etat(code);
  const idx = Object.fromEntries(e0.joueurs.map(j => [j.nom, j.index]));
  for (let k = 0; k < noms.length; k++) {
    const p = pages[k];
    const voile = await p.locator('.mot-secret').first().innerText();
    verifier(/🙈/.test(voile), noms[k] + ' : mot voilé au départ');
    const t0 = Date.now();
    await p.locator('.mot-secret').first().click();
    const vu = await p.locator('.mot-secret .mot').first().innerText();
    console.log('  (' + noms[k] + ' : ' + (Date.now() - t0) + ' ms pour voir son mot)');
    const role = e0.uc.roles[idx[noms[k]]];
    const attendu = role === 'white' ? '👻 Mr White' : e0.uc.mots[role];
    verifier(vu.trim() === attendu, `${noms[k]} (${role}) voit « ${vu.trim()} »`);
    if (k === 0) await capture(p, '2-mot');
    // La lecture dure 30 s : sur une machine lente, elle a pu se finir d'elle-même.
    const lu = p.locator('button', { hasText: /j.ai lu/ });
    if (await lu.count() && await lu.isEnabled()) await lu.click();
  }
  await attendreQue(async () => (await etat(code)).phase === 'indices', 15000, 'phase indices');

  // Boucle de jeu : chaque page fait ce que son panneau propose.
  const vus = new Set();
  let tours = 0, devine = false;
  const fin = Date.now() + 600000;
  while (Date.now() < fin) {
    const e = await etat(code);
    if (e.phase === 'fin') break;
    if (process.env.BAVARD) console.log('  …', e.phase, JSON.stringify(e.uc.vivants));
    if (!vus.has(e.phase)) {
      vus.add(e.phase);
      await attendre(600);
      await capture(A, '3-' + e.phase);
    }
    for (let k = 0; k < noms.length; k++) {
      const p = pages[k];
      const i = idx[noms[k]];
      const b = p.locator('#panneau button', { hasText: /J.ai donn. mon indice/ });
      if (await b.count() && await cliquer(b)) { tours++; continue; }
      if (e.phase === 'vote' && e.uc.vivants[i]) {
        // Les Civils visent un infiltré vivant s'il y en a un candidat, sinon le premier bouton.
        const boutons = p.locator('#panneau button.danger');
        const n = await boutons.count();
        if (n) {
          let choix = 0;
          for (let c = 0; c < n; c++) {
            const nom = (await boutons.nth(c).innerText()).trim();
            const r = e.uc.roles[idx[nom]];
            if (r && r !== 'civil') { choix = c; break; }
          }
          const deja = await p.locator('#panneau button.danger.choisi').count();
          if (!deja) await cliquer(boutons.nth(choix));
        }
      }
      const champ = p.locator('#devine');
      if (await champ.count()) {
        await capture(p, '4-mrwhite');
        try {
          await champ.fill('le ' + e.uc.mots.civil.toLowerCase(), { timeout: 4000 });
          devine = await cliquer(p.locator('#panneau button', { hasText: 'Valider' })) || devine;
        } catch (err) { /* le panneau a bougé : on réessaie au tour de boucle suivant */ }
      }
    }
    await attendre(400);
  }
  const ef = await etat(code);
  verifier(ef.phase === 'fin', 'la partie va jusqu’au bout (phase ' + ef.phase + ')');
  verifier(tours >= 4, 'la parole a circulé (' + tours + ' indices)');
  verifier(vus.has('vote'), 'un vote a eu lieu');
  // Une page lente rejoue d'abord ses animations en file : on attend son bilan.
  await A.waitForSelector('.bilan', { timeout: 180000 });
  await attendre(3000);
  const bilan = await A.evaluate(() => document.getElementById('panneau').innerText);
  await capture(A, '5-fin');
  verifier(bilan.includes(ef.uc.mots.civil) && bilan.includes(ef.uc.mots.undercover), 'le bilan révèle les deux mots');
  if (devine) verifier(/Mr White a trouvé le mot/.test(bilan), 'Mr White a deviné avec article et minuscules');

  // 5. Rejouer : nouvelle partie, points conservés.
  await A.locator('#panneau button', { hasText: /^Rejouer$/ }).click();
  await attendreQue(async () => (await etat(code)).phase === 'lecture', 15000, 'nouvelle partie');
  await A.waitForFunction(() => /Découvre ton mot/.test(document.getElementById('message').innerText), null, { timeout: 120000 });
  const liste = await A.evaluate(() => document.getElementById('joueurs').innerText);
  verifier(/⭐ [1-9]/.test(liste), 'les points de la partie précédente sont conservés');
  const e2 = await etat(code);
  verifier(e2.uc.mots.civil !== ef.uc.mots.civil, 'nouvelle paire de mots');
  await capture(A, '6-rejouer');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  for (const n of navigateurs) await n.close();
  for (const [r, q] of resultats) console.log((r === 'OK' ? '  ok    ' : '  ÉCHEC ') + q);
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

