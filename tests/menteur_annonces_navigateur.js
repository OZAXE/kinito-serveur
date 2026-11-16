// Menteur : la grande annonce « MENTEUR ! » puis « VRAI » / « MENSONGE », visible de tous.
// Une vraie partie à deux sur le serveur d'essai : Enzo dans un vrai navigateur,
// Max simulé par une connexion directe. Les coups d'Enzo passent par SA connexion
// (Playwright routeWebSocket), la page reçoit donc exactement ce qu'elle recevrait en soirée.
// Prérequis : tests/serveur_essai.py et le site sur le port 8765.
// Usage : node tests/menteur_annonces_navigateur.js [dossier_captures]
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) { /* on réessaie */ } await attendre(200); }
  return null;
}

(async () => {
  const nav = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1 });
  const p = await ctx.newPage();
  const erreurs = [];
  p.on('pageerror', e => erreurs.push(e.message));
  // Connexion d'Enzo : relayée telle quelle, et on garde la main dessus pour jouer ses coups
  const enzo = { serveur: null, etat: null, index: null };
  await p.routeWebSocket(/\/ws\//, (ws) => {
    const serveur = ws.connectToServer();
    enzo.serveur = serveur;
    serveur.onMessage((m) => {
      try {
        const msg = JSON.parse(m);
        if (msg.type === 'mt_etat') enzo.etat = msg;
        if (msg.type === 'mt_main') enzo.index = msg.index;
      } catch (e) { /* pas du JSON */ }
      ws.send(m);
    });
    ws.onMessage((m) => serveur.send(m));
  });
  console.log('== menteur : annonces');
  await p.goto(SITE + 'menteur-3d-jeu.html', { timeout: 120000 });
  await p.fill('#pseudo', 'Enzo');
  await p.locator('button', { hasText: /Cr.er un salon/ }).first().click();
  const code = await attendreQue(() => p.evaluate(() => { try { return JSON.parse(localStorage.getItem('shield.soiree')).table; } catch (e) { return null; } }), 30000);
  // Max, simulé
  const max = { ws: new WebSocket(API.replace('http', 'ws') + '/ws/' + code + '/Max?bat=1&page=menteur'), etat: null, index: null };
  max.ws.onmessage = (e) => { const msg = JSON.parse(e.data); if (msg.type === 'mt_etat') max.etat = msg; if (msg.type === 'mt_main') max.index = msg.index; };
  const pingMax = setInterval(() => { try { max.ws.send('{"type":"ping"}'); } catch (e) { /* fermé */ } }, 3000);
  await attendreQue(() => p.evaluate(() => /Max/.test(document.body.innerText)), 15000);
  await p.locator('button', { hasText: /D.marrer la partie/ }).first().click();
  const pret = await attendreQue(() => enzo.etat && max.etat && enzo.etat.phase === 'jeu' && enzo.index !== null && max.index !== null, 30000);
  verifier(!!pret, 'partie de Menteur lancée à deux (Enzo dans le navigateur, Max simulé)');
  // Celui dont c'est le tour pose une carte, l'autre crie « Menteur ! »
  const joueur = (i) => (i === enzo.index ? (m) => enzo.serveur.send(JSON.stringify(m)) : (m) => max.ws.send(JSON.stringify(m)));
  const courant = enzo.etat.courant;
  const autre = courant === enzo.index ? max.index : enzo.index;
  joueur(courant)({ type: 'mt_poser', cartes: [0] });
  await attendreQue(() => enzo.etat.pose && enzo.etat.courant === autre, 10000);
  await attendre(1500);
  joueur(autre)({ type: 'mt_menteur' });
  const cri = await attendreQue(() => p.evaluate(() => { const a = document.querySelector('.annonce-mt'); return a && /MENTEUR/.test(a.innerText) && a.innerText; }), 15000);
  verifier(!!cri, 'tout le monde voit en grand : « ' + (cri || '').replace(/\n/g, ' · ') + ' »');
  if (CAPTURES) { await attendre(500); await p.screenshot({ path: CAPTURES + '/menteur-cri.png' }); }
  const verdict = await attendreQue(() => p.evaluate(() => { const a = document.querySelector('.annonce-mt'); return a && /VRAI|MENSONGE|DIABLE|CHAOS|MAÎTRE/.test(a.innerText) && a.innerText; }), 15000);
  verifier(!!verdict, '  puis le verdict, en grand, avec les cartes posées : « ' + (verdict || '').replace(/\n/g, ' · ') + ' »');
  verifier(await p.evaluate(() => !!document.querySelector('.annonce-mt .carte-mt') || /DIABLE|CHAOS|MAÎTRE/.test(document.querySelector('.annonce-mt').innerText)), '  les cartes réellement posées sont montrées à tous');
  if (CAPTURES) { await attendre(500); await p.screenshot({ path: CAPTURES + '/menteur-verdict.png' }); }
  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  clearInterval(pingMax);
  max.ws.close();
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
