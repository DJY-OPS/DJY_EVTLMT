"""Measured, fresh channels for the broadcast HUD. No display-test values."""
from tuning import project

KEYS = ('speed_kmh', 'rpm_left', 'rpm_right', 'tps_pct', 'sas_deg',
        'yaw_rate_rad_s', 'lateral_accel_m_s2', 'longitudinal_accel_m_s2')


def project_onboard(packet):
    measured = project(packet)
    online = bool(packet.get('vehicle_online'))
    power = packet.get('vehicle_power_on')
    # Telemetry reception and BMS MOS state do not prove vehicle ignition.
    return {'online': online,
            'power_on': power if online and type(power) is bool else None,
            'values': {key: measured[key] if online else None for key in KEYS}}
