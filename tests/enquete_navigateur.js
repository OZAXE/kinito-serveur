// Enquête sur les vraies pages, quatre joueurs (prérequis : comme navigateur.js,
// serveur d'essai lancé avec ENQ_RAPIDE=1 pour un chrono d'archive court).
// Usage : node tests/enquete_navigateur.js [dossier_captures]
//  1. Salle d'attente en onglets : portraits, joueurs, lancement par l'hôte.
//  2. Ouverture de Gaspard, rôle par appui long (un seul tueur, qui voit la solution).
//  3. Écran unique d'enquête : bandeau, onglets Plan / Indices / Notes / Accuser / Gaspard,
//     sans défilement de la page.
//  4. Points, pli, archive (gorgée, chrono), carnet local jamais envoyé.
//  5. Accusation ratée (verdict, délai), puis accusation juste : fin, plan du crime, récap.
//  6. L'onglet 🔍 d'un jeu ouvre l'enquête par-dessus, sans couper la partie.
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const ENVOIS_MAX = [];   // tout ce que la page de Max envoie au serveur
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) {} await attendre(250); }
  return null;
}
// Texte VISIBLE de la page (textContent inclut aussi le code des <script>).
async function visible(page) { return page.evaluate(() => document.body.innerText); }
// Les confirmations sont des fenêtres dans la page (role=dialog) : on clique le bouton nommé.
const clicSel = (page, sel) => attendreQue(() => page.evaluate((x) => { const b = document.querySelector(x); if (b && !b.disabled) { b.click(); return true; } return false; }, sel), 8000);
const document_body_coupure = (c) => /evt-coupure/.test(c);

async function confirmerDansPage(page, libelle) {
  return attendreQue(() => page.evaluate((l) => {
    const b = [...document.querySelectorAll('[role=dialog] button')].find(x => x.textContent === l);
    if (b) { b.click(); return true; }
    return false;
  }, libelle), 8000);
}

async function appuyer(page, texte, delai = 15000) {
  const ok = await attendreQue(() => page.evaluate(t => {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t && !x.disabled);
    if (b) b.click();
    return !!b;
  }, texte), delai);
  if (!ok) throw new Error('bouton introuvable : ' + texte);
}
async function onglet(page, id) {
  const ok = await attendreQue(() => page.evaluate(i => {
    const b = document.querySelector('nav.onglets button[data-onglet="' + i + '"]');
    if (b) b.click();
    return !!b;
  }, id), 15000);
  if (!ok) throw new Error('onglet introuvable : ' + id);
  await attendre(150);
}
async function appuiLong(page, selecteur) {
  const boite = await (await page.$(selecteur)).boundingBox();
  await page.mouse.move(boite.x + boite.width / 2, boite.y + boite.height / 2);
  await page.mouse.down();
  await attendre(700);
  const texte = await page.evaluate(() => { const v = document.getElementById('role-voile'); return v.classList.contains('ouvert') ? v.innerText : null; });
  return { texte, relache: async () => { await page.mouse.up(); return page.evaluate(() => document.getElementById('role-voile').classList.contains('ouvert')); } };
}
async function sansDefilement(page) {
  return page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1);
}

