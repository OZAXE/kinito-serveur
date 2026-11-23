// Loup-Garou à 20 : une vraie page (Alice) et 19 joueurs simulés par WebSocket.
//
// Prérequis : comme loupgarou_navigateur.js (serveur d essai sur 127.0.0.1:8000, lancé avec
// LG_NUIT=240 pour une première nuit assez longue, site sur http://localhost:8765).
// Usage : node tests/loupgarou_roles_navigateur.js [dossier_captures]
//
// On vérifie :
//   1. la salle d'attente à 20 : composition automatique (Loup Blanc, Infect,
//      Capitaine...) et lancement ;
//   2. la première nuit, le panneau de chaque rôle sur la page d'Alice (on lui
//      change de carte avec /test/lg_role) : meute, Infect, Voyante, Salvateur,
//      Renard, Corbeau, Cupidon, Enfant Sauvage, Petite Fille, suspect ;
//   3. Cupidon depuis la page : Alice s'unit à Bruno, les amoureux le savent ;
//   4. l'élection du Capitaine au premier jour, puis le débat à voix double ;
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
    try { dernier = await test(); } catch (e) { dernier = null; }
    if (dernier) return dernier;
    await attendre(300);
  }
  throw new Error('délai dépassé : ' + quoi);
}

// Un joueur simulé : il joue sa nuit selon sa vue secrète, vote pour Alice à l'élection.
function robot(code, nom, alice) {
  const r = { nom, secret: null, etat: null, fait: '' };
  r.ws = new WebSocket(API.replace('http', 'ws') + '/ws/' + code + '/' + nom + '?page=loupgarou');
  const envoyer = m => { try { r.ws.send(JSON.stringify(m)); } catch (e) { /* fermé */ } };
  const jouer = () => {
    const e = r.etat, s = r.secret;
    if (!e || !s || s.index === undefined) return;
    const moi = s.index;
    const vivants = e.vivants.map((v, i) => (v ? i : -1)).filter(i => i >= 0);
    const autres = vivants.filter(i => i !== moi && !alice.epargnes.includes(i));
    const cle = e.phase + e.nuit + '|' + e.jour + '|' + (s.type_nuit || '') + JSON.stringify(s.action) + JSON.stringify(s.vote);
    if (cle === r.fait && !(e.phase === 'nuit' && s.type_nuit === 'meute')) return;
    if (e.phase === 'role' && !e.prets[moi]) { r.fait = cle; envoyer({ type: 'lg_pret' }); }
    else if (e.phase === 'nuit' && s.vivant && s.type_nuit === 'meute') {
      // La meute s'aligne sur le premier choix déjà posé, sinon sur la dernière proie.
      const proies = autres.filter(i => !(s.loups || []).includes(i));
      const poses = Object.values(s.meute || {}).filter(c => proies.includes(c));
      const cible = poses.length ? Math.min(...poses) : proies[proies.length - 1];
      if (s.action !== cible && r.fait !== cle + cible) { r.fait = cle + cible; envoyer({ type: 'lg_nuit', cible }); }
    } else if (e.phase === 'nuit' && s.vivant && (s.action === null || s.action === undefined) && !(s.type_nuit === 'cupidon' && s.couple)) {
      r.fait = cle;
      if (s.type_nuit === 'cupidon') envoyer({ type: 'lg_nuit', cible: autres[0], cible2: autres[1] });
      else if (s.type_nuit === 'protection') envoyer({ type: 'lg_nuit', cible: autres.find(i => i !== s.dernier_protege) });
      else envoyer({ type: 'lg_nuit', cible: autres[0] });
    } else if (e.phase === 'potion' && s.role === 'herboriste' && s.vivant && s.victime !== undefined && !s.potion_faite) {
      r.fait = cle; envoyer({ type: 'lg_potion', soin: false, poison: null });
    } else if ((e.phase === 'aube' || e.phase === 'verdict') && !e.suivants[moi]) { r.fait = cle; envoyer({ type: 'lg_suivant' }); }
    else if (e.phase === 'election' && s.vivant && !s.vote) { r.fait = cle; envoyer({ type: 'lg_voter', cible: alice.index }); }
    else if (e.phase === 'debat' && s.vivant && !s.vote && e.idiot_revele !== moi) { r.fait = cle; envoyer({ type: 'lg_voter', cible: null }); }
    else if ((e.phase === 'tir' || e.phase === 'succession') && e.tireur === moi) { r.fait = cle; envoyer({ type: 'lg_tir', cible: autres[0] }); }
  };
  r.ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === 'lg_etat') r.etat = m;
    else if (m.type === 'lg_secret') r.secret = m;
    else return;
    setTimeout(jouer, 50 + Math.random() * 200);
  };
  r.ping = setInterval(() => envoyer({ type: 'ping' }), 3000);
  return r;
}

