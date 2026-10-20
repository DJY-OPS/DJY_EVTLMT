'use strict';
(() => {
  const form = document.getElementById('geometryForm');
  if (!form) return;
  const ratio = document.getElementById('gearRatio');
  const diameter = document.getElementById('tireDiameterCm');
  const save = document.getElementById('geometrySave');
  const note = document.getElementById('geometryStatus');
  async function request(options) {
    const response = await fetch('/api/vehicle/geometry', {
      cache: 'no-store', signal: AbortSignal.timeout(8000), ...options
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `저장 실패 (${response.status})`);
    return result;
  }
  function fill(geometry) {
    ratio.value = geometry.gear_ratio;
    diameter.value = Number((geometry.tire_diameter_m * 100).toFixed(8));
  }
  request().then(result => {
    fill(result.geometry);
    ratio.disabled = diameter.disabled = save.disabled = !result.can_edit;
    note.textContent = result.can_edit ? '저장하면 다음 유효한 RPM 표본부터 적용됩니다.' : '피트 PC에서 접속하면 변경할 수 있습니다.';
  }).catch(error => { note.textContent = error.message; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity() || save.disabled) return;
    save.disabled = true;
    try {
      const result = await request({method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({gear_ratio: Number(ratio.value), tire_diameter_m: Number(diameter.value) / 100})});
      fill(result.geometry);
      note.textContent = '저장 완료 · 다음 유효한 RPM 표본부터 적용됩니다. 기존 기록은 유지됩니다.';
    } catch (error) { note.textContent = error.message; }
    finally { save.disabled = false; }
  });
})();
