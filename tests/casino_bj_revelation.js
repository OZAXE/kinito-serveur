// Blackjack : l'écran de résultat ne doit apparaître qu'une fois la
// dernière carte du croupier posée (carte cachée retournée, puis chaque
// carte tirée, 0,8 s d'écart). On mesure, pour chaque coup, l'écart entre
// l'arrivée de l'état « resultat » et l'apparition de l'écran de bilan,
// et on vérifie que le bilan n'est pas dans le panneau avant.
//
// Prérequis : python tests/serveur_essai.py et le site sur http://localhost:8765.
// Usage : node tests/casino_bj_revelation.js [coups=4]
const { chromium } = require('playwright');
const API = 'http://127.0.0.1:8000', WS = 'ws://127.0.0.1:8000', SITE = 'http://localhost:8765/';
const COUPS = +process.argv[2] || 4;
const LIMITE = +process.env.LIMITE || 40000;
const attendre = ms => new Promise(r => setTimeout(r, ms));

function bot(code, nom) {
  const ws = new WebSocket(`${WS}/ws/${code}/${encodeURIComponent(nom)}?bat=1&page=casino`);
  let i = null;
  const envoyer = (m, d = 300) => setTimeout(() => { try { ws.send(JSON.stringify(m)); } catch (e) { /* fermé */ } }, d);
  setInterval(() => { try { ws.send('{"type":"ping"}'); } catch (e) { /* fermé */ } }, 4000);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'ton_index') i = m.index;
    if (m.type !== 'cs_etat' || i == null || i >= m.jetons.length) return;
    const tb = m.table || {};
    if (m.phase === 'hall') envoyer({ type: 'cs_choisir', jeu: 'blackjack' }, 1500);
    else if (m.phase === 'mises' && !(tb.prets || [])[i]) { envoyer({ type: 'cs_bj_miser', montant: 200 }); envoyer({ type: 'cs_pret' }, 800); }
    else if (m.phase === 'jeu' && tb.courant === i) envoyer({ type: 'cs_bj_rester' });
  };
  return ws;
}

(async () => {
  const code = (await (await fetch(API + '/creer-salon')).json()).code;
  const nav = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const ctx = await nav.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  await ctx.addInitScript((c) => {
    localStorage.setItem('shield.nom', JSON.stringify('Alice'));
    localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
    localStorage.setItem('shield.leger.propose', '1');
    localStorage.setItem('shield.leger', 'true');   // rendu logiciel lent : version légère
  }, code);
  const p = await ctx.newPage();
  const erreurs = [];
  p.on('pageerror', e => erreurs.push(e.message));
  // Chaque état « resultat » reçu : l'heure et le nombre de cartes du croupier.
  const resultats = [];
  p.on('websocket', ws => ws.on('framereceived', f => {
    try {
      const m = JSON.parse(f.payload);
      if (m.type === 'cs_etat' && m.jeu === 'blackjack' && m.phase === 'resultat' && !resultats.find(r => r.manche === m.manche)) {
        resultats.push({ manche: m.manche, t: Date.now(), cartes: m.table.croupier.length });
      }
    } catch (e) { /* binaire */ }
  }));
  await p.goto(`${SITE}casino-3d-jeu.html?salon=${code}`);
  await attendre(2500);
  const bots = [bot(code, 'Bruno'), bot(code, 'Chloé')];
  await attendre(2000);
  await p.evaluate(() => { const b = [...document.querySelectorAll('#panneau button')].find(x => /D.marrer|Ouvrir/.test(x.textContent) && !x.disabled); if (b) b.click(); });
  await attendre(1200);
  await p.evaluate(() => { const b = [...document.querySelectorAll('[role=dialog] button')].find(x => /Lancer|Oui|Confirmer/.test(x.textContent)); if (b) b.click(); });

  const ips = await p.evaluate(() => new Promise(r => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 2000) requestAnimationFrame(f); else r(n / 2); }; requestAnimationFrame(f); }));
  console.log('images/s :', ips);
  const mesures = [];
  let enAttente = null, dernierClic = 0;
  const fin = Date.now() + LIMITE * COUPS;
  while (mesures.length < COUPS && Date.now() < fin) {
    await attendre(60);
    const cliquer = Date.now() - dernierClic > 1500;
    if (cliquer) dernierClic = Date.now();
    const vu = await p.evaluate((cliquer) => {
      const pan = document.getElementById('panneau');
      const txt = pan ? pan.innerText : '';
      // Alice joue : mise, prête, reste.
      const boutons = pan ? [...pan.querySelectorAll('button')].filter(b => !b.disabled && b.offsetParent) : [];
      const b = boutons.find(x => /^Miser|Prêt|Rester/i.test(x.textContent.trim()));
      if (b && cliquer) b.click();
      return { ecran: !!document.querySelector('.res-voile'), bilanPanneau: !!(pan && pan.querySelector('table.bilan')), abat: /croupier abat/.test(txt) };
    }, cliquer);
    const r = resultats.find(x => !x.vu);
    if (r && !enAttente) { enAttente = r; r.vu = true; r.bilanTot = false; }
    if (enAttente) {
      const ecoule = (Date.now() - enAttente.t) / 1000;
      const posee = 0.45 + (enAttente.cartes > 2 ? 0.7 + (enAttente.cartes - 3) * 0.8 : 0);
      if (vu.bilanPanneau && ecoule < posee) enAttente.bilanTot = true;
      if (vu.ecran) {
        mesures.push({ cartes: enAttente.cartes, ecran: ecoule, posee, bilanTot: enAttente.bilanTot });
        enAttente = null;
        await attendre(1500);
        await p.evaluate(() => { const v = document.querySelector('.res-voile'); if (v) v.click(); });
      } else if (ecoule > 12) { enAttente = null; }   // coup sans écran (égalité : simple annonce)
    }
  }
  console.log("états resultat vus :", resultats.length);
  let ok = mesures.length > 0;
  mesures.forEach(m => {
    const bon = m.ecran >= m.posee && !m.bilanTot;
    ok = ok && bon;
    console.log(`${bon ? 'OK ' : 'KO '} croupier ${m.cartes} cartes : dernière posée à ${m.posee.toFixed(2)} s, écran de résultat à ${m.ecran.toFixed(2)} s${m.bilanTot ? ', BILAN DANS LE PANNEAU TROP TÔT' : ''}`);
  });
  if (erreurs.length) { ok = false; console.log('ERREURS JS : ' + [...new Set(erreurs)].join(' | ')); }
  console.log(ok ? 'Révélation du croupier : OK' : 'Révélation du croupier : ÉCHEC');
  bots.forEach(w => { try { w.close(); } catch (e) { /* fermé */ } });
  await nav.close();
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
