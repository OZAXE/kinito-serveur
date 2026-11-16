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
  verifier(!!await attendreQue(() => max.evaluate(() => !!document.querySelector('.chrono') && !!document.querySelector('svg.plan')), 30000),
    'l\'enquête commence : bandeau (chrono) et plan du manoir');
  const zones = await max.evaluate(() => [document.querySelectorAll('.plan .zone').length, document.querySelectorAll('.plan .zone.fermee').length]);
  verifier(zones[0] === 8 && zones[1] === 0, '  8 pièces sur le plan (vitesse Soirée : aucune fermée)');
  verifier(await sansDefilement(max), '  écran unique : la page ne défile pas');
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
  await appuyer(max, 'Ranger dans l’archive');
  await attendreQue(() => max.evaluate(() => { const b = document.querySelector('[data-relire]'); if (b && !b.disabled) { b.click(); return true; } return false; }), 10000);
  const gorgee = await attendreQue(() => max.evaluate(() => { const t = document.getElementById('toast'); return t && t.style.display !== 'none' && /Bois/.test(t.textContent) && t.textContent; }), 10000);
  verifier(!!gorgee, '  le relire coûte une gorgée : « ' + (gorgee || '') + ' »');
  await max.evaluate(() => document.querySelector('.voile button').click());
  verifier(!!await attendreQue(() => max.evaluate(() => { const b = document.querySelector('[data-relire]'); return b && b.disabled && /Relire dans/.test(b.textContent); }), 5000),
    '  puis chrono avant la prochaine relecture');

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
  verifier(!!marque, 'carnet : Zoé suspecte et une pièce éliminée, toujours là après rechargement');
  verifier(ENVOIS_MAX.length > 0 && !ENVOIS_MAX.some(t => /suspect:|Zoe|carnet|notes/.test(t)),
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
  const fin = await attendreQue(async () => (await visible(pages.Lea)).includes('Grand Détective'), 10000);
  verifier(!!fin, juste + ' accuse juste : tout le monde voit la fin (le Grand Détective)');
  verifier(await pages.Lea.evaluate(l => !!document.querySelector('.plan .zone.crime[data-lieu="' + l + '"]'), sol.lieu), '  le plan met en lumière le lieu du crime');
  if (CAPTURES) await pages.Lea.screenshot({ path: CAPTURES + '/enquete-fin.png' });
  await onglet(pages.Lea, 'recap');
  const recap = await visible(pages.Lea);
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
