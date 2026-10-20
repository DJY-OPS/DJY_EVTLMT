'use strict';
const $ = id => document.getElementById(id);
let state = null, lastStatus = 0, active = null, stopping = false, pending = Promise.resolve();
let sequence = Date.now() & 255;
let contract = null;
const parameterNodes = new Map();
const nextSequence = () => sequence = (sequence + 1) & 255;
const controls = ['strength', 'limit', 'enabled'];
function choice() {
  return {strength_pct: Number($('strength').value), limit_pct: Number($('limit').value), tv_enable: $('enabled').checked};
}
function updateChoice() {
  const c = choice();
  $('strengthOut').textContent = `${c.strength_pct}%`;
  $('limitOut').textContent = `${c.limit_pct}%`;
  $('target').textContent = `${c.tv_enable ? Math.min(c.strength_pct, c.limit_pct) : 0}%`;
  $('formula').textContent = c.tv_enable ? `min(요청 ${c.strength_pct}%, 상한 ${c.limit_pct}%)` : '차동 개입 해제 → 0%';
  $('targetBar').style.width = `${c.tv_enable ? Math.min(c.strength_pct, c.limit_pct) : 0}%`;
  $('capMarker').style.left = `${c.limit_pct}%`;
  renderAvailability();
}
function ready(c = choice()) {
  return !!(contract && state && Date.now() - lastStatus < 1000 && state.control_client_allowed &&
    state.online && state.stm_online && state.live_control_allowed &&
    (!state.fault_code || !c.tv_enable || c.strength_pct === 0));
}
function renderAvailability() {
  $('hold').disabled = stopping || (!active && !ready());
  for (const id of controls) $(id).disabled = !!active || stopping;
  $('hold').classList.toggle('active', !!active);
  $('hold').textContent = active ? '적용 유지 중 · 놓으면 해제' : '누르는 동안 적용';
}
async function post(command) {
  const response = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(command), signal:AbortSignal.timeout(800), cache:'no-store'});
  if (!response.ok) throw new Error(`명령 전송 HTTP ${response.status}`);
  const result = await response.json();
  if (!result.ok) throw new Error(result.command_message || '명령 거부');
  return result;
}
async function pulse(session) {
  if (active !== session) return;
  if (!ready(session.command.values)) { void stop(); return; }
  try {
    pending = post(session.command);
    await pending;
    if (active === session) session.timer = setTimeout(() => pulse(session), 150);
  } catch (error) {
    $('message').textContent = error.message;
    if (active === session) void stop();
  }
}
function start() {
  if (active || stopping || !ready()) return;
  active = {command:{type:'parameter_update', schema_version:contract.schema_version,
    persistence:'volatile_lease', request_id:nextSequence(), values:choice()}, timer:null};
  renderAvailability();
  void pulse(active);
}
async function stop() {
  if (!active) return;
  const session = active; active = null; stopping = true;
  clearTimeout(session.timer); renderAvailability();
  // Preserve ordering: an older in-flight request must finish before release.
  try { await pending; } catch (_) { /* server timeout also releases the lease */ }
  try { await post({type:'parameter_update', schema_version:contract.schema_version,
    persistence:'volatile_lease', request_id:nextSequence(), values:{strength_pct:0,
      limit_pct:session.command.values.limit_pct, tv_enable:false}}); }
  catch (error) { $('message').textContent = `원격 해제 전송 확인 불가: ${error.message}. 통신 만료 후 STM 기본값으로 복귀합니다.`; }
  finally { stopping = false; renderAvailability(); }
}
async function loadCatalog() {
  const response = await fetch('/api/control/catalog', {cache:'no-store', signal:AbortSignal.timeout(800)});
  if (!response.ok) throw new Error('조정 항목 정보를 불러올 수 없습니다.');
  const data = await response.json();
  if (data.schema_version !== 1 || !Array.isArray(data.parameters)) throw new Error('지원하지 않는 설정 규격입니다.');
  for (const [key, id] of [['strength_pct','strength'], ['limit_pct','limit']]) {
    const p = data.parameters.find(item => item.key === key);
    if (!p || !p.writable || p.min !== 0 || p.max !== 100 || p.step !== 5) throw new Error('현재 TV 조정 규격을 확인할 수 없습니다.');
    $(id).min = p.min; $(id).max = p.max; $(id).step = p.step;
    $(`${id}Help`).textContent = p.description;
  }
  if (!data.parameters.some(p => p.key === 'tv_enable' && p.writable)) throw new Error('차동 허용 기능을 확인할 수 없습니다.');
  parameterNodes.clear(); $('pidParameters').replaceChildren(); $('futureParameters').replaceChildren();
  for (const p of data.parameters.filter(item => item.group !== 'differential')) {
    const card = document.createElement('article'); card.className = 'parameter';
    const title = document.createElement('h3'); title.textContent = p.label;
    const value = document.createElement('strong'); value.className = 'value'; value.textContent = '—';
    const badge = document.createElement('span'); badge.className = 'tag';
    badge.textContent = p.access === 'planned' ? '지원 준비 중' : '읽기 전용';
    const description = document.createElement('p'); description.textContent = p.description;
    card.append(title, value, badge, description);
    $(p.group === 'pid' ? 'pidParameters' : 'futureParameters').append(card);
    parameterNodes.set(p.key, {definition:p, value});
  }
  contract = data;
}
function renderParameters(connected) {
  for (const {definition:p, value:node} of parameterNodes.values()) {
    const value = connected && p.telemetry ? (p.group === 'pid' ? state._tuning_values?.[p.telemetry] : state[p.telemetry]) : null;
    node.textContent = typeof value === 'number' && Number.isFinite(value) ? `${Number(value.toFixed(3))}${p.unit ? ' '+p.unit : ''}` : '—';
  }
}
async function refresh() {
  try {
    if (!contract) await loadCatalog();
    const response = await fetch('/api/tv', {cache:'no-store', signal:AbortSignal.timeout(800)});
    if (!response.ok) throw new Error(`상태 조회 HTTP ${response.status}`);
    state = await response.json(); lastStatus = Date.now();
    const connected = state.online && state.stm_online;
    renderParameters(connected);
    $('transport').textContent = connected ? ({USB:'ESP USB', WIFI_UDP:'핫스팟 직접 무선', INTERNET_RELAY:'인터넷 HTTPS'}[state.telemetry_transport] || '확인 중') : '—';
    $('link').textContent = connected ? (state.live_control_allowed ? 'STM 연결 · 송수신 가능' : 'STM 연결 · ESP 수신 전용') : 'STM 데이터 없음';
    $('actual').textContent = connected ? `${state.tv_applied_pct ?? '—'}% / ${state.tv_limit_pct ?? '—'}%` : '—';
    $('activity').textContent = !connected ? '—' : state.tv_active ? 'TV 개입 중' : state.ed_active ? '전자식 차동 개입 중' : '현재 개입 없음';
    $('sequence').textContent = `${state.command_expected_seq ?? '—'} / ${state.config_seq ?? '—'} · ${state.command_status ?? '대기'}`;
    $('counts').textContent = `${state.rear_command_rx ?? '—'} / ${state.rear_command_errors ?? '—'}`;
    $('fault').textContent = connected ? String(state.fault_code) : '—';
    $('fault').classList.toggle('alarm', !!state.fault_code && connected);
    $('commandBadge').textContent = active ? '원격 유지 중' : '원격 유지 해제';
    $('permission').textContent = !state.control_client_allowed ? '조회 전용 · 피트 PC 로컬 화면에서 조정하세요.' :
      state.fault_code ? '차량 오류 상태 · 0% 또는 차동 해제만 허용' : '5% 단위 · 버튼을 누르는 동안 유지';
    $('message').textContent = !state.control_client_allowed ? '이 접속은 조회 전용이거나 서버 제어가 비활성화되어 있습니다.' :
      state.fault_code ? `차량 오류 ${state.fault_code}: 0% 또는 차동 해제 명령만 허용됩니다.` :
      (state.command_message || '조정값을 고른 뒤 적용 버튼을 누르고 계세요.');
    if (active && !ready(active.command.values)) void stop();
  } catch (error) {
    state = null; $('link').textContent = '연결 끊김'; $('actual').textContent = '—';
    $('transport').textContent = '—'; $('activity').textContent = '—'; renderParameters(false);
    $('message').textContent = error.message; void stop();
  } finally { renderAvailability(); setTimeout(refresh, 250); }
}
for (const id of controls) $(id).addEventListener('input', updateChoice);
$('hold').addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  event.preventDefault(); $('hold').setPointerCapture(event.pointerId); start();
});
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) $('hold').addEventListener(name, () => void stop());
$('hold').addEventListener('keydown', event => {
  if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) start(); }
});
$('hold').addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key)) void stop(); });
window.addEventListener('blur', () => void stop());
window.addEventListener('pagehide', () => void stop());
document.addEventListener('visibilitychange', () => { if (document.hidden) void stop(); });
updateChoice(); void refresh();
