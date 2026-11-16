// Mode solo : un seul vrai joueur, la vitesse Démo et des joueurs fictifs.
// Prérequis : serveur d'essai (tests/serveur_essai.py) et site sur le port 8765.
// Usage : node tests/solo_navigateur.js [dossier_captures]
const { chromium } = require('playwright');
const SITE = 'http://localhost:8765/';
const API = 'http://127.0.0.1:8000';
const CAPTURES = process.argv[2];
const attendre = ms => new Promise(r => setTimeout(r, ms));
let echecs = 0;
function verifier(ok, quoi) { console.log((ok ? '   OK    ' : '   ECHEC ') + quoi); if (!ok) echecs++; }
async function attendreQue(test, delai) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) { try { const r = await test(); if (r) return r; } catch (e) { /* on réessaie */ } await attendre(250); }
  return null;
}
const clic = (page, texte) => attendreQue(() => page.evaluate((t) => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes(t) && !x.disabled); if (b) { b.click(); return true; } return false; }, texte), 15000);

(async () => {
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const nav = await chromium.launch();
  const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: CAPTURES ? 2 : 1, hasTouch: true });
  await ctx.addInitScript(([c]) => {
    localStorage.setItem('shield.nom', JSON.stringify('Lea'));
    localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
  }, [code]);
  const p = await ctx.newPage();
  const erreurs = [];
  p.on('pageerror', e => erreurs.push(e.message));
  console.log('== solo');
  await p.goto(SITE + 'enquete.html');
  await clic(p, 'Ouvrir une enquête');
  await attendreQue(() => p.evaluate(() => document.querySelectorAll('.portrait').length === 12), 15000);
  await p.evaluate(() => document.querySelector('.portrait').click());
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="reglages"]').click());
  await clic(p, 'Démo');
  const demo = await attendreQue(() => p.evaluate(() => { const r = document.querySelector('.resume-vitesse'); return r && /Mode démo/.test(r.innerText) && r.innerText.split('\n')[0]; }), 10000);
  verifier(!!demo, 'vitesse Démo (10 min) : « ' + (demo || '') + ' »');
  if (CAPTURES) await p.screenshot({ path: CAPTURES + '/solo-demo.png' });
  // Règles : onglet de la salle d'attente
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="regles"]').click());
  const regles = await attendreQue(() => p.evaluate(() => { const r = document.querySelector('.regles'); return r && r.querySelectorAll('details').length; }), 10000);
  verifier(regles >= 10, 'onglet Règles : ' + regles + ' sections (principe, rôles, indices, accusations, événements…)');
  const principe = await p.evaluate(() => document.querySelector('.regles details[open]').innerText);
  verifier(/Saboteur|Assassin|Tueur/.test(principe) && /PE/.test(principe), '  le principe est ouvert d’office, avec le vocabulaire de l’univers');
  await p.evaluate(() => document.querySelectorAll('.regles details')[8].querySelector('summary').click());
  const evts = await p.evaluate(() => document.querySelectorAll('.regles details')[8].innerText);
  verifier(/Panne du réacteur|L.orage fait sauter le courant/.test(evts), '  les événements portent leur nom dans l’univers choisi');
  if (CAPTURES) await p.screenshot({ path: CAPTURES + '/regles.png' });
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="joueurs"]').click());
  for (let i = 0; i < 3; i++) { await clic(p, 'Ajouter un joueur fictif'); await attendre(500); }
  const liste = await attendreQue(() => p.evaluate(() => { const t = document.querySelector('.joueurs').innerText; return (t.match(/🤖/g) || []).length === 3 && t; }), 10000);
  verifier(!!liste, 'Léa seule ajoute 3 joueurs fictifs 🤖 : « ' + (liste || '').replace(/\n/g, ' ') + ' »');
  if (CAPTURES) await p.screenshot({ path: CAPTURES + '/solo-salle.png' });
  verifier(!!await clic(p, 'Lancer l’enquête'), '  4 joueurs : elle peut lancer l’enquête');
  verifier(!!await attendreQue(() => p.evaluate(() => !!document.querySelector('.plan3d canvas')), 60000), 'l’enquête commence, maison 3D');
  await p.evaluate(() => document.getElementById('aide').click());
  verifier(!!await attendreQue(() => p.evaluate(() => document.getElementById('regles').classList.contains('ouvert') && document.querySelectorAll('#regles details').length >= 10), 5000),
    '  le bouton ❓ ouvre les règles par-dessus la partie');
  await p.evaluate(() => document.querySelector('#regles header button').click());
  const pe = await attendreQue(() => p.evaluate(() => { const t = document.querySelector('.etat-jeu .pe'); const m = t && t.textContent.match(/(\d+) PE/); return m && Number(m[1]) > 0 && m[0]; }), 240000);
  verifier(!!pe, '  Léa gagne des PE toute seule, sans jouer aux autres jeux (' + pe + ')');
  const r = await (await fetch(API + '/test/bots/' + code + '/3')).json();
  verifier(r.plis > 0, '  les joueurs fictifs jouent : ' + r.plis + ' plis reçus par eux');
  await p.evaluate(() => document.querySelector('nav.onglets button[data-onglet="fil"]').click());
  verifier(!!await attendreQue(() => p.evaluate(() => /pli/.test(document.querySelector('.fil').innerText)), 10000), '  Léa le voit dans le fil du narrateur');
  verifier(await p.evaluate(() => document.getElementById('dossier').children.length === 0), 'aucune trace de console dans la page tant que le dossier est fermé');
  // Appui long AU DOIGT sur « Rôle » pendant que les joueurs fictifs jouent (l'écran change sous le doigt)
  const b = await (await p.$('.bouton-role')).boundingBox();
  const cdp = await ctx.newCDPSession(p);
  const point = [{ x: b.x + b.width / 2, y: b.y + b.height / 2 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: point });
  const ouvert = await attendreQue(() => p.evaluate(() => document.getElementById('role-voile').classList.contains('ouvert')), 5000);
  await p.evaluate(() => { const v = document.querySelector('.bouton-role'); v.replaceWith(v.cloneNode(true)); });   // le bandeau est redessiné
  await fetch(API + '/test/bots/' + code + '/2');
  await attendre(1500);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  const ferme = await attendreQue(() => p.evaluate(() => !document.getElementById('role-voile').classList.contains('ouvert')), 5000);
  verifier(ouvert && ferme, 'le rôle se recache au doigt levé, même si les joueurs fictifs ont joué pendant l’appui');
  await p.evaluate(() => { document.getElementById('role-voile').classList.add('ouvert'); });
  await p.mouse.click(200, 400);
  verifier(!!await attendreQue(() => p.evaluate(() => !document.getElementById('role-voile').classList.contains('ouvert')), 5000), '  et toucher l’écran de rôle le referme dans tous les cas');
  verifier(erreurs.length === 0, 'aucune erreur JavaScript' + (erreurs.length ? ' : ' + erreurs.join(' | ') : ''));
  await nav.close();
  console.log(echecs ? echecs + ' ECHEC(S)' : 'OK');
  process.exit(echecs ? 1 : 0);
})();
