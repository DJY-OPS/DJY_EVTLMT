"""Offline STM -> ESP compatibility simulation; never opens ports or sends HTTP."""
from __future__ import annotations

import binascii
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from host_compiler import compiler

OUT = ROOT / '.local' / 'firmware-compatibility'
COUNT = 100


def git_text(revision, path):
    return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT).decode('utf-8')


def function(source, signature):
    body = source[source.index(signature):]
    return body[:body.index('\n}') + 2]


def build(source, destination, includes, cpp=True):
    source_path = destination.with_suffix('.cpp' if cpp else '.c')
    source_path.write_text(source, encoding='utf-8')
    args = compiler(cpp) + ['-std=c++11' if cpp else '-std=c11', '-O0']
    for include in includes:
        args += ['-I', str(include)]
    result = subprocess.run(args + [str(source_path), '-o', str(destination)],
                            capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))


def ascii_fixture(source):
    # Use the exact committed STM printf format, substituting benign test values.
    match = re.search(r'snprintf\(line,\s*sizeof\(line\),\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', source)
    if not match:
        raise ValueError('STM ASCII sender format was not found')
    fmt = ''.join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', match[1]))
    return re.sub(r'%([0-9]*)(?:l+)?[udx]',
                  lambda m: '1'.zfill(int(m[1] or 0)), fmt).encode('ascii')


