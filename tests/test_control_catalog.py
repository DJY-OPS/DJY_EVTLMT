import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gateway'))
sys.path.insert(0,str(ROOT/'scripts'))
from control_catalog import catalog, to_live_command
from ev_gateway import EVGateway
from generate_tuning_contract import TARGETS, render
from host_compiler import compiler

def request(**values):
    return dict(type='parameter_update',schema_version=1,request_id=7,
                values=dict(strength_pct=35,limit_pct=60,tv_enable=True,**values))

class ControlCatalogTests(unittest.TestCase):
    def test_supported_atomic_update_maps_to_existing_wire(self):
        self.assertEqual(to_live_command(request()), dict(type='live_tv',request_id=7,
                         strength_pct=35,limit_pct=60,tv_enable=True))

    def test_invalid_updates_never_reach_vehicle(self):
        g=EVGateway('127.0.0.1',0,'127.0.0.1',9004,enable_control=True)
        g.serial_stream=io.BytesIO()
        g.store.update({'seq':1,'stm_online':True,'fault_code':0})
        cases=[{'schema_version':2},{'schema_version':True},{'request_id':256},
               {'persistence':'flash'},{'values':{'pid_kp':30}},
               {'values':{'strength_pct':50}}]
        for key,value in [('strength_pct',36),('strength_pct',-5),('strength_pct',105),
                          ('strength_pct',35.0),('strength_pct',True),('limit_pct',float('nan')),
                          ('tv_enable',1),('pid_ki',2)]:
            base=request()['values'];base[key]=value;cases.append({'values':base})
        try:
            for change in cases:
                with self.subTest(change=change):
                    update=request();update.update(change)
                    with self.assertRaises(ValueError):g.submit_control(update)
                    self.assertEqual(g.serial_stream.getvalue(),b'')
            self.assertFalse(g.submit_control({'type':'pid_set','pid_kp':30})['ok'])
            self.assertEqual(g.serial_stream.getvalue(),b'')
        finally:g.forward_socket.close()

    def test_catalog_read_only_values_and_stale_data(self):
        state={'online':True,'stm_online':True,'tv_applied_pct':35,
               '_tuning_values':{'pid_kp':30,'pid_ki':None,'pid_kd':5}}
        items={p['key']:p for p in catalog(state)['parameters']}
        self.assertTrue(items['strength_pct']['writable'])
        self.assertFalse(items['pid_kp']['writable'])
        self.assertEqual(items['pid_kp']['value'],30)
        self.assertIsNone(items['pid_ki']['value'])
        self.assertNotIn('max',items['pid_kp'])
        state['online']=False
        self.assertTrue(all(p['value'] is None for p in catalog(state)['parameters']))

    def test_generated_contract_matches_and_compiles_for_c_and_cpp(self):
        expected=render()
        for path in TARGETS:self.assertEqual(path.read_text(encoding='utf8'),expected)
        source=r'''
#include <assert.h>
#include "djy_uart_protocol.h"
int main(void) {
    DjyUartLiveTv command={7,35,60,1},decoded={0};
    uint8_t frame[DJY_UART_LIVE_TV_SIZE];
    djy_uart_pack_live_tv(frame,&command);
    assert(sizeof(frame)==9 && frame[0]==0xd5 && frame[3]==1 && frame[5]==35);
    assert(djy_uart_unpack_live_tv(&decoded,frame));
    assert(decoded.limit_percent==60);
    assert(djy_tuning_validate(DJY_PARAM_TV_STRENGTH,0)==DJY_TUNING_OK);
    assert(djy_tuning_validate(DJY_PARAM_TV_STRENGTH,100)==DJY_TUNING_OK);
    assert(djy_tuning_validate(DJY_PARAM_TV_STRENGTH,36)==DJY_TUNING_RANGE);
    assert(djy_tuning_validate(DJY_PARAM_TV_LIMIT,-5)==DJY_TUNING_RANGE);
    assert(djy_tuning_validate(DJY_PARAM_TV_ENABLE,2)==DJY_TUNING_RANGE);
    assert(djy_tuning_validate(DJY_PARAM_PID_KP,30000)==DJY_TUNING_UNSUPPORTED);
    assert(djy_tuning_validate(65535,0)==DJY_TUNING_UNSUPPORTED);
    DjyTuningUpdate update={2,DJY_PARAM_TV_LIMIT,7,50};
    assert(djy_tuning_validate_update(&update)==DJY_TUNING_VERSION);
    update.schema_version=1;assert(djy_tuning_validate_update(&update)==DJY_TUNING_OK);
    frame[5]=36;frame[8]=djy_uart_crc8(frame,8);
    assert(!djy_uart_unpack_live_tv(&decoded,frame));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            for index,target in enumerate(TARGETS):
                cpp=index==0
                path=Path(folder)/('test.cpp' if cpp else 'test.c');path.write_text(source,encoding='utf8')
                exe=Path(folder)/f'test-{index}.exe'
                result=subprocess.run(compiler(cpp)+['-I',str(target.parent),str(path),'-o',str(exe)],capture_output=True,timeout=180)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
                subprocess.run([str(exe)],check=True,timeout=10)