(async () => {
  const erreurs = [];
  const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const ctx = await navigateur.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
  await ctx.addInitScript(() => { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 120); });
  // Vingt sièges en 3D sans carte graphique : version légère, sinon la page prend des minutes de retard.
  await ctx.addInitScript(() => { localStorage.setItem('shield.leger', 'true'); });
  if (process.env.CDN_LOCAL) {
    await ctx.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
      const m = r.request().url().match(/three@[^/]+\/(.*)$/);
      r.fulfill({ path: path.join(process.env.CDN_LOCAL, 'three/package', m[1]), contentType: 'application/javascript' });
    });
    await ctx.route(/qrious\.min\.js$/, (r) => r.fulfill({ path: path.join(process.env.CDN_LOCAL, 'qrious/package/dist/qrious.min.js'), contentType: 'application/javascript' }));
    await ctx.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ body: '', contentType: 'text/css' }));
  }
  const A = await ctx.newPage();
  A.setDefaultNavigationTimeout(120000);
  A.on('pageerror', e => erreurs.push(e.message));
  A.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|favicon/.test(m.text())) erreurs.push('console : ' + m.text()); });
  const secretsRecus = [];
  A.on('websocket', w => w.on('framereceived', f => { try { const m = JSON.parse(f.payload); if (m.type === 'lg_secret') secretsRecus.push(m.role + '/' + m.type_nuit); } catch (e) { /* binaire */ } }));
  const resultats = [];
  const verifier = (cond, quoi) => { resultats.push([cond ? 'OK' : 'ÉCHEC', quoi]); };
  const panneau = () => A.locator('#panneau').innerText();
  const capture = async nom => {
    if (!CAPTURES) return;
    fs.mkdirSync(CAPTURES, { recursive: true });
    await A.screenshot({ path: path.join(CAPTURES, nom + '.png') });
  };
  const cliquer = (re, delai = 30000) => attendreQue(() => A.evaluate(src => {
    const re = new RegExp(src);
    const b = [...document.querySelectorAll('#panneau button')].find(x => re.test(x.textContent) && !x.disabled);
    if (!b) return false;
    b.click();
    return true;
  }, re.source), delai, 'bouton ' + re);
  const tuile = i => A.evaluate(i => { const t = document.querySelector(`#panneau .tuile[data-joueur="${i}"]`); if (t) t.click(); return !!t; }, i);

  await A.goto(SITE + 'loupgarou-3d-jeu.html');
  await A.fill('#pseudo', 'Alice');
  await A.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  await A.waitForFunction(() => { try { return /^[A-Z]{4}$/.test(JSON.parse(localStorage.getItem('shield.soiree')).table); } catch (e) { return false; } });
  const code = await A.evaluate(() => JSON.parse(localStorage.getItem('shield.soiree')).table);
  const alice = { index: 0, epargnes: [0] };
  const noms = ['Bruno', 'Chloe', 'David', 'Emma', 'Farid', 'Gaby', 'Hugo', 'Ines', 'Jules', 'Karim', 'Lea', 'Malo', 'Nina', 'Oscar', 'Paul', 'Rose', 'Sami', 'Tom', 'Ugo'];
  const robots = [];
  for (const n of noms) { robots.push(robot(code, n, alice)); await attendre(120); }
  await attendreQue(async () => (await etat(code)).joueurs?.length === 20, 30000, '20 joueurs dans le salon');

  // 1. Salle d'attente à 20.
  await attendreQue(() => A.evaluate(() => /À 20/.test(document.getElementById('panneau').innerText)), 20000, 'aperçu à 20');
  const compo = await A.evaluate(() => document.querySelector('.compo').textContent);
  verifier(/Loup Blanc/.test(compo) && /Infect Père des Loups/.test(compo) && /3 Loups-Garous/.test(compo) && /Capitaine/.test(compo) && /Bouc/.test(compo), 'aperçu à 20 : ' + compo);
  await capture('roles-1-attente');
  await cliquer(/D.marrer la partie/);
  await attendreQue(async () => (await etat(code)).phase === 'role', 20000, 'phase des rôles');
  const e0 = await etat(code);
  const indexDe = nom => e0.joueurs.find(j => j.nom === nom).index;
  alice.index = indexDe('Alice');
  const I = alice.index;
  alice.epargnes = [I, indexDe('Bruno')];
  verifier(e0.lg.roles.length === 20 && e0.lg.roles.filter(r => ['loup', 'infect', 'loup_blanc'].includes(r)).length === 5, 'partie à 20 avec 5 loups : ' + e0.lg.roles.join(', '));
  await A.waitForSelector('.carte-role', { timeout: 60000 });
  await A.evaluate(() => document.querySelector('.carte-role').click());
  await cliquer(/Je suis pr.t/);
  await attendreQue(async () => (await etat(code)).phase === 'nuit', 60000, 'première nuit');

  // 2. Le panneau de chaque rôle (première nuit).
  const PANNEAUX = [
    ['loup', /Choisis la victime/], ['infect', /Infecter \(au lieu de tuer\)/], ['loup_blanc', /Choisis la victime/],
    ['cartomancienne', /Sonde un joueur/], ['videur', /Protège un joueur/], ['renard', /Flaire un joueur/],
    ['corbeau', /Désigne un joueur/], ['enfant_sauvage', /Choisis ton modèle/], ['petite_fille', /entrouvres les yeux/],
    ['villageois', /Qui soupçonnes-tu/], ['ancien', /Qui soupçonnes-tu/], ['chien_loup', /Qui soupçonnes-tu/],
    ['vampire', /Mordez un joueur/], ['pyromane', /Arrose un joueur/], ['assassin', /Choisis ta victime/],
    ['cupidon', /Unis deux amoureux/],
  ];
  for (const [role, re] of PANNEAUX) {
    const rep = await (await fetch(`${API}/test/lg_role/${code}/${I}/${role}`)).text();
    if (!/true/.test(rep)) console.log("lg_role", role, rep);
    const ok = await attendreQue(() => A.evaluate(src => new RegExp(src).test(document.getElementById('panneau').innerText), re.source), 15000, 'panneau ' + role).catch(() => false);
    verifier(!!ok, 'nuit : panneau ' + role + ' (' + re.source + ')');
    if (['infect', 'cupidon', 'vampire', 'pyromane'].includes(role)) await capture('roles-2-' + role);
  }
  // 3. Cupidon : Alice s'unit à Bruno.
  const B = indexDe('Bruno');
  await tuile(I); await tuile(B);
  await cliquer(/Unir ces deux|Changer de couple/, 20000).catch(async err => {
    resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
    console.log(await panneau());
    console.log(erreurs.slice(0, 5).join('\n'));
    console.log('secrets reçus : ' + secretsRecus.length + ' ' + [...new Set(secretsRecus)].join(' '));
    throw err;
  });
  await attendreQue(async () => (await etat(code)).phase === 'aube', 90000, 'aube').catch(async err => {
    const e = await etat(code);
    console.log(JSON.stringify({ phase: e.phase, meute: e.lg.meute, roles: e.lg.roles, attente: robots.map(r => [r.nom, r.secret && r.secret.type_nuit, r.secret && r.secret.action]) }));
    throw err;
  });
  const e1 = await etat(code);
  verifier(JSON.stringify(e1.lg.amoureux) === JSON.stringify([I, B].sort((x, y) => x - y)), 'les amoureux : ' + JSON.stringify(e1.lg.amoureux));
  const robB = robots[noms.indexOf('Bruno')];
  verifier(robB.secret && JSON.stringify(robB.secret.amoureux) === JSON.stringify(e1.lg.amoureux), 'Bruno sait qu’il est amoureux');
  await A.evaluate(() => { const m = document.querySelector('.mini-role'); if (m && /Voir/.test(m.textContent)) m.click(); });

  // 4. Election du Capitaine, puis le débat.
  if (e1.lg.vivants[I]) {
    await cliquer(/Au d.bat/);
    await attendreQue(async () => (await etat(code)).phase === 'election', 60000, 'élection du Capitaine');
    await attendreQue(() => A.evaluate(() => /Élisez votre Capitaine/.test(document.getElementById('panneau').innerText)), 15000, 'panneau d’élection');
    verifier(true, 'élection : panneau affiché');
    await capture('roles-3-election');
    await tuile(I);
    await cliquer(/^Élire$/);
    await attendreQue(async () => (await etat(code)).phase === 'debat', 60000, 'débat');
    const e2 = await etat(code);
    verifier(e2.lg.capitaine === I, 'Alice élue Capitaine');
    const ok = await attendreQue(() => A.evaluate(() => /Capitaine compte double/.test(document.getElementById('panneau').innerText)
      && /🎖️/.test(document.getElementById('joueurs').innerText)), 15000, 'Capitaine affiché').catch(() => false);
    verifier(!!ok, 'débat : voix double annoncée et 🎖️ dans la liste');
    await capture('roles-4-debat');
  } else {
    verifier(true, '(Alice morte la première nuit : élection non vérifiée depuis sa page)');
  }

  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.slice(0, 3).join(' | ') : ''));
  robots.forEach(r => { clearInterval(r.ping); try { r.ws.close(); } catch (e) { /* fermé */ } });
  await navigateur.close();
  resultats.forEach(([r, q]) => console.log(r.padEnd(6), q));
  const echecs = resultats.filter(r => r[0] !== 'OK').length;
  console.log(echecs ? echecs + ' échec(s)' : 'OK');
  process.exit(echecs ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