def uart_resample(data, tx_baud, rx_baud):
    """Ideal 8N1 line, idle high, center sampling. No electrical noise modeled."""
    if tx_baud == rx_baud:
        return data, 0
    bits = []
    for byte in data:
        bits.extend([0] + [(byte >> n) & 1 for n in range(8)] + [1])
    # Work in integer ticks: one TX bit=rx_baud ticks; RX bit=tx_baud ticks.
    def level(ticks):
        index = ticks // rx_baud
        return bits[index] if 0 <= index < len(bits) else 1
    output = bytearray()
    framing_errors = 0
    available_at = 0
    for index, bit in enumerate(bits):
        edge = index * rx_baud
        if bit or (index and bits[index - 1] == 0) or edge < available_at:
            continue
        if level(edge + tx_baud // 2):
            continue
        byte = sum(level(edge + ((3 + 2 * n) * tx_baud) // 2) << n for n in range(8))
        if level(edge + (19 * tx_baud) // 2) == 0:
            framing_errors += 1
        output.append(byte)
        available_at = edge + 10 * tx_baud
    return bytes(output), framing_errors


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'firmware/esp32_ev_gateway/src/main.cpp').read_text(encoding='utf-8')
    state = source[source.index('struct VehicleState {'):source.index('} state;') + len('} state;')]
    parsers = source[source.index('bool parseRearTelemetry('):source.index('void receiveRearUart()')]
    receive = function(source, 'void receiveRearUart() {')
    prefix = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <deque>
#include "djy_telemetry_protocol.h"
#include "djy_uart_protocol.h"
#include "djy_can_protocol.h"
#include "rear_timing.h"
#include "rear_uart_line.h"
#define EV_REAR_UART_RX_GPIO 18
#define HIGH 1
template<class T> T constrain(T x,T lo,T hi) { return x<lo?lo:x>hi?hi:x; }
uint32_t millis() { return 1000; }
int digitalRead(int) { return HIGH; }
bool liveCommandSeen=false,rearSamplePending=false;
DjyUartLiveTv liveCommand={};
struct { std::deque<uint8_t> bytes; int available(){return bytes.size();}
  int read(){int v=bytes.front();bytes.pop_front();return v;} } rearUart;
unsigned published=0;
void publishTelemetry(){rearSamplePending=false;++published;}
'''
    suffix = r'''
int main(int argc,char**argv) {
    if(argc!=2)return 2;
    FILE* input=std::fopen(argv[1],"rb");if(!input)return 3;
    int byte;while((byte=std::fgetc(input))!=EOF)rearUart.bytes.push_back((uint8_t)byte);
    std::fclose(input);receiveRearUart();
    std::printf("{\"accepted\":%u,\"bytes\":%lu,\"parser_errors\":%lu,\"published\":%u}",
      (unsigned)state.rearUartRx,(unsigned long)state.rearUartBytes,
      (unsigned long)state.rearUartErrors,published);
}
'''
    head = subprocess.check_output(['git', 'rev-parse', 'origin/main'], cwd=ROOT).decode().strip()
    binary_path = 'firmware/stm32_rear_binary/Core/Inc/djy_telemetry_protocol.h'
    revisions = ['e514e18', head]
    results = []
    with tempfile.TemporaryDirectory(prefix='djy-compat-') as folder:
        temp = Path(folder)
        backends = {}
        for mode in (0, 1):
            exe = temp / f'receiver-{mode}.exe'
            build(f'#define EV_REAR_UART_BINARY {mode}\n' + prefix + state + parsers + receive + suffix,
                  exe, [ROOT / 'firmware/esp32_ev_gateway/include'])
            backends[mode] = exe
        streams = {}
        for revision in revisions:
            header = git_text(revision, binary_path)
            (temp / 'sender_protocol.h').write_text(header, encoding='utf-8')
            emitter = temp / f'emitter-{revision[:7]}.exe'
            build(r'''
#include <stdio.h>
#include "sender_protocol.h"
int main(int argc,char**argv){
  if(argc!=2)return 2;FILE* file=fopen(argv[1],"wb");if(!file)return 3;
  for(unsigned i=1;i<=100;++i){
    DjyTelemetry p={0};p.sequence=i;p.snapshot_us=i*10000;
    p.rpm_left=123;p.rpm_right=456;p.tps_raw=p.tps_idle=868;p.flags=12;
    uint8_t wire[DJY_TELEMETRY_SIZE];djy_telemetry_pack(wire,&p);
    if(fwrite(wire,1,sizeof(wire),file)!=sizeof(wire))return 4;
  }return fclose(file);
}
''', emitter, [temp], cpp=False)
            output = temp / f'{revision[:7]}.bin'
            subprocess.run([str(emitter), str(output)], check=True, timeout=10)
            streams[revision] = output.read_bytes()

        def case(name, data, mode=1, expected=0, tx=460800, rx=460800, historical=True):
            wire, framing = uart_resample(data, tx, rx)
            path = temp / 'input.bin'
            path.write_bytes(wire)
            result = json.loads(subprocess.check_output([str(backends[mode]), str(path)], timeout=15))
            row = dict(case=name, tx_baud=tx, rx_baud=rx,
                       esp_mode='binary' if mode else 'ASCII', expected=expected,
                       uart_framing_errors=framing, historical=historical, **result)
            row['pass'] = result['accepted'] == expected and result['published'] == expected
            results.append(row)
            if not row['pass']:
                raise AssertionError(row)

        good = streams[head]
        case('current binary STM -> current binary ESP', good, expected=COUNT)
        case('e514e18 binary STM -> current binary ESP', streams['e514e18'], expected=COUNT)
        legacy = ascii_fixture(git_text(head, 'firmware/stm32_rear_uart/Core/Src/main.c')) * COUNT
        team = ascii_fixture(git_text(head, 'firmware/tv_stm_esp/stm_back/Core/Src/main.c')) * COUNT
        case('legacy ASCII STM -> matching ASCII ESP', legacy, mode=0, expected=COUNT, tx=115200, rx=115200)
        case('team ASCII STM -> matching ASCII ESP', team, mode=0, expected=COUNT, tx=115200, rx=115200)
        imported = ascii_fixture(git_text('4a82533', 'firmware/stm32_rear_binary/Core/Src/main.c')) * COUNT
        case('4a82533 original STM -> matching ASCII ESP', imported, mode=0,
             expected=COUNT, tx=115200, rx=115200)
        case('4a82533 original STM -> current ESP', imported, tx=115200)
        case('legacy ASCII STM -> current ESP (actual baud mismatch)', legacy, tx=115200)
        case('team ASCII STM -> current ESP (actual baud mismatch)', team, tx=115200)
        case('ASCII -> binary (baud corrected only)', team)
        case('binary -> ASCII (same baud, wrong parser)', good, mode=0)

        def mutate(offset, value, crc=True):
            output = bytearray()
            for start in range(0, len(good), 238):
                frame = bytearray(good[start:start + 238])
                frame[offset] = value if offset in (2, 4) else frame[offset] ^ value
                if crc:
                    frame[-2:] = binascii.crc_hqx(frame[:-2], 0xffff).to_bytes(2, 'little')
                output.extend(frame)
            return bytes(output)

        case('hypothetical wire version 2 (valid CRC)', mutate(2, 2), historical=False)
        case('hypothetical changed payload length (valid CRC)', mutate(4, 228), historical=False)
        broken = mutate(50, 1, crc=False)
        case('bit corruption / CRC mismatch', broken, historical=False)
        case('corrupt frames followed by matching firmware frames', broken + good,
             expected=COUNT, historical=False)

    report = dict(origin_commit=head, receiver_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  frames_per_sender=COUNT, cases=results,
                  limitations=['Host simulation; no board firmware identity verified.',
                               'UART baud mismatch uses ideal 8N1 sampling, not analog hardware.',
                               'ASCII fixtures use committed printf formats with synthetic values.',
                               'Wire version 2 and changed length are hypothetical, not GitHub releases.',
                               'No data sent to live server; Wi-Fi/ngrok/CAN are outside simulation.'])
    (OUT / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    for row in results:
        print(f"PASS {row['case']}: {row['accepted']}/{COUNT}, parser_errors={row['parser_errors']}")
    print(f'Results: {OUT / "results.json"}')


if __name__ == '__main__':
    main()
