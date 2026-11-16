// Enquête sur les vraies pages, quatre joueurs (prérequis : comme navigateur.js).
// Usage : node tests/enquete_navigateur.js [dossier_captures]
//  1. Léa ouvre une enquête ; chacun choisit un portrait ; un portrait pris est refusé.
//  2. Léa (hôte) lance : scène d'ouverture, puis l'enquête.
//  3. Appui long : chacun voit son rôle ; un seul tueur, qui voit la solution.
//  4. L'onglet 🔍 d'une page de jeu ouvre l'enquête par-dessus, sans couper la partie.
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) {} await attendre(250); }
  return null;
}
async function appuyer(page, texte, delai = 15000) {
  const ok = await attendreQue(() => page.evaluate(t => {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t && !x.disabled);
    if (b) b.click();
    return !!b;
  }, texte), delai);
  if (!ok) throw new Error('bouton introuvable : ' + texte);
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
    pages[nom] = p;
  }
  console.log('== enquête');
  for (const nom of noms) await pages[nom].goto(SITE + 'enquete.html');
  await appuyer(pages.Lea, 'Ouvrir une enquête');
  verifier(!!await attendreQue(async () => (await pages.Max.textContent('body')).includes('Choisis ton personnage'), 15000),
    'Léa ouvre une enquête : tout le monde voit la salle d\'attente');
  const portraits = ['comtesse', 'docteur', 'lady', 'professeur'];
  for (let i = 0; i < 4; i++) {
    const vus = await attendreQue(() => pages[noms[i]].evaluate(() => document.querySelectorAll('.portrait').length === 12), 10000);
    if (!vus) console.log('   (' + noms[i] + ' : ' + (await pages[noms[i]].textContent('body')).slice(0, 200).replace(/\s+/g, ' ') + ')');
    await pages[noms[i]].evaluate(k => document.querySelectorAll('.portrait')[k].click(), i);
    await attendre(400);
  }
  await pages.Tom.evaluate(() => document.querySelectorAll('.portrait')[0].click());   // la comtesse est à Léa
  const inscrits = await attendreQue(async () => (await pages.Lea.textContent('body')).includes('(4/12)'), 10000);
  verifier(!!inscrits, 'quatre portraits choisis (4/12)');
  verifier((await pages.Tom.evaluate(() => document.querySelector('.portrait.moi .arch').textContent)) === 'le professeur',
    '  un portrait déjà pris ne change pas de main');
  if (CAPTURES) await pages.Lea.screenshot({ path: CAPTURES + '/enquete-salle.png', fullPage: true });

  await appuyer(pages.Lea, 'Lancer l’enquête');
  verifier(!!await attendreQue(async () => (await pages.Zoe.textContent('body')).includes('Gaspard'), 10000), 'lancée : Gaspard ouvre la scène');
  await attendre(6000);
  if (CAPTURES) await pages.Zoe.screenshot({ path: CAPTURES + '/enquete-ouverture.png' });

  const roles = {};
  for (const nom of noms) {
    const p = pages[nom];
    const r = await p.$('.role');
    const boite = await r.boundingBox();
    await p.mouse.move(boite.x + boite.width / 2, boite.y + boite.height / 2);
    await p.mouse.down();
    await attendre(700);
    roles[nom] = await p.evaluate(() => {
      const e = document.querySelector('.role');
      return e.classList.contains('ouvert') ? e.querySelector('.revele').innerText : null;
    });
    if (CAPTURES && /^Tu es l.Assassin/.test(roles[nom] || '')) await p.screenshot({ path: CAPTURES + '/enquete-role-tueur.png' });
    await p.mouse.up();
    roles[nom + '_apres'] = await p.evaluate(() => document.querySelector('.role').classList.contains('ouvert'));
  }
  const tueurs = noms.filter(n => /^Tu es l.Assassin/.test(roles[n] || ''));
  if (tueurs.length !== 1) console.log('   (roles : ' + JSON.stringify(roles) + ')');
  verifier(noms.every(n => roles[n]), 'appui long : chacun voit son rôle');
  verifier(tueurs.length === 1 && /avec/.test(roles[tueurs[0]]), '  un seul Assassin, qui voit le lieu et l\'arme');
  verifier(noms.every(n => !roles[n + '_apres']), '  le rôle se recache dès qu\'on lâche');

  verifier(!!await attendreQue(async () => (await pages.Max.textContent('body')).includes('Temps restant'), 30000),
    'après l\'ouverture, l\'enquête commence (temps restant, dossier)');
  if (CAPTURES) await pages.Max.screenshot({ path: CAPTURES + '/enquete-en-cours.png', fullPage: true });

  // Onglet par-dessus un jeu
  const lea = pages.Lea;
  await lea.goto(SITE + 'kinito-3d-jeu.html?salon=' + code);
  await attendreQue(() => lea.evaluate(() => !!document.getElementById('onglet-enquete')), 15000);
  await lea.click('#onglet-enquete');
  const cadre = await attendreQue(async () => {
    const f = lea.frames().find(fr => fr.url().includes('enquete.html'));
    return f && (await f.textContent('body')).includes('Temps restant') ? f : null;
  }, 15000);
  verifier(!!cadre, "l'onglet 🔍 d'un jeu ouvre l'enquête par-dessus");
  const etatTable = await (await fetch(API + '/test/etat/' + code)).json();
  verifier(etatTable.joueurs.some(j => j.nom === 'Lea' && !j.parti), '  sans couper la place de Léa à la table');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
