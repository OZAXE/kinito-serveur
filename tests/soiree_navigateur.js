// Parcours d'une soirée complète, sur les vraies pages, avec trois joueurs.
//
// Prérequis : comme tests/navigateur.js (serveur d'essai sur 127.0.0.1:8000,
// site servi sur http://localhost:8765).
// Usage : node tests/soiree_navigateur.js
//
//  1. Léa crée une soirée sur l'accueil ; Max et Zoé la rejoignent avec le code.
//  2. Chacun ouvre Kinito depuis l'accueil : il rejoint le salon sans ressaisir
//     son prénom ; Léa lance la partie.
//  3. Léa ouvre le Casino : on lui demande de confirmer le remplacement, puis
//     Max et Zoé passent TOUT SEULS sur la page du Casino, en partie.
//  4. Un nouveau téléphone qui ouvre Kinito pendant le Casino voit le bandeau
//     « Le groupe joue à Casino » et le rejoint.
//  5. Aucune erreur JavaScript.
const { chromium } = require('playwright');

const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) {} await attendre(250); }
  return null;
}
// Les panneaux des jeux s'animent et se redessinent à chaque arrivée : on
// attend que le bouton existe, puis on l'actionne comme un doigt.
async function appuyer(page, texte, delai = 15000) {
  const ok = await attendreQue(() => page.evaluate(t => {
    const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t && !x.disabled);
    if (b) b.click();
    return !!b;
  }, texte), delai);
  if (!ok) throw new Error('bouton introuvable : ' + texte);
}
async function etat(code) { return (await fetch(`${API}/test/etat/${code}`)).json(); }