(async () => {
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const nav = await chromium.launch();
  const erreurs = [];
  const noms = ['Lea', 'Max', 'Zoe', 'Tom'];
  const pages = {};
  for (const nom of noms) {
    const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1 });
    await ctx.addInitScript(([n, c]) => {
      localStorage.setItem('shield.nom', JSON.stringify(n));
      localStorage.setItem('shield.regles_vues', '1');   // règles déjà vues (testées dans solo_navigateur.js)
      localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
    }, [nom, code]);
    const p = await ctx.newPage();
    p.on('pageerror', e => erreurs.push(nom + ' : ' + e.message));
    p.on('dialog', d => d.accept());
    if (nom === 'Max') p.on('websocket', w => w.on('framesent', f => ENVOIS_MAX.push(String(f.payload))));
    pages[nom] = p;
  }
  console.log('== enquête');
  for (const nom of noms) await pages[nom].goto(SITE + 'enquete.html');
  await appuyer(pages.Lea, 'Ouvrir une enquête');
  verifier(!!await attendreQue(() => pages.Max.evaluate(() => document.querySelectorAll('.portrait').length === 12), 15000),
    'Léa ouvre une enquête : tout le monde voit les 12 portraits (onglet Personnage)');
  // Changement d'univers par l'hôte : portraits, police et couleurs suivent chez tout le monde
  await onglet(pages.Lea, 'reglages');
  await appuyer(pages.Lea, 'Station Aurora-7');
  const station = await attendreQue(() => pages.Max.evaluate(() => {
    const t = document.body.innerText;
    return /la médecin/.test(t) && /Exo 2/.test(getComputedStyle(document.documentElement).getPropertyValue('--titre')) && t.slice(0, 200);
  }), 15000);
  verifier(!!station, 'univers : Léa choisit la Station Aurora-7, Max voit ses portraits et sa police');
  if (CAPTURES) await pages.Max.screenshot({ path: CAPTURES + '/enquete-station-salle.png' });
  await appuyer(pages.Lea, 'Le Velvet Room');
  verifier(!!await attendreQue(() => pages.Max.evaluate(() => /le croupier/.test(document.body.innerText)), 15000), '  puis le Velvet Room');
  await appuyer(pages.Lea, 'Le Manoir de Valmont');
  verifier(!!await attendreQue(() => pages.Max.evaluate(() => document.querySelectorAll('.portrait').length === 12 && /la comtesse/.test(document.body.innerText)), 15000), '  et retour au Manoir');
  await onglet(pages.Lea, 'perso');
  for (let i = 0; i < 4; i++) {
    await pages[noms[i]].evaluate(k => document.querySelectorAll('.portrait')[k].click(), i);
    await attendre(400);
  }
  await pages.Tom.evaluate(() => document.querySelectorAll('.portrait')[0].click());   // la comtesse est à Léa
  await attendre(500);
  verifier((await pages.Tom.evaluate(() => document.querySelector('.portrait.moi .arch').textContent)) === 'le professeur',
    '  un portrait déjà pris ne change pas de main');
  verifier(await sansDefilement(pages.Lea), '  la page ne défile pas');
  if (CAPTURES) await pages.Lea.screenshot({ path: CAPTURES + '/enquete-salle.png' });
  // Réglages de l'hôte : dosage, plafond, résumé de la vitesse
  await onglet(pages.Lea, 'reglages');
  await clicSel(pages.Lea, '[data-dosage="corse"]');
  await clicSel(pages.Lea, '[data-plafond="6"]');
  const resume = await attendreQue(async () => { const t = await pages.Max.evaluate(() => { const b = [...document.querySelectorAll('nav button')].find(x => /Réglages/.test(x.textContent)); if (b) b.click(); const r = document.querySelector('.resume-vitesse'); return r && r.innerText; }); return t && /corsé/.test(t) && /6 gorgées par heure/.test(t) && t; }, 10000);
  verifier(!!resume, 'réglages : Léa passe en corsé, plafond 6 ; Max voit le résumé de la vitesse');
  if (CAPTURES) await pages.Lea.screenshot({ path: CAPTURES + '/enquete-reglages.png' });
  await clicSel(pages.Lea, '[data-dosage="normal"]');
  await clicSel(pages.Lea, '[data-plafond="0"]');
  await attendreQue(() => pages.Lea.evaluate(() => /sans plafond/.test(document.querySelector('.resume-vitesse').innerText) && !/corsé/.test(document.querySelector('.resume-vitesse').innerText)), 10000);
  await onglet(pages.Lea, 'joueurs');
  verifier(!!await attendreQue(async () => (await visible(pages.Lea)).includes('(4/12)'), 10000), 'onglet Joueurs : 4/12');
  await appuyer(pages.Lea, 'Lancer l’enquête');
  verifier(!!await attendreQue(async () => (await visible(pages.Zoe)).includes('Gaspard'), 10000), 'lancée : Gaspard ouvre la scène');

  const roles = {};
  for (const nom of noms) {
    await attendreQue(() => pages[nom].$('.zone-role'), 10000);
    const r = await appuiLong(pages[nom], '.zone-role');
    roles[nom] = r.texte;
    if (CAPTURES && /^Tu es l.Assassin/.test(r.texte || '')) await pages[nom].screenshot({ path: CAPTURES + '/enquete-role-tueur.png' });
    roles[nom + '_apres'] = await r.relache();
  }
  const tueurs = noms.filter(n => /^Tu es l.Assassin/.test(roles[n] || ''));
  verifier(noms.every(n => roles[n]), 'appui long : chacun voit son rôle');
  verifier(tueurs.length === 1 && /avec/.test(roles[tueurs[0]]), '  un seul Assassin, qui voit le lieu et l\'arme');
  verifier(noms.every(n => !roles[n + '_apres']), '  le rôle se recache dès qu\'on lâche');

  const max = pages.Max;
  verifier(!!await attendreQue(() => max.evaluate(() => !!document.querySelector('.chrono') && !!document.querySelector('.plan3d canvas')), 30000),
    'l\'enquête commence : bandeau (chrono) et maison 3D');
  await attendre(2500);
  verifier(await sansDefilement(max), '  écran unique avec la 3D : la page ne défile pas');
  if (CAPTURES) await max.screenshot({ path: CAPTURES + '/enquete-3d.png' });
  // On fait tourner la maison d'un glissé : la scène suit.
  const avant = await max.screenshot({ clip: await (await max.$('.plan3d')).boundingBox() });
  const bb = await (await max.$('.plan3d')).boundingBox();
  await max.mouse.move(bb.x + bb.width * 0.3, bb.y + bb.height * 0.5);
  await max.mouse.down(); await max.mouse.move(bb.x + bb.width * 0.75, bb.y + bb.height * 0.45, { steps: 8 }); await max.mouse.up();
  await attendre(800);
  const apres = await max.screenshot({ clip: bb });
  verifier(!avant.equals(apres), '  la maison tourne au doigt');
  if (CAPTURES) await max.screenshot({ path: CAPTURES + '/enquete-3d-tournee.png' });
  await appuyer(max, 'Vue 2D');
  verifier(!!await attendreQue(() => max.evaluate(() => !!document.querySelector('svg.plan') && !document.querySelector('.plan3d')), 5000), '  bouton « Vue 2D » : le plan 2D remplace la 3D');
  const zones = await max.evaluate(() => [document.querySelectorAll('.plan .zone').length, document.querySelectorAll('.plan .zone.fermee').length]);
  verifier(zones[0] === 8 && zones[1] === 0, '  8 pièces sur le plan (vitesse Soirée : aucune fermée)');
  if (CAPTURES) await max.screenshot({ path: CAPTURES + '/enquete-plan.png' });

  // Points, pli, archive
  await fetch(API + '/test/pe/' + code + '/Max/3');
  verifier(!!await attendreQue(() => max.evaluate(() => /3 PE/.test(document.querySelector('.etat-jeu .pe').textContent)), 10000), 'Max gagne 3 PE (bandeau)');
  await onglet(max, 'indices');
  await appuyer(max, 'Demander un indice');
  verifier(!!await attendreQue(async () => (await visible(max)).includes('Un pli pour toi'), 10000), '  il demande un indice : un pli arrive');
  verifier(await max.evaluate(() => (document.querySelector('nav button[data-onglet="indices"] .pastille') || {}).textContent === '1'), '  pastille sur l\'onglet Indices');
  await appuyer(max, 'Ouvrir le pli');
  const lettre = await attendreQue(() => max.evaluate(() => { const l = document.querySelector('.lettre .corps'); return l && l.textContent; }), 10000);
  verifier(!!lettre && /Gaspard/.test(lettre), '  il le lit : « ' + (lettre || '').slice(0, 55) + '… »');
  const vignette = await max.evaluate(() => { const v = document.querySelector('.lettre .vignette'); return v && v.querySelector('.image').textContent.trim() && v.querySelector('.legende-photo').textContent; });
  verifier(!!vignette, '  la lettre est illustrée : photo « ' + vignette + ' », bandeau Hors de cause');
  if (CAPTURES) await max.screenshot({ path: CAPTURES + '/enquete-lettre.png' });
  await appuyer(max, 'Ranger dans l’archive');
  await attendreQue(() => max.evaluate(() => { const b = document.querySelector('[data-relire]'); if (b && !b.disabled) { b.click(); return true; } return false; }), 10000);
  verifier(!!await confirmerDansPage(max, 'Relire'), '  confirmation dans la page (pas de fenêtre native qui gèle la connexion)');
  const gorgee = await attendreQue(() => max.evaluate(() => { const t = document.getElementById('toast'); return t && t.style.display !== 'none' && /Bois/.test(t.textContent) && t.textContent; }), 10000);
  verifier(!!gorgee, '  le relire coûte une gorgée : « ' + (gorgee || '') + ' »');
  await max.evaluate(() => document.querySelector('.voile button').click());
  verifier(!!await attendreQue(() => max.evaluate(() => { const b = document.querySelector('[data-relire]'); return b && b.disabled && /Relire dans/.test(b.textContent); }), 5000),
    '  puis chrono avant la prochaine relecture');
  const chezZoe = await attendreQue(() => pages.Zoe.evaluate(() => { const v = [...document.querySelectorAll('.a-boire .verre')].find(x => /Max/.test(x.textContent)); return v && v.textContent; }), 10000);
  verifier(!!chezZoe, '  pénalité visible par tous : Zoé voit « ' + (chezZoe || '') + ' »');
  if (CAPTURES) await pages.Zoe.screenshot({ path: CAPTURES + '/enquete-a-boire.png' });
  await clicSel(max, '[data-bu]');
  verifier(!!await attendreQue(() => pages.Zoe.evaluate(() => ![...document.querySelectorAll('.a-boire .verre')].some(x => /Max/.test(x.textContent))), 10000),
    '  Max tape « C’est bu ✓ » : la pénalité disparaît chez tout le monde');

  // Témoin anonyme et contre-expertise
  const servi = (await (await fetch(API + '/test/temoin/' + code)).json()).nom;
  verifier(!!servi, 'un témoin anonyme glisse un pli à ' + servi);
  const ps = pages[servi];
  const toastTemoin = await attendreQue(() => ps.evaluate(() => { const t = document.getElementById('toast'); return t && /témoin anonyme/i.test(t.textContent) && t.textContent; }), 10000);
  verifier(!!toastTemoin, '  ' + servi + ' est prévenu : « ' + (toastTemoin || '') + ' »');
  await onglet(ps, 'indices');
  verifier(!!await attendreQue(async () => (await visible(ps)).includes('Témoin anonyme'), 10000), '  le pli affiche sa source « Témoin anonyme »');
  await appuyer(ps, 'Ouvrir le pli');
  await attendreQue(() => ps.evaluate(() => !!document.querySelector('.lettre .corps')), 10000);
  await appuyer(ps, 'Ranger dans l’archive');
  await fetch(API + '/test/pe/' + code + '/' + servi + '/3');
  await attendreQue(() => ps.evaluate(() => /3 PE/.test(document.querySelector('.etat-jeu .pe').textContent)), 10000);
  await attendreQue(() => ps.evaluate(() => { const b = document.querySelector('[data-expertiser]'); if (b && !b.disabled) { b.click(); return true; } return false; }), 15000);
  const choixPE = await attendreQue(() => ps.evaluate(() => { const b = [...document.querySelectorAll('[role=dialog] button')].find(x => / PE$/.test(x.textContent)); return b && !b.disabled && b.textContent; }), 10000);
  verifier(!!choixPE, '  contre-expertise : payer en PE (' + choixPE + ') ou en gorgée');
  await ps.evaluate(() => [...document.querySelectorAll('[role=dialog] button')].find(x => / PE$/.test(x.textContent)).click());
  const cachet = await attendreQue(() => ps.evaluate(() => { const c = document.querySelector('.lettre .cachet'); return c && c.textContent; }), 10000);
  verifier(cachet === 'AUTHENTIQUE', '  le verdict tombe en tampon sur la lettre : « ' + cachet + ' »');
  if (CAPTURES) { await attendre(700); await ps.screenshot({ path: CAPTURES + '/enquete-expertise.png' }); }
  await ps.evaluate(() => document.querySelector('.voile button').click());
  verifier(!!await attendreQue(() => ps.evaluate(() => { const t = document.querySelector('.archive-ligne .tampon.authentique'); return t && !t.closest('.archive-ligne').querySelector('[data-expertiser]'); }), 5000),
    '  tampon permanent dans l\'archive, plus de bouton d\'expertise sur cet indice');

  // Dossier et console du tueur (même geste pour tous)
  const solT = await (await fetch(API + '/test/solution/' + code)).json();
  const tueur = solT.tueur;
  const geste = async (page, essai = 0) => {
    const b = await (await page.$('.bouton-role')).boundingBox();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    const z = await attendreQue(() => page.evaluate(() => { const e = document.querySelector('.role-voile.ouvert .zone-dossier'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }), 5000);
    if (!z) { await page.mouse.up(); return essai < 2 ? geste(page, essai + 1) : null; }   // écran redessiné pendant l'appui : on recommence
    await page.mouse.move(z.x, z.y, { steps: 6 });
    await page.mouse.up();
    return attendreQue(() => page.evaluate(() => { const d = document.getElementById('dossier'); return d.classList.contains('ouvert') && d.querySelector('h2').textContent; }), 5000);
  };
  const enqs = noms.filter(n => n !== tueur);
  const titreEnq = await geste(pages[enqs[0]]);
  verifier(titreEnq === 'Ton dossier', enqs[0] + ' (enquêteur) glisse du bouton Rôle vers la zone : « ' + titreEnq + ' »');
  await pages[enqs[0]].evaluate(() => [...document.querySelectorAll('#dossier header button')][0].click());
  const pt = pages[tueur];
  const titreK = await geste(pt);
  verifier(titreK === 'Ta console', '  ' + tueur + ' (tueur), même geste : « ' + titreK + ' »');
  await fetch(API + '/test/pe/' + code + '/' + tueur + '/9');
  for (let i = 1; i <= 3; i++) {
    await attendreQue(() => pt.evaluate(() => { const b = [...document.querySelectorAll('#dossier button')].find(x => /Acheter une charge/.test(x.textContent)); if (b && !b.disabled) { b.click(); return true; } return false; }), 10000);
    await attendreQue(() => pt.evaluate((n) => new RegExp(n + '/3 charges').test(document.getElementById('dossier').innerText), i), 8000);
  }
  verifier(!!await attendreQue(() => pt.evaluate(() => /3\/3 charges/.test(document.getElementById('dossier').innerText)), 8000), '  il achète 3 charges avec ses PE (le maximum)');
  if (CAPTURES) await pt.screenshot({ path: CAPTURES + '/enquete-console.png' });
  const clic = (page, sel) => attendreQue(() => page.evaluate((s) => { const b = document.querySelector(s); if (b && !b.disabled) { b.click(); return true; } return false; }, sel), 8000);
  const avecPli = ['Max', servi].find(n => n !== tueur);
  await clic(pt, '#dossier [data-sabotage="ecoute"]');
  await clic(pt, '#dossier [data-cible="' + avecPli + '"]');
  await clic(pt, '#dossier [data-saboter]');
  const ecoute = await attendreQue(() => pt.evaluate(() => { const h = document.querySelector('.lettre h3'); return h && /Écoute/.test(h.textContent) && document.querySelector('.lettre .corps').textContent; }), 8000);
  verifier(!!ecoute, '  écoute de ' + avecPli + ' : « ' + (ecoute || '').slice(0, 50) + '… »');
  await pt.evaluate(() => document.querySelector('.voile button').click());
  // Fausse piste en remplacement
  const victime = enqs.find(n => n !== 'Max' && n !== servi) || enqs.find(n => n !== avecPli) || enqs[0];
  await clic(pt, '#dossier [data-sabotage="fausse_piste"]');
  await clic(pt, '#dossier [data-cible="' + victime + '"]');
  await clic(pt, '#dossier [data-categorie]:not([disabled])');
  await clic(pt, '#dossier [data-mode="remplacement"]');
  const risque = await pt.evaluate(() => { const b = document.querySelector('#dossier [data-categorie].actif small'); return b && b.textContent; });
  verifier(!!risque && /risque/.test(risque), '  fausse piste sur ' + victime + ' : indicateur affiché (« ' + risque + ' »)');
  verifier(!!await clic(pt, '#dossier [data-saboter]'), '  le bouton Saboter attend la fin du délai, puis part');
  const pv = pages[victime];
  await fetch(API + '/test/pe/' + code + '/' + victime + '/3');
  await attendreQue(() => pv.evaluate(() => /3 PE/.test(document.querySelector('.etat-jeu .pe').textContent)), 10000);
  await onglet(pv, 'indices');
  await appuyer(pv, 'Demander un indice');
  await appuyer(pv, 'Ouvrir le pli');
  await attendreQue(() => pv.evaluate(() => !!document.querySelector('.lettre .corps')), 10000);
  await appuyer(pv, 'Ranger dans l’archive');
  await clic(pv, '[data-expertiser]');
  await attendreQue(() => pv.evaluate(() => { const b = [...document.querySelectorAll('[role=dialog] button')].find(x => /gorgée/.test(x.textContent)); if (b) { b.click(); return true; } return false; }), 8000);
  const cachetF = await attendreQue(() => pv.evaluate(() => { const c = document.querySelector('.lettre .cachet'); return c && c.textContent; }), 10000);
  verifier(cachetF === 'FALSIFIÉ', '  ' + victime + ' reçoit le faux, et la contre-expertise le démasque : « ' + cachetF + ' »');
  if (CAPTURES) { await attendre(700); await pv.screenshot({ path: CAPTURES + '/enquete-falsifie.png' }); }
  await pv.evaluate(() => document.querySelector('.voile button').click());
  // Fausse rumeur
  await clic(pt, '#dossier [data-sabotage="rumeur"]');
  await clic(pt, '#dossier [data-categorie="lieu"]');
  await clic(pt, '#dossier [data-element]');
  await clic(pt, '#dossier [data-saboter]');
  const temoinR = pages[enqs[0]];
  const toastR = await attendreQue(() => temoinR.evaluate(() => { const t = document.getElementById('toast'); return t && t.style.display !== 'none' && /🗣️/.test(t.textContent) && t.textContent; }), 15000);
  verifier(!!toastR, 'la fausse rumeur circule chez tout le monde : « ' + (toastR || '') + ' »');
  await onglet(temoinR, 'fil');
  verifier((await visible(temoinR)).includes('🗣️'), '  et reste dans le fil de ' + (await temoinR.evaluate(() => document.querySelector('nav button[data-onglet="fil"]').textContent.trim())));
  verifier(!!await attendreQue(() => pt.evaluate(() => /Tes sabotages/.test(document.getElementById('dossier').innerText) && document.querySelectorAll('#dossier section:last-child p').length === 3), 8000),
    '  la console garde l\'historique de ses 3 sabotages');
  await pt.evaluate(() => [...document.querySelectorAll('#dossier header button')][0].click());

  // Événements d'ambiance
  const pe0 = pages[enqs[0]];
  await fetch(API + '/test/evenement/' + code + '/coupure');
  const bande = await attendreQue(() => pe0.evaluate(() => { const b = document.querySelector('.evenement-bande'); return b && document.body.classList.contains('evt-coupure') && b.innerText; }), 10000);
  verifier(!!bande && /courant/i.test(bande), 'coupure : bandeau avec sa règle et son compte à rebours chez tout le monde');
  await onglet(pe0, 'indices');
  const coupe = await attendreQue(() => pe0.evaluate(() => { const b = document.querySelector('[data-relire]'); return b && b.disabled && /Courant coupé/.test(b.textContent); }), 8000);
  verifier(!!coupe, '  l\'archive est verrouillée (« Courant coupé »)');
  await onglet(pe0, 'plan');
  if (CAPTURES) { await attendre(800); await pe0.screenshot({ path: CAPTURES + '/enquete-coupure.png' }); }
  await fetch(API + '/test/evenement/' + code + '/decouverte');
  const lettresD = await Promise.all(noms.map(n => attendreQue(() => pages[n].evaluate(() => { const a = [...document.querySelectorAll('.lettre .avis')].find(x => /Indice public/.test(x.textContent)); return a && document.querySelector('.lettre .corps').textContent; }), 10000)));
  verifier(lettresD.every(x => x && x === lettresD[0]) && !document_body_coupure(await pe0.evaluate(() => document.body.className)),
    'découverte : tous reçoivent le même indice public, illustré (« ' + (lettresD[0] || '').slice(0, 45) + '… »), et le courant est revenu');
  if (CAPTURES) await pe0.screenshot({ path: CAPTURES + '/enquete-decouverte.png' });
  for (const n of noms) await pages[n].evaluate(() => document.querySelectorAll('.voile').forEach(v => v.remove()));
  const inter = (await (await fetch(API + '/test/evenement/' + code + '/interrogatoire')).json()).evenement.interroges;
  const pi = pages[inter[0]];
  verifier(!!await attendreQue(() => pi.evaluate(() => { const b = [...document.querySelectorAll('.evenement-bande button')].find(x => /Je refuse/.test(x.textContent)); if (b) { b.click(); return true; } return false; }), 8000), 'interrogatoire : ' + inter.join(' et ') + ' sont désignés, bouton « Je refuse »');
  await confirmerDansPage(pi, 'Je refuse');
  const refus = await attendreQue(() => pi.evaluate(() => { const t = document.getElementById('toast'); return t && /Bois/.test(t.textContent) && t.textContent; }), 8000);
  verifier(!!refus, '  refuser coûte des gorgées : « ' + (refus || '') + ' »');
  const pi2 = pages[inter[1]];
  verifier(!!await clicSel(pi2, '[data-repondu]'), '  ' + inter[1] + ' livre son indice à voix haute et tape « J’ai répondu ✓ »');
  const statut = await attendreQue(() => pages[noms.find(n => !inter.includes(n))].evaluate(() => { const s = document.querySelector('.evenement-bande .interroges'); return s && /a répondu/.test(s.innerText) && /a refusé/.test(s.innerText) && s.innerText; }), 10000);
  verifier(!!statut, '  tout le monde voit qui a répondu et qui a refusé : « ' + (statut || '').replace(/\n/g, ' ') + ' »');
  if (CAPTURES) await pi2.screenshot({ path: CAPTURES + '/enquete-interrogatoire.png' });
  await fetch(API + '/test/evenement/' + code + '/maree');

  // Carnet sur le plan
  await onglet(max, 'plan');
  await max.evaluate(() => { const b = [...document.querySelectorAll('.jetons button')].find(x => x.textContent.includes('Zoe')); b.click(); b.click(); });
  await max.evaluate(() => document.querySelector('.plan .zone').dispatchEvent(new MouseEvent('click', { bubbles: true })));
  await max.reload();
  await onglet(max, 'plan');
  const marque = await attendreQue(() => max.evaluate(() => {
    const b = [...document.querySelectorAll('.jetons button')].find(x => x.textContent.includes('Zoe'));
    return b && b.className === 'suspect' && document.querySelector('.plan .zone.elimine');
  }), 15000);
  verifier(!!marque, 'carnet : Zoé suspecte et une pièce éliminée, toujours là après rechargement (vue 2D gardée)');
  await appuyer(max, 'Vue 3D');
  verifier(!!await attendreQue(() => max.evaluate(() => !!document.querySelector('.plan3d canvas')), 10000), '  bouton « Vue 3D » : retour à la maison 3D');
  verifier(ENVOIS_MAX.length > 0 && !ENVOIS_MAX.filter(t => !/enq_sabotage/.test(t)).some(t => /suspect:|Zoe|carnet|notes/.test(t)),
    '  le carnet n\'est jamais envoyé au serveur (' + ENVOIS_MAX.length + ' messages, aucun ne le contient)');

  // Accusations
  const sol = await (await fetch(API + '/test/solution/' + code)).json();
  const enqueteur = noms.find(n => n !== sol.tueur && n !== 'Max');
  const page = pages[enqueteur];
  await onglet(page, 'accuser');
  const autreLieu = await page.evaluate(l => [...document.querySelectorAll('.plan .zone:not(.fermee)')].map(z => z.dataset.lieu).find(x => x !== l), sol.lieu);
  const choisir = async (lieu, arme, suspect) => {
    await page.evaluate(l => document.querySelector('.plan .zone[data-lieu="' + l + '"]').dispatchEvent(new MouseEvent('click', { bubbles: true })), lieu);
    await attendre(200);
    await page.evaluate(a => document.querySelector('.jetons button[data-arme="' + a + '"]').click(), arme);
    await attendre(200);
    await page.evaluate(s => document.querySelector('.jetons button[data-suspect="' + s + '"]').click(), suspect);
    await attendre(300);
  };
  await choisir(autreLieu, sol.arme, sol.tueur);
  if (CAPTURES) await page.screenshot({ path: CAPTURES + '/enquete-accuser.png' });
  verifier(await page.evaluate(() => { const b = document.querySelector('[data-accuser]').getBoundingClientRect(); return b.bottom <= document.querySelector('nav.onglets').getBoundingClientRect().top + 1 && b.top > 0; }),
    '  le bouton Accuser est visible sans faire défiler');
  await page.evaluate(() => document.querySelector('[data-accuser]').click());
  await confirmerDansPage(page, 'Accuser');
  const verdict = await attendreQue(() => page.evaluate(() => { const l = document.querySelector('.lettre .corps'); return l && l.textContent; }), 10000);
  verifier(verdict === '2 éléments justes sur 3.', enqueteur + ' accuse (mauvais lieu) : « ' + verdict + ' »');
  await page.evaluate(() => document.querySelector('.voile button').click());
  verifier(!!await attendreQue(() => page.evaluate(() => /Attendre/.test(document.querySelector('[data-accuser]').textContent)), 5000),
    '  délai avant la prochaine accusation');
  verifier(!!await attendreQue(async () => (await visible(pages.Zoe === page ? pages.Tom : pages.Zoe)).includes('échecs 1/'), 10000),
    '  les autres voient « échecs 1/8 », sans savoir qui ni quoi');

  const juste = noms.find(n => n !== sol.tueur && n !== enqueteur);
  const p2 = pages[juste];
  await onglet(p2, 'accuser');
  const choisir2 = async (lieu, arme, suspect) => {
    await p2.evaluate(l => document.querySelector('.plan .zone[data-lieu="' + l + '"]').dispatchEvent(new MouseEvent('click', { bubbles: true })), lieu);
    await attendre(200);
    await p2.evaluate(a => document.querySelector('.jetons button[data-arme="' + a + '"]').click(), arme);
    await attendre(200);
    await p2.evaluate(s => document.querySelector('.jetons button[data-suspect="' + s + '"]').click(), suspect);
    await attendre(300);
  };
  await choisir2(sol.lieu, sol.arme, sol.tueur);
  await p2.evaluate(() => document.querySelector('[data-accuser]').click());
  await confirmerDansPage(p2, 'Accuser');
  const fin = await attendreQue(async () => (await visible(pages.Lea)).includes('Grand Détective'), 10000);
  verifier(!!fin, juste + ' accuse juste : tout le monde voit la fin (le Grand Détective)');
  verifier(await pages.Lea.evaluate(l => !!document.querySelector('.plan .zone.crime[data-lieu="' + l + '"]'), sol.lieu), '  le plan met en lumière le lieu du crime');
  if (CAPTURES) await pages.Lea.screenshot({ path: CAPTURES + '/enquete-fin.png' });
  await onglet(pages.Lea, 'recap');
  const recap = await visible(pages.Lea);
  verifier(/sabot/i.test(recap), '  récapitulatif : les sabotages du tueur');
  verifier(/gorgées de la soirée/i.test(recap) && /Changées en attente/i.test(recap), '  récapitulatif : les gorgées bues par chacun');
  verifier(/Témoin/i.test(recap) && /Expertises/i.test(recap), '  récapitulatif : plis du témoin et contre-expertises par joueur');
  verifier(recap.includes(enqueteur) && recap.includes('2/3') && recap.includes('3/3'), '  récapitulatif : les deux accusations');

  // Onglet par-dessus un jeu
  const lea = pages.Lea;
  await lea.goto(SITE + 'kinito-3d-jeu.html?salon=' + code);
  await attendreQue(() => lea.evaluate(() => !!document.getElementById('onglet-enquete')), 15000);
  await lea.click('#onglet-enquete');
  const cadre = await attendreQue(async () => {
    const f = lea.frames().find(fr => fr.url().includes('enquete.html'));
    return f && (await f.evaluate(() => document.body.innerText)).includes('Grand Détective') ? f : null;
  }, 15000);
  verifier(!!cadre, "l'onglet 🔍 d'un jeu ouvre l'enquête par-dessus");
  const etatTable = await (await fetch(API + '/test/etat/' + code)).json();
  verifier(etatTable.joueurs.some(j => j.nom === 'Lea' && !j.parti), '  sans couper la place de Léa à la table');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
