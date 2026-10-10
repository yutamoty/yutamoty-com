/* ===== 判定ロジック(DOM非依存) ===== */
const R_LAT = 110540, R_LON = 111320;

function proj(lat, lon, lat0, lon0) {
  return [ (lon - lon0) * Math.cos(lat0 * Math.PI / 180) * R_LON, (lat - lat0) * R_LAT ];
}

const NEAR_M = 400; // 現在地からこの距離以内にかかる区間だけを判定に使う

// streets.json の座標(1e-5度の整数。先頭だけ絶対値、以降は差分)を [{name, pts:[[lat,lon],...]}] に戻す
function decodeStreets(doc) {
  const ways = [];
  for (const st of doc.streets) {
    for (const w of st.w) {
      let la = w[0], lo = w[1];
      const pts = [[la / 1e5, lo / 1e5]];
      for (let i = 2; i < w.length; i += 2) { la += w[i]; lo += w[i + 1]; pts.push([la / 1e5, lo / 1e5]); }
      ways.push({ name: st.n, pts });
    }
  }
  return ways;
}

// 通りの折れ線を、現在地を原点(x=東, y=北, m)とする通り(名前+向き)にまとめる
function buildStreets(ways, lat0, lon0) {
  const map = new Map();
  for (const way of ways) {
    const nm = way.name;
    const pts = way.pts.map(([la, lo]) => proj(la, lo, lat0, lon0));
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1];
      const dx = bx - ax, dy = by - ay;
      if (Math.hypot(dx, dy) < 1) continue;
      if (pointSeg(0, 0, ax, ay, bx, by).d > NEAR_M) continue;
      const ang = Math.atan2(Math.abs(dx), Math.abs(dy)); // 0=南北, π/2=東西
      const orient = ang < Math.PI / 6 ? 'NS' : ang > Math.PI / 3 ? 'EW' : null;
      if (!orient) continue; // 斜めの区間は碁盤の目として扱わない
      const key = nm + '|' + orient;
      if (!map.has(key)) map.set(key, { name: nm, orient, segs: [] });
      map.get(key).segs.push([ax, ay, bx, by]);
    }
  }
  return [...map.values()];
}

function pointSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  const x = ax + t * dx, y = ay + t * dy;
  return { d: Math.hypot(px - x, py - y), x, y };
}

function segInt(s1, s2) {
  const [x1, y1, x2, y2] = s1, [x3, y3, x4, y4] = s2;
  const den = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3);
  if (Math.abs(den) < 1e-9) return null;
  const t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / den;
  const u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / den;
  if (t < -0.02 || t > 1.02 || u < -0.02 || u > 1.02) return null;
  return { x: x1 + t * (x2 - x1), y: y1 + t * (y2 - y1) };
}

const shortName = n => n.replace(/通$/, '');

function nearestOn(street) {
  let best = { d: Infinity };
  for (const s of street.segs) {
    const r = pointSeg(0, 0, ...s);
    if (r.d < best.d) best = r;
  }
  return best;
}

// 1本の通りについて「〇〇通 △△上る」形式を作る
function describe(S, streets) {
  const np = nearestOn(S);
  const crosses = [];
  for (const t of streets) {
    if (t.orient === S.orient || t.name === S.name) continue;
    let best = null;
    for (const s1 of S.segs) for (const s2 of t.segs) {
      const ip = segInt(s1, s2);
      if (!ip) continue;
      const dd = S.orient === 'NS' ? Math.abs(ip.y - np.y) : Math.abs(ip.x - np.x);
      if (!best || dd < best.dd) best = { ...ip, dd };
    }
    if (best) crosses.push({ name: t.name, ...best });
  }
  crosses.sort((a, b) => a.dd - b.dd);
  const base = { street: S.name, orient: S.orient, dist: np.d };
  const c = crosses[0];
  if (!c) return { ...base, text: S.name, note: '交差する通りが見つかりません' };
  let verb;
  if (S.orient === 'NS') verb = (c.y - np.y) < 0 ? '上る' : '下る';
  else verb = (c.x - np.x) < 0 ? '東入る' : '西入る';
  const atCross = c.dd < 15;
  return {
    ...base, cross: c.name, crossDist: c.dd, atCross,
    text: atCross ? `${S.name}${shortName(c.name)}` : `${S.name}${shortName(c.name)}${verb}`
  };
}

function analyze(streets) {
  const ns = streets.filter(s => s.orient === 'NS');
  const ew = streets.filter(s => s.orient === 'EW');
  if (!ns.length || !ew.length) return { ok: false };
  const pick = list => list.map(s => ({ s, d: nearestOn(s).d })).sort((a, b) => a.d - b.d)[0].s;
  const items = [describe(pick(ns), streets), describe(pick(ew), streets)].sort((a, b) => a.dist - b.dist);
  const [m, a] = items;
  const intersection = m.dist < 25 && a.dist < 25;
  return {
    ok: true, intersection, items,
    main: intersection ? items.slice().sort((x, y) => (x.orient === 'NS' ? -1 : 1) - (y.orient === 'NS' ? -1 : 1)).map(i => shortName(i.street)).join('') : m.text,
    far: m.dist > 150
  };
}

