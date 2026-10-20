"""Desktop uploader for a prepared DJY ESP32-S3 firmware package."""
import hashlib
import json
import os
import re
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.request

from serial.tools import list_ports

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT = Path(__file__).resolve().parent / 'firmware'
if not DEFAULT.is_dir():
    DEFAULT = PROJECT / '.local/aws/esp-upload'
IMAGES = [('0x0', 'bootloader.bin'), ('0x8000', 'partitions.bin'),
          ('0xe000', 'boot_app0.bin'), ('0x10000', 'firmware.bin')]


def flash_command(package, port):
    if port is not None and (not port.startswith('COM') or not port[3:].isdigit()):
        raise ValueError('COM 포트를 선택하세요.')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8-sig'))
    if manifest.get('target') != 'ESP32-S3 R8':
        raise ValueError('ESP32-S3 R8 패키지가 아닙니다.')
    command = [sys.executable, '-m', 'esptool', '--chip', 'esp32s3',
               '--port', port, '--baud', '115200', '--before', 'default-reset',
               '--after', 'hard-reset', 'write-flash']
    for address, name in IMAGES:
        image = package / 'binary' / name
        if hashlib.sha256(image.read_bytes()).hexdigest() != manifest['images'][name]:
            raise ValueError(f'펌웨어 검증 실패: {name}')
        command.extend([address, str(image)])
    return command


def confirmed_s3(result):
    return result.returncode == 0 and bool(re.search(r'Chip (?:is|type:)\s*ESP32-S3\b', result.stdout))


def is_usb_reader(command, port):
    return (bool(re.search(r'[/\\]DJY_EVTQV[/\\]gateway[/\\]ev_gateway\.py', command, re.I))
            and bool(re.search(r'--serial\s+' + re.escape(port) + r'(?:\s|$)', command, re.I))
            and '--http-port 8767' in command
            and '--relay-token-env DJY_EVTQV_LOCAL_ONLY_NO_TOKEN' in command
            and '--enable-control' not in command)


def pause_usb_reader(port, emit):
    script = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=True)
    processes = json.loads(result.stdout or '[]')
    if isinstance(processes, dict):
        processes = [processes]
    readers = [p for p in processes if is_usb_reader(p.get('CommandLine') or '', port)]
    if not readers:
        return None
    # Never interrupt an active recording, or stop an unverified process.
    with urllib.request.urlopen('http://127.0.0.1:8767/api/telemetry', timeout=3) as response:
        telemetry = json.load(response)
    if telemetry.get('recording_active') is not False:
        raise ValueError('USB 기록을 먼저 중지한 뒤 다시 업로드하세요.')
    emit('기존 USB 수신 프로그램을 잠시 중지합니다.\n')
    ids = ','.join(str(int(p['ProcessId'])) for p in readers)
    subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                    f'Stop-Process -Id {ids} -ErrorAction Stop'],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                   timeout=10, check=True)
    time.sleep(1)
    return readers[0]['CommandLine']


