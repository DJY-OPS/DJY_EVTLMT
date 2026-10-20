"""Tire diameter confirmed 2026-09-20; gear ratio updated 2026-09-30."""
import math

GEAR_RATIO = 52.0 / 11.0  # User-confirmed 4.72727272...:1.
TIRE_DIAMETER_M = 0.45
TIRE_RADIUS_M = TIRE_DIAMETER_M / 2.0
DEFAULT_GEOMETRY = {'gear_ratio': GEAR_RATIO, 'tire_diameter_m': TIRE_DIAMETER_M,
                    'calculation_version': 1}


def validate_geometry(data):
    result = dict(DEFAULT_GEOMETRY)
    for key, low, high in (('gear_ratio', 0.1, 100.0), ('tire_diameter_m', 0.05, 3.0)):
        value = data.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f'{key}: {low} ~ {high} 범위의 숫자를 입력하세요.')
        result[key] = float(value)
    return result


def speed_kmh(rpm_left, rpm_right, geometry=None):
    geometry = DEFAULT_GEOMETRY if geometry is None else geometry
    return (rpm_left + rpm_right) * 0.5 / geometry['gear_ratio'] * math.pi * geometry['tire_diameter_m'] * 60.0 / 1000.0


def recorded_geometry(packet):
    value = packet.get('speed_geometry')
    if value == 'DIAMETER_0.45M_GEAR_4':
        return {**DEFAULT_GEOMETRY, 'gear_ratio': 4.0}
    if value == 'DIAMETER_0.45M_GEAR_52_DIV_11':
        return dict(DEFAULT_GEOMETRY)
    if isinstance(value, dict):
        try:
            return validate_geometry(value)
        except ValueError:
            pass
    return None


def with_verified_speed(data, geometry=None):
    """Recalculate only a same-packet, explicitly valid pair of motor RPMs."""
    result = dict(data)
    left = data.get('rpm_left', data.get('rpm_l'))
    right = data.get('rpm_right', data.get('rpm_r'))
    valid = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and 0 <= v <= 65535 for v in (left, right))
    if valid and data.get('motor_left_ok') and data.get('motor_right_ok'):
        if 'speed_kmh' in data:
            result['speed_reported_kmh'] = data['speed_kmh']
        result['speed_kmh'] = speed_kmh(left, right, geometry)
        result['speed_online'] = data.get('rpm_left_online', True) and data.get('rpm_right_online', True)
        result['speed_geometry'] = dict(DEFAULT_GEOMETRY if geometry is None else geometry)
    return result