(async () => {
  const nav = await chromium.launch();
  const erreurs = [];
  const dialogues = [];
  async function joueur(nom) {
    const ctx = await nav.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    page.on('pageerror', e => erreurs.push(nom + ' : ' + e.message));
    page.on('dialog', d => { dialogues.push(nom + ' : ' + d.message()); d.accept(); });
    await page.goto(SITE + 'index.html');
    await page.fill('#prenom', nom);
    return page;
  }

  console.log('== soirée');
  const lea = await joueur('Lea'), max = await joueur('Max'), zoe = await joueur('Zoe');

  await lea.click('text=Créer une soirée');
  const code = await attendreQue(async () => {
    const t = await lea.textContent('.code-grand'); return /^[A-Z]{4}$/.test(t) ? t : null;
  }, 15000);
  verifier(!!code, 'Léa crée une soirée (code ' + code + ')');
  for (const p of [max, zoe]) {
    await p.fill('input.code', code);
    await p.click('text=Rejoindre');
    await p.waitForSelector('.code-grand');
  }
  verifier((await zoe.textContent('.code-grand')) === code, 'Max et Zoé rejoignent la soirée');

  for (const p of [lea, max, zoe]) {
    await p.click('a.jeu.kinito');
    await p.waitForURL(/kinito-3d-jeu\.html\?salon=/);
  }
  const tous = await attendreQue(async () => { const e = await etat(code); return e.joueurs.length === 3 ? e : null; }, 15000);
  verifier(!!tous && tous.joueurs.map(j => j.nom).sort().join() === 'Lea,Max,Zoe',
    'les trois arrivent dans le salon Kinito sans ressaisir leur prénom');

  await appuyer(lea, 'Demarrer la partie');
  verifier(!!await attendreQue(async () => (await etat(code)).jeu === 'kinito', 10000), 'Léa lance Kinito');

  // Zoé verrouille son téléphone : sa connexion reste ouverte mais muette, elle
  // va rater l'annonce du changement de jeu.
  await zoe.evaluate(() => {
    const vrai = Soiree.traiterMessage;
    Soiree.traiterMessage = (msg, jeu) => (msg && msg.type === 'salon_jeu') ? true : vrai(msg, jeu);
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });

  await lea.goto(SITE + 'index.html');
  const enCours = await attendreQue(async () => (await lea.textContent('#soiree-contenu')).includes('Kinito'), 10000);
  verifier(!!enCours, "l'accueil affiche « En cours : Kinito »");
  await lea.click('a.jeu.casino');
  await lea.waitForURL(/casino-3d-jeu\.html\?salon=/);
  await appuyer(lea, 'Ouvrir le casino');
  verifier(!!await attendreQue(async () => (await etat(code)).jeu === 'casino', 10000),
    'Léa ouvre le casino : il remplace Kinito');
  verifier(dialogues.some(d => d.startsWith('Lea') && d.includes('Kinito')),
    '  après lui avoir demandé de confirmer le remplacement');
  const suivis = await attendreQue(async () => /casino-3d-jeu\.html/.test(max.url()), 15000);
  verifier(!!suivis, 'Max passe tout seul sur la page du Casino');

  await attendre(2500);
  await zoe.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const reveil = await attendreQue(async () =>
    (await zoe.textContent('body')).includes('Ta table joue à Casino'), 25000);
  verifier(!!reveil, 'Zoé, qui dormait pendant le changement, voit au réveil « Le groupe joue à Casino »');
  await appuyer(zoe, 'Rejoindre');
  verifier(!!await attendreQue(async () => /casino-3d-jeu\.html/.test(zoe.url()), 15000), '  et rejoint en un appui');
  const assis = await attendreQue(async () => {
    const e = await etat(code); return e.jeu === 'casino' && e.joueurs.filter(j => !j.parti).length === 3;
  }, 15000);
  verifier(!!assis, '  et ils sont bien à la table (3 présents)');

  // Quatre scènes 3D en rendu logiciel saturent la machine de test : Max et
  // Zoé posent leur téléphone (le casino continue sur le serveur).
  await max.context().close();
  await zoe.context().close();
  const tom = await joueur('Tom');
  await tom.goto(SITE + 'index.html');
  await tom.fill('input.code', code);
  await tom.click('text=Rejoindre');
  await tom.waitForSelector('.code-grand');
  await tom.click('a.jeu.kinito');
  const bandeau = await attendreQue(async () =>
    (await tom.textContent('body')).includes('Ta table joue à Casino'), 25000);
  if (!bandeau) console.log('   (url ' + tom.url() + ', bandeaux : ' + JSON.stringify(await tom.evaluate(() =>
    [[...document.querySelectorAll('[role=status]')].map(b => b.textContent), Soiree.nom(), Soiree.actuelle(),
     (document.querySelector('#panneau') || {}).innerText])) + ')');
  verifier(!!bandeau, 'un retardataire sur Kinito voit « Le groupe joue à Casino »');
  await appuyer(tom, 'Rejoindre');
  verifier(!!await attendreQue(async () => /casino-3d-jeu\.html/.test(tom.url()), 10000), '  et le rejoint en un appui');

  // Deuxième table : Tom en ouvre une et choisit Kinito ; Léa vient s'y asseoir.
  await tom.goto(SITE + 'index.html');
  await appuyer(tom, 'Nouvelle table');
  const ouverte = await attendreQue(async () => (await tom.textContent('#soiree-contenu')).includes('Table 2 · ta table'), 15000);
  verifier(!!ouverte, 'Tom ouvre une deuxième table');
  await tom.click('a.jeu.kinito');
  await tom.waitForURL(new RegExp('kinito-3d-jeu\\.html\\?salon=' + code + '-2'));
  verifier(!!await attendreQue(async () => (await etat(code + '-2')).joueurs.some(j => j.nom === 'Tom' && !j.parti), 15000),
    '  et s\'y assoit en choisissant Kinito');
  verifier(!!await attendreQue(async () => (await etat(code)).joueurs.every(j => j.nom !== 'Tom' || j.parti), 10000),
    '  il a quitté la table 1');

  await lea.goto(SITE + 'index.html');
  const deux = await attendreQue(async () => {
    const t = await lea.textContent('#soiree-contenu'); return t.includes('Table 2') && t.includes('Tom');
  }, 15000);
  verifier(!!deux, "l'accueil de Léa liste les deux tables et leurs joueurs");
  await appuyer(lea, "S'asseoir ici");
  await lea.click('a.jeu.kinito');
  await lea.waitForURL(new RegExp('salon=' + code + '-2'));
  await appuyer(lea, 'Demarrer la partie', 25000);
  verifier(!!await attendreQue(async () => (await etat(code + '-2')).jeu === 'kinito', 15000), 'Léa lance Kinito à la table 2');
  verifier((await etat(code)).jeu === 'casino', '  la table 1 joue toujours au Casino');

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
