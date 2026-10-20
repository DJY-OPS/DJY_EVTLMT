'use strict';
(() => {
  const el = id => document.getElementById(id);
  const form = el('predictionSettings'), note = el('predictionEditStatus');
  let initialized = false, editable = false, saving = false, revision = 0;
  const number = (n, unit, digits=1) => typeof n === 'number' && Number.isFinite(n) ? `${n.toFixed(digits)} ${unit}` : '—';
  const controls = () => form.querySelectorAll('input, select, button');
  function fill(settings) {
    for (const [key, value] of Object.entries(settings)) form.elements.namedItem(key).value = value;
  }
  function render(data) {
    el('predictionReason').textContent = data.reason;
    el('runtimeMinutes').textContent = data.remaining_minutes === null ? '예측 대기' : data.remaining_minutes === 0 ? '예비량 도달' : `약 ${number(data.remaining_minutes, '분')}`;
    el('runtimeReserve').textContent = `예비 SOC ${data.settings.reserve_soc_pct}%까지 · 최근 30초 소비 기준`;
    el('runtimeRange').textContent = data.range_minutes ? `${data.range_minutes[0].toFixed(1)}–${data.range_minutes[1].toFixed(1)} 분` : '—';
    el('efficiencyWhKm').textContent = number(data.wh_km, 'Wh/km');
    const verdict = el('efficiencyVerdict');
    verdict.textContent = ({good:'좋음', poor:'나쁨', similar:'비슷함', regen:'회생 우세', waiting:'비교 대기'})[data.efficiency] || '비교 대기';
    verdict.classList.toggle('prediction-good', data.efficiency === 'good');
    verdict.classList.toggle('prediction-poor', data.efficiency === 'poor');
    const change = data.efficiency_change_pct;
    el('efficiencyComparison').textContent = change === null ? data.efficiency_reason :
      `직전 ${number(data.reference_wh_km, 'Wh/km')} 대비 ${number(Math.abs(change), '%')} ${change >= 0 ? '덜 소비' : '더 소비'} · 비슷한 평균 속도`;
    el('predictionCurrent').textContent = number(data.mean_current_a, 'A');
    el('predictionAvailable').textContent = number(data.available_ah, 'Ah');
    el('predictionCapacity').textContent = data.capacity_source === 'soc' ? 'SOC × BMS 설정용량 · 참고 추정' : 'BMS 잔여 Ah에서 예비량 제외';
    el('predictionQuality').textContent = `${data.quality} · 최근 유효 구간 ${number(data.observed_seconds, '초')}${data.bms_fault ? ' · BMS 경보 발생' : ''}`;
    el('predictionQuality').classList.toggle('alarm', data.bms_fault === true || data.state === 'inconsistent');
  }
  async function request(payload) {
    const response = await fetch('/api/battery/prediction', {cache:'no-store', signal:AbortSignal.timeout(4000),
      ...(payload ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)} : {})});
    if (!response.ok) {
      const text = await response.text();
      let error; try { error = JSON.parse(text).error; } catch {}
      throw new Error(error || `요청 실패 (${response.status})`);
    }
    return response.json();
  }
  form.onsubmit = async event => {
    event.preventDefault();
    if (!editable || saving || !form.reportValidity()) return;
    const payload = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, Number(value)]));
    saving = true; revision++; controls().forEach(c => c.disabled = true);
    try {
      const data = await request(payload);
      fill(data.settings); render(data);
      note.textContent = '저장 완료 · 새 설정으로 소비 구간을 다시 수집합니다.';
    } catch (error) { note.textContent = error.message; }
    finally { saving = false; controls().forEach(c => c.disabled = !editable); }
  };
  async function poll() {
    const current = revision;
    try {
      if (saving) return;
      const data = await request();
      if (current !== revision || saving) return;
      editable = data.can_edit;
      if (!initialized) {
        fill(data.settings); initialized = true;
        note.textContent = editable ? '피트 PC · 설정은 DB에 저장되며 다음 실행에도 유지됩니다.' : '피트 PC 로컬 접속에서 변경할 수 있습니다.';
      }
      controls().forEach(c => c.disabled = !editable);
      render(data);
    } catch (error) {
      if (current !== revision || saving) return;
      el('predictionReason').textContent = '게이트웨이 연결 끊김 · 현재 예측 확인 불가';
      for (const id of ['runtimeMinutes','runtimeRange','efficiencyWhKm','predictionCurrent','predictionAvailable']) el(id).textContent = '—';
      el('efficiencyVerdict').textContent = '비교 대기';
      el('efficiencyVerdict').classList.remove('prediction-good','prediction-poor');
      el('efficiencyComparison').textContent = '다시 연결하면 갱신합니다.';
      el('predictionQuality').textContent = '';
      editable = false; controls().forEach(c => c.disabled = true);
    } finally { setTimeout(poll, 1000); }
  }
  poll();
})();
