'use strict';
(() => {
  const params = new URLSearchParams(location.search);
  const demo = params.get('demo') === '1';
  const overlay = params.get('overlay') === '1';
  const $ = id => document.getElementById(id);
  document.body.classList.toggle('overlay', overlay);
  $('demoBadge').hidden = !demo;
  $('modeLink').textContent = demo ? '실제 데이터 보기' : '데모 보기';
  $('modeLink').href = demo ? '/onboard' : '/onboard?demo=1';
  $('overlayLink').href = '/onboard?overlay=1' + (demo ? '&demo=1' : '');
  const format = (value, digits = 0) => Number.isFinite(value) ? value.toFixed(digits) : '—';
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const bars = ['rpmLeftBar', 'rpmRightBar'].map(id => {
    const elements = Array.from({length: 20}, () => document.createElement('i'));
    $(id).append(...elements);
    return elements;
  });
  const empty = () => ({online: false, power_on: null, values: {}});
  function render(packet, disconnected = false) {
    const v = packet.values || {};
    const live = packet.online === true;
    const status = $('link').parentElement;
    status.classList.toggle('live', live && !demo);
    status.classList.toggle('demo', demo);
    $('link').textContent = demo ? 'DESIGN PREVIEW' : disconnected ? 'GATEWAY DISCONNECTED' : live ? 'LIVE TELEMETRY' : 'WAITING FOR TELEMETRY';
    $('speed').textContent = format(v.speed_kmh);
    ['left', 'right'].forEach((side, index) => {
      const rpm = v['rpm_' + side];
      $(index ? 'rpmRight' : 'rpmLeft').textContent = format(rpm);
      const count = Number.isFinite(rpm) ? Math.round(clamp(rpm / 8000, 0, 1) * 20) : 0;
      bars[index].forEach((bar, i) => bar.classList.toggle('lit', i < count));
    });
    const power = packet.power_on;
    $('power').textContent = power === true ? 'ON' : power === false ? 'OFF' : '—';
    $('powerHint').textContent = power === true ? '전원 켜짐' : power === false ? '전원 꺼짐' : '미확인';
    $('powerBlock').dataset.power = power === true ? 'on' : power === false ? 'off' : 'unknown';
    $('throttle').textContent = format(v.tps_pct);
    $('throttleFill').style.height = (Number.isFinite(v.tps_pct) ? clamp(v.tps_pct, 0, 100) : 0) + '%';
    const lat = Number.isFinite(v.lateral_accel_m_s2) ? v.lateral_accel_m_s2 / 9.80665 : null;
    const long = Number.isFinite(v.longitudinal_accel_m_s2) ? v.longitudinal_accel_m_s2 / 9.80665 : null;
    $('lat').textContent = format(lat, 2);
    $('long').textContent = format(long, 2);
    const gValid = Number.isFinite(lat) && Number.isFinite(long);
    $('gTotal').textContent = format(gValid ? Math.hypot(lat, long) : null, 2);
    // Clamp only the marker to the circle; numbers retain the measured G.
    const magnitude = gValid ? Math.hypot(lat, long) : 0;
    const scale = magnitude > 2 ? 58 / magnitude : 29;
    const x = gValid ? 70 + lat * scale : 70;
    const y = gValid ? 70 - long * scale : 70;
    $('gDot').setAttribute('visibility', gValid ? 'visible' : 'hidden');
    $('gDot').setAttribute('cx', x);
    $('gDot').setAttribute('cy', y);
    const yaw = Number.isFinite(v.yaw_rate_rad_s) ? v.yaw_rate_rad_s * 180 / Math.PI : null;
    $('yaw').textContent = format(yaw, 1);
    $('steer').textContent = format(v.sas_deg, 1);
  }
  async function poll() {
    try {
      const response = await fetch('/api/onboard', {cache: 'no-store', signal: AbortSignal.timeout(1000)});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const packet = await response.json();
      if (!packet || typeof packet.online !== 'boolean' || !packet.values || typeof packet.values !== 'object') throw new Error('Invalid telemetry');
      render(packet);
    } catch {
      render(empty(), true);
    } finally {
      setTimeout(poll, 100);
    }
  }
  render(empty());
  if (demo) {
    const start = performance.now();
    const tick = () => {
      const t = (performance.now() - start) / 1000;
      const rpm = 4600 + 1800 * Math.sin(t * .45);
      render({online: true, power_on: true, values: {
        speed_kmh: 97 + 38 * Math.sin(t * .45), rpm_left: rpm - 120 * Math.sin(t),
        rpm_right: rpm + 120 * Math.sin(t), tps_pct: 65 + 30 * Math.sin(t * .6),
        lateral_accel_m_s2: 7 * Math.sin(t * .7), longitudinal_accel_m_s2: 3 * Math.cos(t * .45),
        yaw_rate_rad_s: .35 * Math.sin(t * .7), sas_deg: 14 * Math.sin(t * .7)
      }});
    };
    tick();
    setInterval(tick, 100);
  } else poll();
  const video = $('video');
  let videoUrl;
  $('videoFile').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    video.pause();
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    videoUrl = URL.createObjectURL(file);
    video.src = videoUrl;
    $('stage').classList.add('has-video');
    $('play').disabled = false;
    try { await video.play(); } catch { /* User can press play after loading. */ }
  });
  video.addEventListener('error', () => {
    $('stage').classList.remove('has-video');
    $('play').disabled = true;
    $('placeholder').querySelector('p').textContent = '영상을 재생할 수 없습니다. 브라우저가 지원하는 MP4 또는 WebM을 선택하세요.';
  });
  $('play').addEventListener('click', async () => {
    if (video.paused) { try { await video.play(); } catch {} } else video.pause();
  });
  $('fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $('stage').requestFullscreen();
    } catch { /* Fullscreen may be blocked by an embedding browser. */ }
  });
  window.addEventListener('pagehide', () => { if (videoUrl) URL.revokeObjectURL(videoUrl); });
})();
