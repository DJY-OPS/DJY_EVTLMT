"""Exercise actual ESP UART command forwarding offline, without vehicle I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_compiler import compiler

ROOT = Path(__file__).resolve().parents[1]


class EspLiveTransmitTests(unittest.TestCase):
    def test_uart_command_freshness_rate_limit_and_read_only(self):
        source = (ROOT / 'firmware/esp32_ev_gateway/src/main.cpp').read_text(encoding='utf-8')
        function = source[source.index('void forwardLiveTvCommand() {'):source.index('void handleCommand(')]
        harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "djy_uart_protocol.h"
#define CHECK(x) do { if(!(x)) { std::fprintf(stderr,"line %d: %s\n",__LINE__,#x);std::exit(1); } } while(0)
uint32_t clockMs=1000,liveCommandMs=1000;
uint32_t millis(){return clockMs;}
bool rearUartReady=true,liveCommandSeen=true;
struct { uint32_t rearExtendedMs=1000; } state;
DjyUartLiveTv liveCommand={7,35,60,DJY_UART_TV_FLAG_ENABLE};
struct {
    unsigned count=0;
    uint8_t frame[DJY_UART_LIVE_TV_SIZE]={};
    void write(const uint8_t*data,unsigned length){
        CHECK(length==DJY_UART_LIVE_TV_SIZE);std::memcpy(frame,data,length);++count;
    }
} rearUart;
''' + function + r'''
int main(){
    forwardLiveTvCommand();
#if EV_RECEIVE_ONLY
    CHECK(rearUart.count==0);
#else
    CHECK(rearUart.count==1);
    DjyUartLiveTv decoded={};
    CHECK(djy_uart_unpack_live_tv(&decoded,rearUart.frame));
    CHECK(decoded.sequence==7 && decoded.strength_percent==35 && decoded.limit_percent==60);
    CHECK(decoded.flags==DJY_UART_TV_FLAG_ENABLE);
    // No duplicate frame inside the 100 ms interval.
    clockMs=1099;state.rearExtendedMs=clockMs;forwardLiveTvCommand();CHECK(rearUart.count==1);
    clockMs=1100;forwardLiveTvCommand();CHECK(rearUart.count==2);
    // A recently received STM frame cannot revive an expired command.
    clockMs=1501;state.rearExtendedMs=clockMs;forwardLiveTvCommand();CHECK(rearUart.count==2);
    liveCommandMs=clockMs;state.rearExtendedMs=1201;forwardLiveTvCommand();CHECK(rearUart.count==2);
    state.rearExtendedMs=0;forwardLiveTvCommand();CHECK(rearUart.count==2);
    state.rearExtendedMs=clockMs;rearUartReady=false;forwardLiveTvCommand();CHECK(rearUart.count==2);
    rearUartReady=true;liveCommandSeen=false;forwardLiveTvCommand();CHECK(rearUart.count==2);
    liveCommandSeen=true;
    // All supported setpoints round-trip over the existing nine-byte protocol.
    unsigned expected=2;
    for(unsigned strength=0;strength<=100;strength+=5){
        for(unsigned limit=0;limit<=100;limit+=5){
            clockMs+=100;state.rearExtendedMs=liveCommandMs=clockMs;
            liveCommand.strength_percent=strength;liveCommand.limit_percent=limit;
            liveCommand.flags=0;forwardLiveTvCommand();CHECK(rearUart.count==++expected);
            CHECK(djy_uart_unpack_live_tv(&decoded,rearUart.frame));
            CHECK(decoded.strength_percent==strength && decoded.limit_percent==limit && decoded.flags==0);
        }
    }
    uint8_t invalid[DJY_UART_LIVE_TV_SIZE];
    liveCommand.strength_percent=36;djy_uart_pack_live_tv(invalid,&liveCommand);
    CHECK(!djy_uart_unpack_live_tv(&decoded,invalid));
    liveCommand.strength_percent=35;liveCommand.limit_percent=101;
    djy_uart_pack_live_tv(invalid,&liveCommand);CHECK(!djy_uart_unpack_live_tv(&decoded,invalid));
#endif
}
'''
        with tempfile.TemporaryDirectory(prefix='djy-live-tx-') as folder:
            path = Path(folder) / 'test.cpp'
            path.write_text(harness, encoding='utf-8')
            for read_only in (0, 1):
                with self.subTest(read_only=read_only):
                    executable = Path(folder) / f'test-{read_only}.exe'
                    build = subprocess.run(compiler(True) + ['-std=c++11',
                        f'-DEV_RECEIVE_ONLY={read_only}', '-I',
                        str(ROOT / 'firmware/esp32_ev_gateway/include'), str(path),
                        '-o', str(executable)], capture_output=True, timeout=180)
                    self.assertEqual(build.returncode, 0, build.stderr.decode(errors='replace'))
                    result = subprocess.run([str(executable)], capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
