'use strict';
(() => {
  const $=id=>document.getElementById(id), finite=x=>typeof x==='number'&&Number.isFinite(x);
  const fmt=(x,d=1)=>finite(x)?x.toFixed(d):'—', signed=x=>finite(x)?`${x>0?'+':''}${x.toFixed(2)}`:'—';
  const lapText=x=>{if(!finite(x)||x<0)return '—:—.—';const n=Math.round(x*100);return `${Math.floor(n/6000)}:${((n%6000)/100).toFixed(2).padStart(5,'0')}`;};
  const state={vehicle:null,vehicleAt:-Infinity,gps:null,gpsAt:-Infinity,rows:[],frozen:null,cursor:0,epoch:'',points:[],follow:true};
  const map=L.map('map',{preferCanvas:true}).setView([35.15,126.93],12);
  let marker=null,firstFix=true;
  const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tiles.on('tileerror',()=>{$('tileStatus').textContent='배경 지도 연결 실패 · 주행 경로는 유지';});
  const trail=L.polyline([],{color:'#49dcff',weight:4}).addTo(map),gate=L.polyline([],{color:'#ffcc66',weight:5}).addTo(map);
  L.control.scale({imperial:false}).addTo(map);
  const setFollow=value=>{state.follow=value;$('follow').textContent=`차량 따라가기 ${value?'ON':'OFF'}`;$('follow').setAttribute('aria-pressed',value);};
  map.on('dragstart',()=>setFollow(false));$('follow').onclick=()=>{setFollow(!state.follow);if(state.follow&&marker)map.panTo(marker.getLatLng());};
  async function json(url){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error(r.status);return r.json();}
  function badge(id,text,ok){$(id).textContent=text;$(id).className='badge '+(ok?'good':'waiting');}
  function current(){
    const now=performance.now(),vehicleLive=state.vehicle?.online===true&&now-state.vehicleAt<1500;
    const gpsLive=state.gps?.live===true&&now-state.gpsAt<1500;
    const v=vehicleLive?state.vehicle.values:{};
    const gpsSpeed=gpsLive?state.gps.latest?.speed_kmh:null;
    const wheelSpeed=finite(v.speed_kmh)&&v.speed_kmh>=0?v.speed_kmh:null;
    const speed=wheelSpeed??(finite(gpsSpeed)&&gpsSpeed>=0?gpsSpeed:null);
    return {now,vehicleLive,gpsLive,v,speed,source:wheelSpeed!==null?'RPM 환산':finite(speed)?'GPS 속도':'수신 대기',
      lat:finite(v.lateral_accel_m_s2)?v.lateral_accel_m_s2/9.80665:null,
      long:finite(v.longitudinal_accel_m_s2)?v.longitudinal_accel_m_s2/9.80665:null};
  }
  function gPoint(lat,long){const range=Number($('gScale').value),m=Math.max(1,Math.hypot(lat,long)/range);return [150+lat/m/range*112,150-long/m/range*112];}
  function render(){
    const c=current(),v=c.v;
    badge('vehicleStatus',c.vehicleLive?'● 차량 수신 중':performance.now()-state.vehicleAt<1500?'○ STM 미수신':'○ 차량 연결 대기 / 지연',c.vehicleLive);
    badge('gpsStatus',c.gpsLive?'● GPS 수신 중':'○ GPS 미수신',c.gpsLive);
    $('speed').textContent=fmt(c.speed);$('speedSource').textContent=c.source;
    $('rpmStatus').textContent=`좌 / 우 RPM ${fmt(v.rpm_left,0)} / ${fmt(v.rpm_right,0)}`;
    const tps=finite(v.tps_pct)&&v.tps_pct>=0&&v.tps_pct<=100?v.tps_pct:null;
    $('throttle').textContent=fmt(tps,0);$('throttleFill').style.width=(tps??0)+'%';
    if(tps===null){$('throttleMeter').removeAttribute('aria-valuenow');$('throttleMeter').setAttribute('aria-valuetext','미수신');}
    else{$('throttleMeter').setAttribute('aria-valuenow',tps);$('throttleMeter').setAttribute('aria-valuetext',`${tps.toFixed(0)}%`);}
    $('steering').textContent=finite(v.sas_deg)?`${v.sas_deg>0?'+':''}${v.sas_deg.toFixed(1)}`:'—';
    $('wheel').setAttribute('transform',`rotate(${finite(v.sas_deg)?v.sas_deg:0} 60 60)`);$('wheel').style.opacity=finite(v.sas_deg)?'1':'.25';
    $('lateralG').textContent=signed(c.lat);$('longitudinalG').textContent=signed(c.long);
    const imu=finite(c.lat)&&finite(c.long),range=Number($('gScale').value);
    $('gDot').setAttribute('visibility',imu?'visible':'hidden');$('gVector').setAttribute('visibility',imu?'visible':'hidden');$('gRing').textContent=range.toFixed(1)+' G';
    if(imu){const [x,y]=gPoint(c.lat,c.long);$('gDot').setAttribute('cx',x);$('gDot').setAttribute('cy',y);$('gVector').setAttribute('x2',x);$('gVector').setAttribute('y2',y);}
    $('gPlot').setAttribute('aria-label',imu?`횡 ${signed(c.lat)} G, 종 ${signed(c.long)} G`:'G-포스 미수신');
    $('gStatus').textContent=imu?`수평 합성 ${Math.hypot(c.lat,c.long).toFixed(2)} G${Math.hypot(c.lat,c.long)>range?' · 표시 범위 초과':''}`:'IMU 미수신 · 점 표시 중단';
    const recent=state.rows.filter(r=>c.now-r.t<2000),lastGap=recent.findLastIndex(r=>!finite(r.lat)||!finite(r.long));
    $('gTrail').setAttribute('points',imu?recent.slice(lastGap+1).map(r=>gPoint(r.lat,r.long).join(',')).join(' '):'');
    const g=state.gps;
    $('currentLap').textContent=c.gpsLive?lapText(g.lap_time_s):'—:—.—';
    $('lapNumber').textContent=g?.active?`#${(g.lap_count||0)+1}`:'—';
    $('lastLap').textContent=lapText(g?.last_lap_s);$('bestLap').textContent=lapText(g?.best_lap_s);
    const delta=finite(g?.last_lap_s)&&finite(g?.best_lap_s)?g.last_lap_s-g.best_lap_s:null;
    $('lapDelta').textContent=finite(delta)?`${delta>=0?'+':''}${delta.toFixed(2)} s`:'—';
    const reasons={TIMING:'랩 측정 중',WAITING_CROSSING:'출발선 첫 통과 대기',SAMPLE_GAP:'GPS 공백 · 현재 랩 무효',POSITION_JUMP:'위치 튐 · 현재 랩 무효',LOW_ACCURACY:'GPS 정확도 부족',TIMING_ACCURACY:'랩 측정 정확도 부족',DELAYED_FIX:'늦은 GPS · 현재 랩 무효',NO_SOURCE_TIME:'GPS 측정 시각 없음'};
    $('lapState').textContent=!c.gpsLive?'GPS 수신 대기':!g.gate?'출발선을 설정하세요':!g.active?'랩 측정 시작 대기':reasons[g.reason]||'출발선 통과 대기';
    const p=g?.latest;
    $('mapStatus').textContent=c.gpsLive?`GPS 정확도 ${fmt(p?.accuracy_m)} m` : p?'마지막 위치 · 현재 GPS 미수신':'GPS 위치 수신 대기';
    if(marker)marker.setOpacity(c.gpsLive?1:.35);
    drawCharts();
  }
  async function pollVehicle(){
    try{const d=await json('/api/onboard');if(typeof d.online!=='boolean'||!d.values)throw Error('Invalid telemetry');state.vehicle=d;state.vehicleAt=performance.now();}
    catch{state.vehicle=null;state.vehicleAt=-Infinity;}
    const c=current();state.rows.push({t:c.now,speed:c.speed,source:c.source,tps:c.v.tps_pct,sas:c.v.sas_deg,lat:c.lat,long:c.long});
    state.rows=state.rows.filter(r=>c.now-r.t<65000).slice(-700);render();setTimeout(pollVehicle,150);
  }
  async function pollGps(){let delay=500;
    try{
      const d=await json('/api/gnss/live?after='+state.cursor);
      if(state.epoch&&state.epoch!==d.epoch){state.cursor=0;state.points=[];firstFix=true;}else{state.cursor=d.cursor;state.points.push(...d.points);state.points=state.points.slice(-3000);}
      state.epoch=d.epoch;state.gps=d;state.gpsAt=performance.now();
      const parts=[];let part=[],key='';
      for(const p of state.points){const next=`${p.segment}:${p.source_id}:${p.stream_id}`;if(next!==key||!p.accepted){if(part.length)parts.push(part);part=[];}key=next;if(p.accepted)part.push([p.lat,p.lon]);}if(part.length)parts.push(part);trail.setLatLngs(parts);
      gate.setLatLngs(d.gate?[d.gate.a,d.gate.b]:[]);
      const p=d.latest;
      if(p?.accepted&&finite(p.lat)&&finite(p.lon)){
        const point=[p.lat,p.lon];
        if(!marker)marker=L.marker(point,{icon:L.divIcon({className:'',html:'<div class="car-dot"></div>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);else marker.setLatLng(point);
        if(d.live&&firstFix){map.setView(point,17);firstFix=false;}else if(d.live&&state.follow)map.panTo(point,{animate:false});
      }
      if(d.more)delay=0;
    }catch{state.gpsAt=-Infinity;}
    render();setTimeout(pollGps,delay);
  }
  function drawCharts(){
    const now=state.frozen?.end??performance.now(),start=now-Number($('window').value)*1000;
    const rows=(state.frozen?.rows??state.rows).filter(r=>r.t>=start&&r.t<=now);
    const charts=[
      ['speedChart',['speed'],['#49dcff'],null],
      ['throttleChart',['tps'],['#9bea81'],[0,100]],
      ['steeringChart',['sas'],['#ffcc66'],null],
      ['gChart',['lat','long'],['#e788ff','#49dcff'],[-Number($('gScale').value),Number($('gScale').value)]]
    ];
    for(const [id,keys,colors,bounds] of charts){
      const canvas=$(id),w=canvas.clientWidth,h=canvas.clientHeight,dpr=devicePixelRatio||1;
      canvas.width=w*dpr;canvas.height=h*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
      const values=rows.flatMap(r=>keys.map(k=>r[k])).filter(finite),extent=Math.max(1,...values.map(Math.abs));
      const [lo,hi]=bounds??(id==='steeringChart'?[-extent*1.1,extent*1.1]:[0,extent*1.1]);
      const x=t=>36+(t-start)/(now-start)*Math.max(1,w-44),y=v=>h-18-(v-lo)/(hi-lo)*(h-30);
      ctx.font='10px Consolas,monospace';ctx.fillStyle='#b0bfd2';ctx.strokeStyle='#29384c';ctx.lineWidth=1;
      for(let i=0;i<=2;i++){const v=lo+(hi-lo)*i/2;ctx.beginPath();ctx.moveTo(34,y(v));ctx.lineTo(w,y(v));ctx.stroke();ctx.fillText(v.toFixed(id==='gChart'?1:0),0,y(v)+3);}
      ctx.save();ctx.beginPath();ctx.rect(34,8,w-34,h-24);ctx.clip();
      keys.forEach((key,i)=>{ctx.strokeStyle=colors[i];ctx.lineWidth=2;ctx.beginPath();let prev=null;
        for(const r of rows){if(!finite(r[key])){prev=null;continue;}if(!prev||r.t-prev.t>1500||(key==='speed'&&r.source!==prev.source))ctx.moveTo(x(r.t),y(r[key]));else ctx.lineTo(x(r.t),y(r[key]));prev=r;}ctx.stroke();});
      ctx.restore();ctx.fillStyle='#b0bfd2';ctx.fillText('-'+$('window').value+'s',36,h-2);ctx.textAlign='right';ctx.fillText(state.frozen?'정지 시점':'현재',w,h-2);
    }
    $('traceStatus').textContent=state.frozen?'그래프 정지 · 상단 계기와 지도는 계속 실시간 갱신됩니다.':'최근 입력 변화 비교 · 속도는 RPM 환산 우선, 유효하지 않으면 GPS 속도';
    $('traceStatus').className='sub'+(state.frozen?' frozen':'');
  }
  $('pause').onclick=()=>{state.frozen=state.frozen?null:{end:performance.now(),rows:state.rows.slice()};$('pause').textContent=state.frozen?'그래프 재개':'그래프 일시정지';$('pause').setAttribute('aria-pressed',!!state.frozen);render();};
  $('gScale').onchange=$('window').onchange=render;
  $('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('fullscreen').textContent='전체 화면 불가';}};
  window.addEventListener('resize',()=>{map.invalidateSize();render();});
  render();pollVehicle();pollGps();setInterval(render,250);
})();