class Uploader:
    def __init__(self, root):
        self.root, self.package = root, DEFAULT
        self.events, self.running = queue.Queue(), False
        root.title('DJY ESP 펌웨어 업로더')
        root.geometry('760x560')
        root.protocol('WM_DELETE_WINDOW', self.close)
        panel = ttk.Frame(root, padding=14)
        panel.pack(fill='both', expand=True)
        self.info = ttk.Label(panel, wraplength=720)
        self.info.pack(anchor='w', pady=(0, 12))
        controls = ttk.Frame(panel)
        controls.pack(fill='x')
        self.port = ttk.Combobox(controls, state='readonly', width=42)
        self.port.pack(side='left')
        self.refresh_button = ttk.Button(controls, text='USB 새로고침', command=self.refresh)
        self.refresh_button.pack(side='left', padx=6)
        self.package_button = ttk.Button(controls, text='펌웨어 폴더 선택', command=self.choose)
        self.package_button.pack(side='left')
        self.upload_button = ttk.Button(panel, text='ESP 업로드', command=self.upload)
        self.upload_button.pack(anchor='w', pady=12)
        self.status = ttk.Label(panel, text='USB를 연결하고 ESP 업로드를 누르세요.')
        self.status.pack(anchor='w')
        ttk.Label(panel, wraplength=720, text='응답 없음: BOOT을 누른 채 EN(RESET)을 한 번 누르고 BOOT을 놓은 뒤 재시도하세요.\n포트 사용 중: 시리얼 모니터나 USB 수신 프로그램을 종료하세요. 업로드 중 USB를 빼지 마세요.').pack(anchor='w', pady=8)
        self.log = tk.Text(panel, height=16, wrap='word')
        self.log.pack(fill='both', expand=True)
        self.show_package()
        self.refresh()
        root.after(100, self.poll)
        if '--upload' in sys.argv and '--smoke' not in sys.argv:
            root.after(500, self.upload)

    def show_package(self):
        try:
            m = json.loads((self.package / 'manifest.json').read_text(encoding='utf-8-sig'))
            self.info.config(text=f"{m['target']} · STM 변경 없음\n서버: {m.get('public_url', '?')} · Wi-Fi: {m.get('ssid', '?')}\n빌드 준비: {m.get('prepared_at_utc', '?')}\n패키지: {self.package}")
        except (OSError, ValueError, KeyError) as exc:
            self.info.config(text=f'펌웨어 폴더를 선택하세요: {exc}')

    def refresh(self):
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        self.port['values'] = ['자동 검색 · ESP 칩 응답 확인'] + [f'{p.device} | {p.description}' for p in ports]
        self.port.current(0)
        if ports:
            self.status.config(text='COM 번호를 지정하지 않고 ESP 응답으로 장치를 찾습니다.')
        else:
            self.status.config(text='연결된 COM 포트가 없습니다. USB 케이블을 확인하세요.')

    def choose(self):
        folder = filedialog.askdirectory(title='manifest.json과 binary 폴더가 있는 위치')
        if folder:
            self.package = Path(folder)
            self.show_package()

    def upload(self):
        if self.running:
            return
        try:
            selected = self.port.get().split(' | ')[0]
            command = flash_command(self.package, selected if selected.startswith('COM') else None)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror('업로드 준비 실패', str(exc))
            return
        self.running = True
        for button in (self.refresh_button, self.package_button, self.upload_button):
            button.config(state='disabled')
        self.port.config(state='disabled')
        self.log.delete('1.0', 'end')
        self.status.config(text='검증 완료. 업로드 중…')
        threading.Thread(target=self.worker, args=(command, self.package), daemon=True).start()

    def worker(self, command, package):
        readers = []
        result = -1
        try:
            log_path = package / 'upload-desktop.log'
            log_path.write_text('', encoding='utf-8')
            port = command[command.index('--port') + 1]
            candidates = [port] if port else [p.device for p in list_ports.comports() if p.vid is not None]
            found = []
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            for candidate in candidates:
                reader = pause_usb_reader(candidate, lambda line: self.events.put(('log', line)))
                if reader:
                    readers.append(reader)
                self.events.put(('log', f'{candidate}: ESP 칩 응답을 확인합니다. 아직 플래시에 쓰지 않습니다.\n'))
                probe = subprocess.run([sys.executable, '-m', 'esptool', '--port', candidate,
                                        '--baud', '115200', '--no-stub', '--connect-attempts', '2',
                                        '--after', 'no-reset', 'chip-id'], capture_output=True,
                                       text=True, encoding='utf-8', errors='replace', env=env,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
                self.events.put(('log', probe.stdout + probe.stderr))
                with log_path.open('a', encoding='utf-8') as log:
                    log.write(probe.stdout + probe.stderr)
                if confirmed_s3(probe):
                    found.append(candidate)
            if not found:
                raise ValueError('ESP32-S3 응답이 확인된 포트가 없습니다. USB 변환기만 보여도 ESP 연결이 확인된 것은 아닙니다. USB 단자·전원·BOOT 상태를 확인하세요.')
            if len(found) != 1:
                raise ValueError('ESP가 여러 개 확인됐습니다. 포트를 직접 선택한 뒤 다시 업로드하세요.')
            command[command.index('--port') + 1] = found[0]
            self.events.put(('log', f'ESP32-S3 확인 완료: {found[0]} · 이 장치에 업로드합니다.\n'))
            log_path = package / 'upload-desktop.log'
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            with log_path.open('a', encoding='utf-8') as log:
                for attempt in range(2):
                    output = ''
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, encoding='utf-8', errors='replace', env=env,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                    for line in process.stdout:
                        output += line
                        log.write(line)
                        log.flush()
                        self.events.put(('log', line))
                    result = process.wait()
                    if result == 0 or 'No serial data received' not in output or attempt == 1:
                        break
                    self.events.put(('log', '자동 리셋 후 연결을 한 번 더 시도합니다.\n'))
                    time.sleep(1)
        except Exception as exc:
            self.events.put(('log', f'{exc}\n'))
        finally:
            for reader in readers:
                try:
                    with (package / 'usb-reader.log').open('a', encoding='utf-8') as log:
                        subprocess.Popen(reader, stdout=log, stderr=log,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                    self.events.put(('log', '기존 USB 수신 프로그램을 다시 시작했습니다.\n'))
                except OSError as exc:
                    self.events.put(('log', f'USB 수신 프로그램 복구 실패: {exc}\n'))
            self.events.put(('done', result))

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'log':
                self.log.insert('end', value)
                self.log.see('end')
            else:
                self.running = False
                for button in (self.refresh_button, self.package_button, self.upload_button):
                    button.config(state='normal')
                self.port.config(state='readonly')
                self.status.config(text='업로드 성공 · ESP 재시작 완료' if value == 0 else '업로드 실패 · 위 안내에 따라 확인 후 다시 업로드하세요.')
        self.root.after(100, self.poll)

    def close(self):
        if self.running:
            messagebox.showinfo('업로드 중', '업로드가 끝난 뒤 창을 닫아주세요.')
        else:
            self.root.destroy()


def self_check():
    assert confirmed_s3(subprocess.CompletedProcess([], 0, 'Chip type: ESP32-S3 (QFN56)'))
    assert confirmed_s3(subprocess.CompletedProcess([], 0, 'Chip is ESP32-S3 (revision v0.2)'))
    assert not confirmed_s3(subprocess.CompletedProcess([], 1, 'Failed to connect to ESP32-S3'))
    assert not confirmed_s3(subprocess.CompletedProcess([], 0, 'Chip type: ESP32'))
    known = r'python.exe C:\project\DJY_EVTQV\gateway\ev_gateway.py --serial COM3 --http-port 8767 --relay-token-env DJY_EVTQV_LOCAL_ONLY_NO_TOKEN'
    assert is_usb_reader(known, 'COM3')
    assert not is_usb_reader(known, 'COM4')
    assert not is_usb_reader(known + ' --enable-control', 'COM3')
    assert not is_usb_reader(known.replace('DJY_EVTQV', 'DJY_EVTLMT'), 'COM3')
    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder)
        (package / 'binary').mkdir()
        manifest = {'target': 'ESP32-S3 R8', 'images': {}}
        for _, name in IMAGES:
            data = name.encode()
            (package / 'binary' / name).write_bytes(data)
            manifest['images'][name] = hashlib.sha256(data).hexdigest()
        (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        assert flash_command(package, 'COM3')[-8::2] == [a for a, _ in IMAGES]
        (package / 'binary/firmware.bin').write_bytes(b'corrupted')
        try:
            flash_command(package, 'COM3')
        except ValueError:
            pass
        else:
            raise AssertionError('Corrupted firmware was accepted')
    flash_command(DEFAULT, 'COM3')
    print('Package hashes, flash offsets and corrupted image rejection: OK; no flash performed.')


if __name__ == '__main__':
    if '--check' in sys.argv:
        self_check()
    else:
        root = tk.Tk()
        Uploader(root)
        if '--smoke' in sys.argv:
            root.after(300, root.destroy)
        root.mainloop()