if (typeof module !== 'undefined') module.exports = { proj, decodeStreets, buildStreets, analyze, describe };

/* ===== ブラウザ側(データ読み込み・UI) ===== */
if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  let bounds = null;     // [南, 西, 北, 東]
  let ways = null;
  let loading = null;
  let watchId = null;

  // 内蔵データ(streets.json)を一度だけ読み込む。外部サーバーとは通信しない
  function loadData() {
    if (!loading) {
      loading = fetch(document.getElementById('street-app').dataset.src)
        .then(res => { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
        .then(doc => { bounds = doc.bounds; ways = decodeStreets(doc); })
        .catch(e => { loading = null; throw e; });
    }
    return loading;
  }

  const inBounds = (lat, lon) =>
    lat >= bounds[0] && lon >= bounds[1] && lat <= bounds[2] && lon <= bounds[3];

  /* ===== 地図(Leaflet + 地理院タイル) ===== */
  const DEFAULT_CENTER = [35.01177, 135.76830]; // 京都市役所前あたり
  const LINE_COLORS = { NS: '#0969da', EW: '#cf222e' };
  let map = null, pin = null, accCircle = null, lines = null;

  // 判定用のローカル座標(x=東, y=北, m)を緯度経度に戻す
  const unproj = (x, y, lat0, lon0) =>
    [lat0 + y / R_LAT, lon0 + x / (Math.cos(lat0 * Math.PI / 180) * R_LON)];

  function initMap() {
    if (typeof L === 'undefined') { $('street-map').hidden = true; return; } // 地図が読めなくても通り名は出す
    map = L.map('street-map').setView(DEFAULT_CENTER, 15);
    L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png', {
      attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
      minZoom: 12, maxNativeZoom: 18, maxZoom: 19
    }).addTo(map);
    lines = L.layerGroup().addTo(map);
    map.on('click', e => { setPin(e.latlng.lat, e.latlng.lng); run(e.latlng.lat, e.latlng.lng, null, true); });
  }

  // データの範囲を地図に重ねる(この外は「碁盤の目の外」)
  function initDataLayers() {
    if (!map) return;
    L.rectangle([[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
      { color: '#999', weight: 1, dashArray: '4 4', fill: false, interactive: false }).addTo(map);
    map.setMaxBounds(L.latLngBounds([bounds[0], bounds[1]], [bounds[2], bounds[3]]).pad(0.5));
  }

  function setPin(lat, lon) {
    if (pin) { pin.setLatLng([lat, lon]); return; }
    pin = L.marker([lat, lon], {
      draggable: true, title: 'ドラッグして場所を動かせます',
      icon: L.divIcon({ className: 'street-pin-wrap', html: '<div class="street-pin"></div>', iconSize: [24, 24], iconAnchor: [12, 29] })
    }).addTo(map);
    let queued = false;
    pin.on('drag', () => { // ドラッグ中も結果を追従させる(描画は1フレームに1回)
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; const c = pin.getLatLng(); run(c.lat, c.lng, null, true); });
    });
    pin.on('dragend', () => { const c = pin.getLatLng(); run(c.lat, c.lng, null, true); });
  }

  // 判定結果を地図に反映する(ピン・誤差の円・判定に使った2本の通り)
  function showOnMap(r, list, lat, lon, acc, fromMap) {
    if (!map) return;
    if (!fromMap && inBounds(lat, lon)) {
      const first = !pin;
      setPin(lat, lon);
      map.setView([lat, lon], first ? 17 : map.getZoom());
    }
    if (accCircle) { map.removeLayer(accCircle); accCircle = null; }
    if (acc && !fromMap && inBounds(lat, lon)) {
      accCircle = L.circle([lat, lon], { radius: acc, color: '#0969da', weight: 1, fillOpacity: 0.08, interactive: false }).addTo(map);
    }
    lines.clearLayers();
    if (!r.ok) return;
    for (const it of r.items) {
      const st = list.find(x => x.name === it.street && x.orient === it.orient);
      if (!st) continue;
      const tip = document.createElement('span'); // Leaflet は文字列を HTML として扱うので、要素で渡す
      tip.textContent = it.street;
      L.polyline(st.segs.map(([ax, ay, bx, by]) => [unproj(ax, ay, lat, lon), unproj(bx, by, lat, lon)]),
        { color: LINE_COLORS[it.orient], weight: 5, opacity: 0.75 })
        .bindTooltip(tip, { sticky: true }).addTo(lines);
    }
  }

  function render(r, lat, lon, acc) {
    $('alts').innerHTML = '';
    $('warn').textContent = '';
    const accTxt = acc ? `(位置の誤差 約${Math.round(acc)}m)` : '';
    $('status').textContent = `緯度 ${lat.toFixed(5)} / 経度 ${lon.toFixed(5)} ${accTxt}`;
    if (r.outside) {
      $('main-text').textContent = '碁盤の目の外です';
      $('main-sub').textContent = 'このツールが持っている洛中の地図データの範囲外です。';
      return;
    }
    if (!r.ok) {
      $('main-text').textContent = '碁盤の目の外です';
      $('main-sub').textContent = '周辺に東西・南北の通りの組が見つかりませんでした。';
      return;
    }
    $('main-text').textContent = r.main;
    const m = r.items[0];
    $('main-sub').textContent = r.intersection ? '' :
      `${m.street}まで約${Math.round(m.dist)}m` + (m.crossDist != null ? ` / ${shortName(m.cross)}まで約${Math.round(m.crossDist)}m` : '');
    if (!r.intersection) {
      const a = r.items[1];
      // 通り名は外部(OSM)のデータ由来なので、HTML としては解釈させず textContent で入れる
      const lab = document.createElement('div');
      lab.className = 'street-label street-alts-label';
      lab.textContent = `別の言い方(${a.street}から見ると)`;
      const alt = document.createElement('div');
      alt.className = 'street-alt';
      alt.textContent = `${a.text}${a.atCross ? '(交差点の近く)' : ''}`;
      $('alts').append(lab, alt);
    }
    if (r.far) $('warn').textContent = '最寄りの通りまで離れています。碁盤の目の外かもしれません。';
    else if (acc && acc > 50) $('warn').textContent = '位置の誤差が大きいため、通り名がずれる可能性があります。';
  }

  async function run(lat, lon, acc, fromMap) {
    $('status').textContent = '通りを調べています…';
    try {
      await loadData();
    } catch (e) {
      $('main-text').textContent = '地図データを読み込めませんでした';
      $('main-sub').textContent = 'streets.json を取得できません。';
      $('warn').textContent = e.message + (location.protocol === 'file:' ? ' / ※ファイルを直接開いています。Webサーバー経由で開いてください' : '');
      $('alts').innerHTML = '';
      return;
    }
    const inside = inBounds(lat, lon);
    const list = inside ? buildStreets(ways, lat, lon) : null;
    const r = inside ? analyze(list) : { ok: false, outside: true };
    render(r, lat, lon, acc);
    showOnMap(r, list, lat, lon, acc, fromMap);
  }

  function locate() {
    if (!navigator.geolocation) { $('status').textContent = 'この端末では位置情報が使えません'; return; }
    if (!window.isSecureContext) { $('status').textContent = '位置情報はHTTPSで開いたページでのみ使えます'; return; }
    $('status').textContent = '位置情報を取得しています…';
    navigator.geolocation.getCurrentPosition(
      p => run(p.coords.latitude, p.coords.longitude, p.coords.accuracy),
      err => {
        $('status').textContent = '位置情報を取得できませんでした(' + err.message + ')';
        $('main-text').textContent = '—';
        $('main-sub').textContent = err.code === 1 ? '位置情報の許可が必要です。ブラウザの設定で許可してから「もう一度調べる」を押すか、地図をタップして場所を指定してください。' : '「もう一度調べる」を押すと再試行します。地図をタップして場所を指定することもできます。';
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 }
    );
  }

  let lastRun = 0;
  function toggleWatch() {
    if (watchId != null) {
      navigator.geolocation.clearWatch(watchId); watchId = null;
      $('btn-watch').textContent = '歩きながら自動更新する'; return;
    }
    $('btn-watch').textContent = '自動更新を止める';
    watchId = navigator.geolocation.watchPosition(p => {
      if (Date.now() - lastRun < 5000) return;
      lastRun = Date.now();
      run(p.coords.latitude, p.coords.longitude, p.coords.accuracy);
    }, err => { $('status').textContent = '位置情報エラー: ' + err.message; },
    { enableHighAccuracy: true });
  }

  initMap();
  loadData().then(initDataLayers).catch(() => {}); // 先に読み込んでおく(失敗は run() 側で表示)
  $('btn-locate').onclick = locate;
  locate(); // アクセスしたらすぐ現在地を調べる
  $('btn-watch').onclick = toggleWatch;
  $('presets').onchange = e => {
    if (!e.target.value) return;
    const [la, lo] = e.target.value.split(','); $('in-lat').value = la; $('in-lon').value = lo;
  };
  $('btn-manual').onclick = () => {
    const la = parseFloat($('in-lat').value), lo = parseFloat($('in-lon').value);
    if (isNaN(la) || isNaN(lo)) { $('status').textContent = '緯度・経度を数値で入力してください'; return; }
    run(la, lo, null);
  };
}
