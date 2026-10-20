"""Versioned tuning metadata and strict adapter to the deployed LIVE_TV wire format."""
import json
from pathlib import Path

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'protocol/tuning_parameters.json').read_text(encoding='utf8'))
PARAMETERS = {p['key']: p for p in CONTRACT['parameters']}
LIVE_KEYS = frozenset(('strength_pct', 'limit_pct', 'tv_enable'))

def catalog(snapshot):
    connected = bool(snapshot.get('online') and snapshot.get('stm_online'))
    values = snapshot.get('_tuning_values', {})
    entries = []
    for parameter in CONTRACT['parameters']:
        item = dict(parameter)
        key = item['telemetry']
        value = (values.get(key) if item['group'] == 'pid' else snapshot.get(key)) if key else None
        item['value'] = value if connected else None
        item['value_fresh'] = connected and value is not None
        item['writable'] = item['key'] in LIVE_KEYS and item['access'] == 'live'
        entries.append(item)
    return {**CONTRACT, 'parameters': entries,
            'capability_source': 'server_legacy_contract_not_device_negotiation',
            'device_parameter_protocol': False,
            'persistence_note': '원격 명령 유지 중에만 적용됩니다. 만료 후 STM 로컬 기본값으로 복귀합니다.'}

def to_live_command(request):
    """Validate the entire update before forwarding anything. No partial writes."""
    if type(request.get('schema_version')) is not int or request['schema_version'] != CONTRACT['schema_version']:
        raise ValueError('unsupported tuning schema_version')
    if request.get('persistence', 'volatile_lease') != 'volatile_lease':
        raise ValueError('persistent tuning is not supported')
    sequence = request.get('request_id')
    if type(sequence) is not int or not 0 <= sequence <= 255:
        raise ValueError('request_id must be an integer in 0..255')
    values = request.get('values')
    if not isinstance(values, dict) or set(values) != LIVE_KEYS:
        raise ValueError('only a complete strength_pct/limit_pct/tv_enable update is supported; PID writes are not enabled')
    for key, value in values.items():
        definition = PARAMETERS[key]
        if definition['type'] == 'boolean':
            if type(value) is not bool:
                raise ValueError(f'{key} must be boolean')
        elif (type(value) is not int or not definition['min'] <= value <= definition['max']
              or (value - definition['min']) % definition['step']):
            raise ValueError(f'{key} must be 0..100 in steps of 5')
    return {'type': 'live_tv', 'request_id': sequence, **values}
