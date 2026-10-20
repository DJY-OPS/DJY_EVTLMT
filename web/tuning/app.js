'use strict';
const $ = id => document.getElementById(id);
const batteryPage = Boolean(window.batteryDashboard);
const api = batteryPage ? '/api/battery' : '/api/tuning';
const channelStorage = batteryPage ? 'ev-battery-channels-v2' : 'ev-tuning-channels-v3';
const colors = ['#00ffff','#ffff00','#00ff00','#ff6666','#ff66ff','#ffffff','#ffb347','#80bfff','#c5ff80','#ff99bb','#b399ff','#80ffd4','#e6cf8b','#a8c8d8','#c8a0a0','#00bfff'];
const groups = batteryPage ? window.batteryDashboard.groups : {tv:'토크벡터링 / Yaw·출력·RPM·개입 상태',steering:'운전자 입력 / 조향·스로틀',speed:'차량 응답 / 차속·가속도·모터 전압',battery_summary:'배터리 요약 / 전압·전류·전력·잔량'};
const groupTitle = group => groups[group] || (group==='readouts'?'W / kW':group==='settings'?'PID 보드 보고값':'STM 차속 원본');
const color = c => colors[state.channels.filter(x=>x.group===c.group).indexOf(c)%colors.length];
const rpmQualities = {OK:'펄스 품질 통과 · 실회전수 보정은 별도',NOISY:'노이즈 의심 · 실제 회전수로 사용 불가',NO_PULSES:'새 펄스 없음 · 정지/단선 구분 불가',UNVERIFIED:'펄스 품질 확인 중',NO_DATA:'현재 수신값 없음'};
const rpmStatusKeys = ['rpm_left_quality','rpm_right_quality','rpm_left_rejected_pct','rpm_right_rejected_pct','motor_command_mode','speed_geometry'];
const state = {channels:[],enabled:new Set(),live:[],history:[],mode:'live',paused:false,cursor:0,epoch:'',now:0,recording:false,session:0,through:0,pages:[0],page:0,more:false,busyHistory:false,generation:0,hover:null,track:[],lastFix:''};
state.pausedRows = null;
state.pausedEnd = null;
state.lockedSample = null;
state.plotView = null;
state.recordBusy = false;
state.recordError = '';
$('record').insertAdjacentHTML('afterend','<div id="recordFeedback" class="record-feedback" data-state="idle"><strong id="recordFeedbackTitle" role="status">기록 상태 확인 중</strong><div class="record-feedback-stats"><span id="recordFeedbackClock">00:00:00</span><span id="recordFeedbackCount">0개 저장</span></div><small id="recordFeedbackDetail">서버 연결 확인 중</small></div>');
const finite = x => typeof x === 'number' && Number.isFinite(x);
const format = (v,d=3) => finite(v)?v.toFixed(d):'미수신';
const time = t => new Date(t).toLocaleTimeString('en-GB',{hour12:false})+'.'+String(Math.floor(t)%1000).padStart(3,'0');
async function json(url,options={}) {
  const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(8000),...options});
  if(!response.ok) throw new Error(`요청 실패 (${response.status})`);
  return response.json();
}
function init(channels) {
  if(batteryPage)channels=channels.filter(c=>/^(battery_|bms_)/.test(c.key)&&c.key!=='battery_power_w');
  channels=channels.map(c=>({...c,group:batteryPage?window.batteryDashboard.groupFor(c):
    c.key.startsWith('battery_')?'battery_summary':['yaw','power','rpm','traction','activity'].includes(c.group)?'tv':c.group==='throttle'?'steering':['acceleration','voltage'].includes(c.group)?'speed':c.group}));
  state.channels=channels;
  let saved=null;try{saved=JSON.parse(localStorage.getItem(channelStorage));}catch{}
  state.enabled=new Set(Array.isArray(saved)?saved:channels.map(c=>c.key));
  const containers={};
  for(const group of [...new Set([...Object.keys(groups),...channels.map(c=>c.group)])]){
    const section=document.createElement('details');section.className='channel-group';section.open=innerWidth>900;
    const title=document.createElement('summary');title.textContent=groupTitle(group);section.append(title);
    $('channels').append(section);containers[group]=section;
  }
  channels.forEach(c=>{
    const label=document.createElement('label');label.className='channel';
    const input=document.createElement('input');input.type='checkbox';input.checked=state.enabled.has(c.key);
    input.onchange=()=>{input.checked?state.enabled.add(c.key):state.enabled.delete(c.key);try{localStorage.setItem(channelStorage,JSON.stringify([...state.enabled]));}catch{}draw();};
    const text=document.createElement('span');text.style.color=color(c);text.textContent=c.label;
    const value=document.createElement('b');value.id='value-'+c.key;value.textContent='미수신';
    const range=document.createElement('small');range.id='range-'+c.key;
    text.append(value,range);label.append(input,text);containers[c.group].append(label);
  });
  const plotParents={};
  const sections=batteryPage?{battery:['배터리 상태',Object.keys(groups)]}:{tv:['토크벡터링 제어',['tv']],vehicle:['운전자 입력 · 차량 응답',['steering','speed']],battery:['배터리 요약',['battery_summary']]};
  for(const [key,[title,groupKeys]] of Object.entries(sections)){
    const section=document.createElement('section');section.className='graph-section';section.id=key+'-graphs';
    const heading=document.createElement('h2');heading.textContent=title;
    const grid=document.createElement('div');grid.className='plot-grid';
    section.append(heading);
    if(!batteryPage&&key==='battery'){
      const summary=document.createElement('p');summary.id='battery';summary.setAttribute('role','status');summary.textContent='BMS 수신 대기';
      const link=document.createElement('a');link.href='/battery';link.textContent='셀 전압 · 온도 · MOS · 알람 상세 ↗';
      section.append(summary,link);
    }
    section.append(grid);$('plots').append(section);groupKeys.forEach(group=>plotParents[group]=grid);
  }
  for(const [group,title] of Object.entries(groups)){
    const section=document.createElement('section');section.className='plot';section.id='plot-'+group;
    const heading=document.createElement('h2');heading.textContent=title;
    const legend=document.createElement('div');legend.className='legend';
    channels.filter(c=>c.group===group).forEach(c=>{
      const label=document.createElement('span');label.style.color=color(c);label.textContent=c.label;
      const value=document.createElement('b');value.id='plot-value-'+c.key;label.append(value);legend.append(label);
    });
    const stamp=document.createElement('small');stamp.id='plot-time-'+group;stamp.className='plot-time';
    const canvas=document.createElement('canvas');canvas.id='canvas-'+group;canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title+' 시간 그래프');
    section.append(heading,stamp,legend,canvas);plotParents[group].append(section);
    const pointTime=e=>{const rect=canvas.getBoundingClientRect(),view=state.plotView||viewport(),bounds=canvas.plotBounds||{left:72,right:rect.width-16};const fraction=Math.max(0,Math.min(1,(e.clientX-rect.left-bounds.left)/(bounds.right-bounds.left)));return view.start+(view.end-view.start)*fraction;};
    canvas.onpointermove=e=>{if(state.lockedSample!==null)return;state.hover=pointTime(e);draw();};
    canvas.onpointerleave=()=>{state.hover=null;draw();};
    canvas.onclick=e=>{
      if(e.button!==0)return;
      const row=nearest((state.plotView||viewport()).rows,pointTime(e));
      if(!row)return;
      // Keep the selected sample even after it leaves the moving live buffer.
      state.lockedSample=row;state.hover=null;draw();
    };
  }
}
function viewport(){
  const rows=state.mode==='live'?(state.pausedRows||state.live):state.history;
  const first=rows[0]?.time_ms??state.now;
  const last=state.mode==='live'?(state.paused?(state.pausedEnd??state.now):state.now):(rows.at(-1)?.time_ms??state.now);
  const duration=$('window').value==='all'?Math.max(1000,last-first):Number($('window').value)*1000;
  const endMax=Math.max(last,first+1000);
  const end=Math.min(endMax,first+duration)+Math.max(0,endMax-first-duration)*Number($('scrub').value)/1000;
  const start=$('window').value==='all'?first:end-duration;
  return {rows:rows.filter(r=>r.time_ms>=start&&r.time_ms<=end),start,end:Math.max(start+1000,end)};
}
function nearest(rows,stamp){return rows.reduce((a,b)=>!a||Math.abs(b.time_ms-stamp)<Math.abs(a.time_ms-stamp)?b:a,null);}
function clearSelection(){state.hover=null;state.lockedSample=null;}
function pauseView(){
  if(!state.paused){state.pausedRows=state.live.slice();state.pausedEnd=state.now;state.paused=true;}
  $('pause').textContent='실시간 재개';
}
function surface(canvas){const r=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1;canvas.width=Math.round(r.width*dpr);canvas.height=Math.round(r.height*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);return {ctx,w:r.width,h:r.height};}
function draw(){
  if(!state.channels.length)return;
  const view=viewport();state.plotView=view;
  const cursorRow=state.lockedSample||(state.hover===null?null:nearest(view.rows,state.hover));
  const selected=cursorRow||view.rows.at(-1);
  let values=selected?.values||{},status=selected?.status||{};
  const locked=state.lockedSample!==null;
  const outside=cursorRow&&(cursorRow.time_ms<view.start||cursorRow.time_ms>view.end);
  $('cursorTime').textContent=cursorRow?`${locked?'값 고정':'선택'} ${time(cursorRow.time_ms)}`:`${time(view.start)} → ${time(view.end)} · ${view.rows.filter(r=>r.id).length}개`;
  $('cursorHint').textContent=locked?`${outside?'선택 시점은 표시 구간 밖 · ':''}다른 지점 클릭: 값 변경 · 실시간 보기: 해제`:state.paused?'화면 정지 · 그래프를 클릭해 값을 선택하세요.':'마우스: 값 확인 · 좌클릭: 표시값 고정';
  if(state.mode==='live')$('mode').textContent=state.paused?'화면 정지 · 표본 수집 중':`실시간${locked?' · 표시값 고정':''} · 버퍼 ${state.live.filter(r=>r.id).length}점`;
  const showingCurrent=state.mode==='live'&&!state.paused&&!locked&&state.hover===null&&Number($('scrub').value)===1000;
  if(showingCurrent){values=state.current||{};status=state.currentStatus||{};}
  const geometry=status.speed_geometry;
  if($('speedGeometry'))$('speedGeometry').textContent=geometry?`표본 환산값 · 지름 ${Number((geometry.tire_diameter_m*100).toFixed(3))} cm · 기어비 ${Number(geometry.gear_ratio.toPrecision(10))}:1`:'이 표본의 환산 설정 미기록';
  state.channels.forEach(c=>$('value-'+c.key).textContent=!finite(values[c.key])&&c.key.endsWith('_target')?'목표값 미제공':format(values[c.key])+' '+c.unit);
  if(batteryPage){window.batteryDashboard.render(values,status,view,selected,showingCurrent);}
  else {
  $('rpmContext').textContent=showingCurrent?'현재 수신값':`선택 시점 · ${selected?time(selected.time_ms):'기록 없음'}`;
  for(const side of ['left','right']){
    const quality=status[`rpm_${side}_quality`]||'NO_DATA',rejected=status[`rpm_${side}_rejected_pct`];
    $('rpm-'+side).textContent=finite(values[`rpm_${side}`])?format(values[`rpm_${side}`],0)+' rpm':quality==='NOISY'?'노이즈 · 측정 불가':quality==='NO_PULSES'?'새 펄스 없음':'미수신/품질 미확인';
    $('reported-'+side).textContent=format(values[`rpm_${side}_reported`],0)+(finite(values[`rpm_${side}_reported`])?' rpm':'');
    $('target-'+side).textContent=finite(values[`rpm_${side}_target`])?format(values[`rpm_${side}_target`],0)+' rpm':'미제공';
    $('power-'+side).textContent=format(values[`power_${side}_kw`])+(finite(values[`power_${side}_kw`])?' kW':'');
    $('quality-'+side).textContent=(rpmQualities[quality]||rpmQualities.UNVERIFIED)+(finite(rejected)?` · 구간 펄스 폐기 ${format(rejected,1)}%`:'');
  }
  $('rpmNote').textContent=status.motor_command_mode==='POWER'?'현재 STM은 전력 명령(kW) 제어입니다. 목표 RPM은 생성·전송하지 않으며, 스로틀로 환산하지 않습니다. STM 보고 RPM은 검증 전 계산값입니다.':'목표 RPM은 송신기가 제공한 값만 표시합니다. STM 보고 RPM은 검증 전 계산값이며, 품질 통과도 실제 회전수 보정 완료를 뜻하지 않습니다.';
  $('desired').textContent=format(values.desired_yaw_rad_s);$('actual').textContent=format(values.yaw_rate_rad_s);
  $('errorYaw').textContent=finite(values.desired_yaw_rad_s)&&finite(values.yaw_rate_rad_s)?format(values.desired_yaw_rad_s-values.yaw_rate_rad_s):'—';
  const pairs=view.rows.filter(r=>r.id&&finite(r.values.desired_yaw_rad_s)&&finite(r.values.yaw_rate_rad_s));
  $('rms').textContent=pairs.length?format(Math.sqrt(pairs.reduce((a,r)=>a+(r.values.desired_yaw_rad_s-r.values.yaw_rate_rad_s)**2,0)/pairs.length)):'—';
  $('telemetryContext').textContent=$('rpmContext').textContent;
  $('speed').textContent=format(values.speed_kmh,1);
  for(const key of ['vehicle_speed_m_s','desired_yaw_rad_s','yaw_error_rad_s','delta_power_kw','traction_scale'])$('tv-'+key).textContent=format(values[key]);
  $('tvPower').textContent=`${format(values.power_left_kw)} / ${format(values.power_right_kw)}`;
  for(const key of ['tv_active','ed_active'])$('tv-'+key).textContent=finite(values[key])?(values[key]?'true · 개입':'false · 비개입'):'미수신';
  const conflict=values.tv_active===1&&values.ed_active===1;
  $('tvMode').textContent=conflict?'오류 · TV/ED 동시 개입':!finite(values.tv_active)||!finite(values.ed_active)?'미수신':values.tv_active?'TV · 폐루프':values.ed_active?'ED · 개루프':'대기 · 개입 없음';
  $('tvMode').classList.toggle('alarm',conflict);
  const gains=['pid_kp','pid_ki','pid_kd'];
  $('pid').textContent=gains.map(k=>format(values[k])).join(' / ');
  $('pidNote').textContent=gains.every(k=>finite(values[k]))?'STM 전송 설정값 · 읽기 전용':'PID 미수신 · PID 전송 지원 Rear 펌웨어 필요 (무선은 ESP도 필요)';
  }
  state.channels.forEach(c=>$('range-'+c.key).textContent=groups[c.group]?(state.enabled.has(c.key)?groupTitle(c.group):'그래프 숨김'):'수신값 표시 / CSV 기록');
  for(const group of Object.keys(groups)){
    $('plot-time-'+group).textContent=showingCurrent?'현재 수신값 · 클릭하면 표시값 고정':`${locked?'값 고정':'선택'} ${selected?time(selected.time_ms):'기록 없음'}`;
    for(const c of state.channels.filter(c=>c.group===group)){
      const label=$('plot-value-'+c.key);label.parentElement.hidden=!state.enabled.has(c.key);
      label.textContent=finite(values[c.key])?`${format(values[c.key],c.unit==='rpm'?0:3)} ${c.unit}`:c.key.endsWith('_target')?'미제공':'미수신';
    }
    drawPlot(group,view,cursorRow);
  }
}
function drawPlot(group,view,cursorRow){
  const all=state.channels.filter(c=>c.group===group);
  const channels=all.filter(c=>state.enabled.has(c.key));
  const axisUnit=c=>['0/1','0..1'].includes(c.unit)?'0–1':c.unit;
  const units=[...new Set((channels.length?channels:all).map(axisUnit))];
  const canvas=$('canvas-'+group);
  canvas.style.height=(group==='tv'?340:280)+'px';
  const {ctx,w,h}=surface(canvas),axisWidth=w<450?42:58;
  const left=12+Math.ceil(units.length/2)*axisWidth;
  const right=w-12-Math.floor(units.length/2)*axisWidth,top=35,bottom=h-32;
  canvas.plotBounds={left,right};
  const x=t=>left+(t-view.start)/(view.end-view.start)*(right-left);
  const scales=new Map();
  const tick=(v,unit)=>Math.abs(v)>=1000?(v/1000).toFixed(1)+'k':v.toFixed(unit==='rpm'?0:2);
  ctx.fillStyle='#000';ctx.fillRect(0,0,w,h);ctx.font='bold 11px Consolas,monospace';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const yy=top+(bottom-top)*i/4,xx=left+(right-left)*i/4;
    ctx.strokeStyle='#333';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.moveTo(xx,top);ctx.lineTo(xx,bottom);ctx.stroke();
  }
  units.forEach((unit,index)=>{
    const series=channels.filter(c=>axisUnit(c)===unit);
    let min=Infinity,max=-Infinity;
    for(const row of view.rows)for(const c of series){const v=row.values[c.key];if(finite(v)){min=Math.min(min,v);max=Math.max(max,v);}}
    const valid=Number.isFinite(min);
    if(!valid){min=0;max=1;}
    const margin=Math.max((max-min)*.12,unit==='rpm'?10:['rad/s','kW','V','m/s²'].includes(unit)?.02:1);
    if(unit==='0–1'){min=-.1;max=1.1;}else{min-=margin;max+=margin;}
    scales.set(unit,{min,max,valid,y:v=>bottom-(v-min)/(max-min)*(bottom-top)});
    const onLeft=index%2===0,axisX=onLeft?left-8-Math.floor(index/2)*axisWidth:right+8+Math.floor(index/2)*axisWidth;
    ctx.fillStyle='#ddd';ctx.textAlign=onLeft?'right':'left';ctx.fillText(unit,axisX,top-12);
    for(let i=0;i<=4;i++)ctx.fillText(tick(max-(max-min)*i/4,unit),axisX,top+(bottom-top)*i/4+4);
  });
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  for(const c of channels){
    const {y}=scales.get(axisUnit(c));
    ctx.strokeStyle=color(c);ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2.5;
    ctx.setLineDash(c.key.endsWith('_reported')?[2,5]:c.key==='desired_yaw_rad_s'||c.key.endsWith('_target')?[9,5]:['tv_active','ed_active'].includes(c.key)?[5,3]:[]);
    ctx.beginPath();let prev=null;
    const batterySignal=/^(battery_|bms_)/.test(c.key),stepped=axisUnit(c)==='0–1'||batterySignal;
    for(const row of view.rows){
      const v=row.values[c.key];if(!finite(v)){prev=null;continue;}
      if(prev===null||row.time_ms-prev.time_ms>(batterySignal?3000:1500))ctx.moveTo(x(row.time_ms),y(v));
      else{if(stepped)ctx.lineTo(x(row.time_ms),y(prev.values[c.key]));ctx.lineTo(x(row.time_ms),y(v));}prev=row;
    }
    ctx.stroke();ctx.setLineDash([]);
    if(view.rows.length<150)for(const row of view.rows){const v=row.values[c.key];if(finite(v)){ctx.beginPath();ctx.arc(x(row.time_ms),y(v),2,0,Math.PI*2);ctx.fill();}}
    if(cursorRow&&cursorRow.time_ms>=view.start&&cursorRow.time_ms<=view.end&&finite(cursorRow.values[c.key])){
      ctx.beginPath();ctx.arc(x(cursorRow.time_ms),y(cursorRow.values[c.key]),4,0,Math.PI*2);ctx.fill();
    }
  }
  ctx.restore();
  if(!channels.length||![...scales.values()].some(scale=>scale.valid)){
    ctx.fillStyle='#ffff00';ctx.textAlign='center';ctx.fillText(channels.length?'유효한 수신값 없음':'표시 신호 없음',(left+right)/2,h/2);
  }
  const stamp=t=>w<450?time(t).slice(0,8):time(t);
  ctx.fillStyle='#fff';ctx.textAlign='left';ctx.fillText(stamp(view.start),left,h-8);ctx.textAlign='right';ctx.fillText(stamp(view.end),right,h-8);
  if(cursorRow&&cursorRow.time_ms>=view.start&&cursorRow.time_ms<=view.end){
    const xx=x(cursorRow.time_ms);ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,bottom);ctx.stroke();ctx.setLineDash([]);
  }
}
async function sessions(){const data=await json(api+'/sessions');const selected=$('session').value;$('session').replaceChildren(new Option('과거 측정 선택',''));for(const s of data.sessions)$('session').add(new Option(`#${s.id} · ${s.label||'측정'} · ${new Date(s.started_at_utc).toLocaleString('ko-KR')} · ${s.sample_count}개`,s.id));$('session').value=selected;}
function renderRecording(data){
  RecordingClock.update(data);state.recording=data.recording_active;
  $('recordStatus').textContent=data.recording_active?`● 기록 중 · 세션 #${data.recording_session_id} · ${data.recording_sample_count}개`:'기록 대기 · 측정 시작부터 DB에 저장';
  if(data.recording_error||state.recordError)$('recordStatus').textContent=data.recording_error||state.recordError;
  else if(data.recording_active){
    if(!data.recording_last_received_at_utc||data.now_ms-Date.parse(data.recording_last_received_at_utc)>3000)$('recordStatus').textContent+=' · 수신 대기';
    if(data.current&&!Object.values(data.current).some(finite))$('recordStatus').textContent+=' · 차량 센서 유효값 없음';
  }
  $('recordFeedback').dataset.state=data.recording_error||state.recordError?'error':data.recording_active?'recording':'idle';
  $('recordFeedbackTitle').textContent=data.recording_error||state.recordError?'기록 확인 필요':data.recording_active?'● 기록 중':data.recording_started_at_utc?'■ 기록 종료':'기록 대기';
  $('recordFeedbackCount').textContent=`${(data.recording_sample_count||0).toLocaleString()}개 저장`;
  $('recordFeedbackDetail').textContent=data.recording_active||data.recording_error||state.recordError?$('recordStatus').textContent:data.recording_started_at_utc?'저장 완료 · 저장 데이터 열람에서 확인하세요.':'측정 시작을 누르면 기록합니다.';
  $('record').textContent=data.recording_active?'■ 기록 종료':'● 측정 시작';
  $('record').dataset.recordState=data.recording_active?'recording':'idle';
  $('record').setAttribute('aria-busy',String(state.recordBusy));
  $('record').disabled=state.recordBusy||!data.can_record;
  $('label').disabled=state.recordBusy||data.recording_active||!data.can_record;$('rawLink').hidden=false;
}
async function history(page=0){
  const generation=++state.generation;clearSelection();state.busyHistory=true;$('prev').disabled=$('next').disabled=true;
  try{const data=await json(`${api}/samples?session_id=${state.session}&after=${state.pages[page]||0}&through=${state.through}`);if(generation!==state.generation)return;state.history=data.samples.sort((a,b)=>a.time_ms-b.time_ms||a.id-b.id);state.through=data.through;state.page=page;state.pages[page+1]=data.cursor;state.more=data.more;$('scrub').value=1000;$('mode').textContent=`과거 세션 #${state.session} · ${page+1}페이지 · ${data.samples.length}개`;$('error').textContent='';draw();}
  catch(e){$('error').textContent=e.message;}finally{if(generation===state.generation){state.busyHistory=false;navigation();}}
}
function navigation(){$('export').textContent=state.mode==='history'?'세션 전체 CSV 저장':'최근 버퍼 CSV 저장';$('prev').disabled=state.mode!=='history'||state.busyHistory||state.page===0;$('next').disabled=state.mode!=='history'||state.busyHistory||!state.more;$('pause').disabled=state.mode!=='live';}
async function poll(){
  let catchUp=false;
  try{
    const data=await json(api+'/live?after='+state.cursor);
    if(!state.channels.length)init(data.channels);
    if(state.epoch&&state.epoch!==data.epoch){state.cursor=0;state.live=[];state.epoch=data.epoch;$('notice').textContent='게이트웨이가 재시작되어 실시간 버퍼를 새로 불러옵니다.';return;}
    state.epoch=data.epoch;state.cursor=data.cursor;state.current=data.current;state.currentStatus=data.current_status||{};state.now=data.now_ms;
    if(data.truncated)$('notice').textContent='실시간 버퍼를 벗어난 구간이 있습니다. 저장한 측정 세션에서 확인하세요.';
    // Pause only freezes the view. Every fetched sample still enters the
    // buffer, and a multi-page response is drained without render throttling.
    for(const sample of data.samples)state.live.push(sample);
    state.live.sort((a,b)=>a.time_ms-b.time_ms||a.id-b.id);
    if(!data.more&&Object.values(data.current).every(v=>v===null))state.live.push({id:0,time_ms:data.now_ms,values:data.current,status:data.current_status});
    state.live=state.live.slice(-12000);
    catchUp=data.more;
    const usbBatch=data.telemetry_transport==='USB'&&data.input_mode==='ESP_USB';
    const dropped=usbBatch?data.usb_dropped_samples:data.relay_dropped_samples;
    if(dropped>0)$('notice').textContent=`${usbBatch?'USB':'차량'} 전송 버퍼 초과: ${dropped}개 표본 누락. 통신 상태를 확인하세요.`;
    const rear=data.rear_sample,bms=data.bms_power_timing;
    $('delivery').textContent=`수신 ${format(data.receive_rate_hz,1)} 표본/s · ${usbBatch?'USB':'무선'} 묶음 ${(usbBatch?data.usb_batch_samples:data.relay_batch_samples)||0}개 · 전송 시 대기 ${(usbBatch?data.usb_queue_samples:data.relay_queue_samples)||0}개 · 최신 표본 전송 대기 ${format(data.sample_age_at_receive_ms||0,0)} ms · 버퍼 누락 ${dropped||0}개`+
      (Array.isArray(rear)?` · STM 송신 누락 ${rear[4]} / UART 누락 ${rear[5]}`:'')+
      (Array.isArray(bms)?` · BMS 응답 ${bms[3]}개 / 최근 간격 ${bms[5]} ms / 최대 ${bms[6]} ms`:'');
    if(!state.recordBusy)renderRecording(data);
    const wired=['STM_USB','ESP_USB'].includes(data.input_mode),board=data.input_mode==='ESP_USB'?'ESP':'STM';
    $('link').textContent=wired?(data.input_online?`● ${board} USB ${data.input_port} · ${format(data.receive_rate_hz,1)} Hz 수신`:`○ ${board} USB ${data.input_port} · 미수신`):(Object.values(data.current).some(finite)?'● 실측 신호 수신 중':'○ 게이트웨이 연결됨 · 센서 미수신');
    $('signalStatus').textContent=wired?(data.input_online?(data.signal_warning||''):'USB 수신 중단 · 마지막 값은 현재값으로 표시하지 않습니다.') : '';
    draw();
  }catch(e){$('link').textContent='게이트웨이 연결 끊김';$('signalStatus').textContent='';$('error').textContent=e.message;$('record').disabled=true;$('recordFeedback').dataset.state='error';$('recordFeedbackTitle').textContent='연결 끊김 · 기록 상태 확인 필요';$('recordFeedbackDetail').textContent='표시된 시간과 개수는 마지막으로 확인한 값입니다.';state.current={};state.currentStatus={};if(state.mode==='live'&&!state.paused)draw();}
  finally{setTimeout(poll,catchUp?0:100);}
}
$('record').onclick=async()=>{
  if(state.recordBusy)return;
  const active=state.recording;state.recordBusy=true;$('record').disabled=true;$('label').disabled=true;
  $('record').textContent=active?'종료 확인 중…':'시작 확인 중…';
  $('record').dataset.recordState='pending';$('record').setAttribute('aria-busy','true');
  $('recordFeedback').dataset.state='pending';$('recordFeedbackTitle').textContent=$('record').textContent;
  $('recordFeedbackDetail').textContent='서버 응답을 기다리는 중입니다.';
  let confirmed=false;
  try{
    const data=await json('/api/records/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:active?'stop':'start',label:$('label').value})});
    if(!data.ok||data.recording_active!==!active)throw new Error('서버 기록 상태를 확인하지 못했습니다.');
    state.recordError='';renderRecording(data);confirmed=true;$('error').textContent='';
    $('notice').textContent=active?`기록 종료 확인 · DB에 ${data.recording_sample_count}개 표본 저장`:`기록 시작 확인 · 세션 #${data.recording_session_id} · 지금부터 수신되는 데이터를 저장합니다.`;
  }catch(e){
    state.recordError=`기록 ${active?'종료':'시작'} 확인 실패: ${e.message}`;
    $('recordStatus').textContent=$('error').textContent=state.recordError;
    $('recordFeedback').dataset.state='error';$('recordFeedbackTitle').textContent='기록 확인 실패';$('recordFeedbackDetail').textContent=state.recordError;
    alert(state.recordError+'\n화면의 기록 상태를 확인한 뒤 다시 시도하세요.');
  }finally{state.recordBusy=false;}
  if(confirmed)try{await sessions();}catch(e){$('error').textContent='기록 상태는 확인됐지만 목록 갱신에 실패했습니다: '+e.message;}
};
$('session').onchange=()=>{if(!$('session').value)return;clearSelection();state.mode='history';state.session=Number($('session').value);state.pages=[0];state.through=0;state.history=[];navigation();history();};
$('live').onclick=()=>{state.generation++;clearSelection();state.mode='live';state.paused=false;state.pausedRows=null;state.pausedEnd=null;state.busyHistory=false;$('pause').textContent='화면 일시정지';$('scrub').value=1000;$('session').value='';navigation();draw();};
$('pause').onclick=()=>{clearSelection();if(state.paused){state.paused=false;state.pausedRows=null;state.pausedEnd=null;$('pause').textContent='화면 일시정지';}else pauseView();$('scrub').value=1000;draw();};
$('prev').onclick=()=>history(state.page-1);$('next').onclick=()=>history(state.page+1);
$('scrub').oninput=()=>{clearSelection();if(state.mode==='live')pauseView();draw();};$('window').onchange=()=>{clearSelection();draw();};$('reload').onclick=()=>sessions().catch(e=>$('error').textContent=e.message);
$('export').onclick=()=>{
  if(state.mode==='history'){const a=document.createElement('a');a.href=`${api}/export?session_id=${state.session}`;a.click();$('notice').textContent='선택 세션 전체 CSV를 내려받습니다. 현재 페이지 밖의 표본도 포함됩니다.';return;}
  const rows=state.mode==='live'?(state.pausedRows||state.live).filter(r=>r.id):state.history;
  const keys=state.channels.map(c=>c.key);
  const statusKeys=batteryPage?['bms_online','bms_fault','bms_state','bms_age_ms','bms_charge_mos_on','bms_discharge_mos_on','bms_alarm_hex','speed_geometry',...Array.from({length:48},(_,i)=>`cell_${i+1}_v`)]:rpmStatusKeys;
  const escape=v=>'"'+String(v??'').replaceAll('"','""')+'"';
  const statusValue=(r,k)=>k==='speed_geometry'?JSON.stringify(r.status?.[k]??null):k.startsWith('cell_')?r.status?.bms_cell_voltages_v?.[Number(k.split('_')[1])-1]:r.status?.[k];
  const csv=[['captured_at_utc','source_timestamp_ms',...keys,...statusKeys].map(escape).join(','),...rows.map(r=>[new Date(r.time_ms).toISOString(),r.source_timestamp_ms??'',...keys.map(k=>finite(r.values[k])?r.values[k]:''),...statusKeys.map(k=>statusValue(r,k))].map(escape).join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=`${batteryPage?'bms':'ev'}-${state.mode}-${state.session||'live'}-page-${state.page+1}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('share').onclick=async()=>{try{await navigator.clipboard.writeText(location.origin+(batteryPage?'/battery':'/pit'));$('notice').textContent='주소 복사됨. 다른 기기에는 localhost 대신 ngrok 주소를 공유하세요.';}catch{$('notice').textContent=location.origin+(batteryPage?'/battery':'/pit');}};
window.addEventListener('resize',draw);

async function overview(){
  try{
    if(!state.channels.length)return;
    const t=await json('/api/tuning/overview');
    const sensor=(online,ok)=>!online?'미수신':ok===false?'오류':'수신';
    $('vehicle').textContent=`STM ${t.stm_online?'수신':'미수신'} · IMU ${t.imu_online?'수신':'미수신'} · 조향 ${sensor(t.sas_online,t.sas_ok)} · 스로틀 ${sensor(t.tps_online,t.tps_ok)} · 좌/우 RPM ${t.motor_left_ok?'유효':'미확인'} / ${t.motor_right_ok?'유효':'미확인'} · 제어 ${!t.tqv_internal_online?'미수신':t.tv_active?'TV 개입':t.ed_active?'ED 개입':'대기'} · 고장코드 ${t.stm_online?(t.fault_code??'미수신'):'미수신'} · 제한 사유 ${t.limit_reason||'미수신'}`;
    $('vehicle').classList.toggle('alarm',Boolean(t.stm_online&&t.fault_code));
    $('battery').textContent=t.bms_online?`BMS 수신 · 최고 온도 ${format(t.bms_temp_max_c,1)} °C · 셀 편차 ${format(t.bms_cell_delta_mv,0)} mV · ${t.bms_fault===true?'경고: '+(t.bms_alarm_summary||t.bms_alarm_hex||'BMS 알람'):t.bms_fault===false?'보고된 알람 없음':'알람 상태 미수신'}`:'BMS 미수신';
    $('battery').classList.toggle('alarm',t.bms_fault===true);
    $('gnss').textContent=t.gnss_online?`${format(t.gnss_latitude_deg,6)}, ${format(t.gnss_longitude_deg,6)} · 정확도 ${format(t.gnss_accuracy_m,1)} m`:'위치 미수신';
  }catch{
    $('vehicle').textContent='게이트웨이 연결 끊김';
    if($('battery'))$('battery').textContent='현재 BMS 상태 확인 불가';
    $('gnss').textContent='현재 위치 상태 확인 불가';
  }finally{setTimeout(overview,1000);}
}
sessions().catch(e=>$('error').textContent=e.message);poll();if(!batteryPage)overview();
