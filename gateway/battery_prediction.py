"""Rolling BMS runtime and Wh/km estimates. No vehicle/BMS control writes."""
from collections import deque
import json
import threading
import time

from battery import finite, project, status

# EVDATA (2026-09-20): remaining Ah / SOC = 80 Ah, discharge current negative.
DEFAULTS = dict(configured_capacity_ah=80.0, reserve_soc_pct=10.0, discharge_sign=-1)


def validate_settings(data):
    result = {}
    for key, low, high in (('configured_capacity_ah', .1, 10000), ('reserve_soc_pct', 0, 90),
                           ('discharge_sign', -1, 1)):
        value = data.get(key)
        if not finite(value) or not low <= value <= high or (key == 'discharge_sign' and value not in (-1, 1)):
            raise ValueError(f'{key}: 유효한 숫자가 필요합니다 ({low}–{high}).')
        result[key] = value
    return result


class BatteryPrediction:
    def __init__(self, database=None):
        self.database = database
        self.lock = threading.RLock()
        self.settings = dict(DEFAULTS)
        if database:
            with database._lock:
                database._connection.execute('CREATE TABLE IF NOT EXISTS battery_prediction_settings '
                                             '(id INTEGER PRIMARY KEY CHECK(id=1), payload_json TEXT NOT NULL)')
                database._connection.commit()
                row = database._connection.execute('SELECT payload_json FROM battery_prediction_settings WHERE id=1').fetchone()
                if row:
                    self.settings = validate_settings(json.loads(row[0]))
        self._reset()

    def _reset(self):
        self.previous = None
        self.last_time = None
        self.identity = None
        self.response = None
        self.intervals = deque(maxlen=36000)
        self.capacities = deque(maxlen=36000)

    def command(self, payload):
        settings = validate_settings(payload)
        if self.database is None:
            raise ValueError('설정 저장에는 DB가 필요합니다.')
        with self.lock, self.database._lock:
            try:
                self.database._connection.execute('INSERT OR REPLACE INTO battery_prediction_settings VALUES(1,?)',
                                                  (json.dumps(settings),))
                self.database._connection.commit()
            except Exception:
                self.database._connection.rollback()
                raise
            self.settings = settings
            self._reset()  # Never combine opposite current signs or different pack settings.

    def observe(self, packet):
        with self.lock:
            stamp = packet.get('sample_time_ms', time.time_ns() // 1000000)
            if not finite(stamp):
                return
            timing = packet.get('bms_power_timing')
            response = None
            if timing is not None:
                if (not isinstance(timing, list) or len(timing) != 8
                        or not all(finite(v) for v in timing) or not 0 <= timing[4] < 2000):
                    self.previous = None
                    return
                stamp -= timing[4]
                response = timing[3]
            # Legacy/direct-UART packets use capture time and the existing freshness gates.
            if self.last_time is not None and stamp <= self.last_time:
                return
            identity = (packet.get('stream_id'), packet.get('bms_protocol'))
            if (self.identity != identity or (response is not None and self.response is not None
                                              and response < self.response)):
                self._reset()
            self.identity = identity
            if response is not None and response == self.response:
                return  # Repeated ESP/STM packets are not new CAN electrical responses.
            self.response = response
            values, metadata = project(packet), status(packet)
            amps, watts, speed = values['battery_current_a'], values['battery_power_w'], values['speed_kmh']
            if not finite(amps) or not finite(watts) or abs(amps) > 10000 or abs(watts) > 10000000:
                self.previous = None
                return
            self.last_time = stamp
            speed = speed if finite(speed) and 0 <= speed <= 400 else None
            sign = self.settings['discharge_sign']
            current = (stamp, amps*sign, watts*sign, speed)
            previous, self.previous = self.previous, current
            if previous:
                dt = (stamp-previous[0])/1000
                if 0 < dt <= 2:
                    distance = (speed+previous[3])*.5*dt/3600 if speed is not None and previous[3] is not None else None
                    self.intervals.append((previous[0], stamp, (current[1]+previous[1])*.5*dt/3600,
                                           (current[2]+previous[2])*.5*dt/3600, distance))
                else:
                    # A new driving stint needs its own warmup, not an old average.
                    self.intervals.clear()
                    self.capacities.clear()
            remaining = metadata['bms_remaining_capacity_ah']
            if finite(remaining) and 0 <= remaining <= self.settings['configured_capacity_ah']*1.1:
                self.capacities.append((stamp, remaining))
            for queue in (self.intervals, self.capacities):
                while queue and queue[0][0] < stamp-180000:
                    queue.popleft()

    def _window(self, end, seconds):
        start = end-seconds*1000
        duration = ah = wh = distance = speed_seconds = 0.0
        for a, b, charge, energy, km in self.intervals:
            overlap = min(b, end)-max(a, start)
            if overlap <= 0:
                continue
            fraction = overlap/(b-a)
            duration += overlap/1000
            ah += charge*fraction
            wh += energy*fraction
            if km is not None:
                distance += km*fraction
                speed_seconds += overlap/1000
        span = min(seconds, max(0, (end-self.intervals[0][0])/1000)) if self.intervals else 0
        valid = duration >= 9.9 and duration >= span*.9
        speed_valid = valid and speed_seconds >= duration*.95 and distance >= .05
        speed = distance/speed_seconds*3600 if speed_seconds else None
        return dict(seconds=duration, valid=valid, ah=ah, current_a=ah/duration*3600 if duration else None,
                    wh_km=wh/distance if speed_valid and speed >= 5 else None,
                    speed_kmh=speed if speed_valid else None)

    def read(self, packet, now_ms=None):
        now_ms = time.time()*1000 if now_ms is None else now_ms
        with self.lock:
            values, metadata = project(packet), status(packet)
            result = dict(settings=dict(self.settings), state='waiting', reason='BMS 전압·전류 수신 대기',
                          remaining_minutes=None, range_minutes=None, available_ah=None, capacity_source=None,
                          mean_current_a=None, wh_km=None, reference_wh_km=None, efficiency_change_pct=None,
                          efficiency='waiting', efficiency_reason='유효한 속도·전력 구간을 모으는 중',
                          observed_seconds=0, quality='수집 중', bms_fault=metadata['bms_fault'])
            if (self.previous is None or self.last_time is None or not 0 <= now_ms-self.last_time < 3000
                    or values['battery_current_a'] is None or values['battery_power_w'] is None):
                return result
            recent = self._window(self.last_time, 30)
            result['observed_seconds'] = round(recent['seconds'], 1)
            if not recent['valid']:
                result['reason'] = '유효한 소비 구간을 10초 이상 수집하고 있습니다.'
                return result
            result['mean_current_a'] = recent['current_a']
            result['wh_km'] = recent['wh_km']
            reference = self._window(self.last_time-30000, 120)
            result['reference_wh_km'] = reference['wh_km']
            if recent['wh_km'] is not None and recent['wh_km'] <= 0:
                result.update(efficiency='regen', efficiency_reason='최근 구간에서 회생·충전 에너지가 소비보다 큽니다.')
            elif recent['wh_km'] is not None and reference['wh_km'] is not None and reference['wh_km'] > 0:
                if abs(recent['speed_kmh']/reference['speed_kmh']-1) > .2:
                    result['efficiency_reason'] = '평균 속도가 직전 구간과 20% 이상 달라 전비 평가는 보류합니다.'
                else:
                    change = (reference['wh_km']-recent['wh_km'])/reference['wh_km']*100
                    result.update(efficiency_change_pct=change,
                                  efficiency='good' if change > 10 else 'poor' if change < -10 else 'similar',
                                  efficiency_reason='최근 30초를 그 직전 최대 120초와 비교 · 비슷한 평균 속도 기준')
            elif recent['wh_km'] is not None:
                result['efficiency_reason'] = '현재 전비 수집 완료 · 직전 비교 구간을 모으는 중'
            else:
                result['efficiency_reason'] = '전비에는 유효한 차속·주행거리 50m 이상·평균속도 5km/h 이상이 필요합니다.'

            capacity = self.settings['configured_capacity_ah']
            reserve = capacity*self.settings['reserve_soc_pct']/100
            remaining, soc = metadata['bms_remaining_capacity_ah'], values['battery_soc_pct']
            measured = finite(remaining) and 0 <= remaining <= capacity*1.1
            if remaining is not None and not measured:
                result.update(state='inconsistent', reason='BMS 잔여 용량이 설정용량 범위를 벗어났습니다.', quality='설정용량 확인 필요')
                return result
            if measured:
                result['capacity_source'] = 'bms_ah'
            elif finite(soc):
                remaining = capacity*soc/100
                result['capacity_source'] = 'soc'
            else:
                result.update(state='missing_capacity', reason='BMS 잔여 Ah 또는 SOC가 필요합니다.')
                return result
            result['available_ah'] = max(0.0, remaining-reserve)
            result['quality'] = 'BMS 잔여 Ah 기반 · 최근 소비 유지 가정' if measured else 'SOC × 설정용량 기반 · 참고 추정'
            if measured and finite(soc) and abs(remaining-capacity*soc/100) > max(.5, capacity*.03):
                result.update(state='inconsistent', reason='잔여 Ah와 SOC·설정용량이 맞지 않아 주행시간 예측을 보류합니다.', quality='용량 확인 필요')
                return result
            # Detect the EVDATA session-6 failure mode: reported Ah falls much faster than integrated I.
            points = [p for p in self.capacities if p[0] >= self.last_time-120000]
            if measured and len(points) > 1:
                span = (points[-1][0]-points[0][0])/1000
                consumed = self._window(points[-1][0], span)
                if span >= 30 and consumed['seconds'] >= span*.95:
                    reported = points[0][1]-points[-1][1]
                    if abs(reported-consumed['ah']) > max(.5, abs(consumed['ah'])*.3):
                        result.update(state='inconsistent', reason='BMS 용량 변화와 전류 적산이 불일치해 주행시간 예측을 보류합니다.', quality='용량·전류 확인 필요')
                        return result
                    result['quality'] = '관측 구간의 BMS 용량 변화·전류 적산 일치 · 보장 시간 아님'
            if result['available_ah'] <= 0:
                result.update(state='reserve', remaining_minutes=0, reason='설정한 예비 SOC에 도달했습니다.')
                return result
            minimum_load = max(1.0, capacity*.02)
            if recent['current_a'] <= minimum_load:
                result.update(state='low_load', reason='충전·회생 또는 저부하 상태여서 주행시간을 추정하지 않습니다.')
                return result
            speed = values['speed_kmh']
            if finite(speed) and speed < 3 and self.previous[1] <= minimum_load:
                result.update(state='stopped', reason='정차·저부하 상태입니다. 주행을 재개하면 예상 시간을 갱신합니다.')
                return result
            # ponytail: recent-load extrapolation, not a trained route/temperature model.
            minutes = result['available_ah']/recent['current_a']*60
            scenarios = [result['available_ah']/window['current_a']*60
                         for window in (self._window(self.last_time, 10), recent, self._window(self.last_time, 120))
                         if window['valid'] and window['current_a'] > minimum_load]
            result.update(state='ready', remaining_minutes=minutes, range_minutes=[min(scenarios), max(scenarios)],
                          reason='지금과 같은 소비가 이어질 때 예비 SOC까지의 예상 시간입니다.')
            if metadata['bms_fault']:
                result['quality'] = 'BMS 경보 발생 · 에너지 계산 참고값이며 계속 운행 가능하다는 뜻이 아닙니다.'
            return result
