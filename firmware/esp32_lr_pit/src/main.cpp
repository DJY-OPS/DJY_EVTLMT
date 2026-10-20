#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>
#include "../../esp32_lr_common/include/lr_settings.h"

namespace {
WiFiUDP telemetry, probe;
IPAddress vehicle;
bool haveVehicle = false;
uint32_t received = 0, dropped = 0, pongCount = 0, lastData = 0;
uint32_t lastProbe = 0, nonce = 0, pendingNonce = 0, probeSent = 0, rtt = 0;
bool probePending = false;
// Keep a whole frame until serial transmission completes: no partial JSON drops.
char wire[16384 + 8];
size_t wireLength = 0, wireOffset = 0;

uint16_t crc16(const char* data, size_t size) {
    uint16_t crc = 0xffff;
    for (size_t i = 0; i < size; ++i) {
        crc ^= static_cast<uint16_t>(static_cast<uint8_t>(data[i])) << 8;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021) : static_cast<uint16_t>(crc << 1);
    }
    return crc;
}

void drainSerial() {
    if (wireOffset == wireLength) { wireOffset = wireLength = 0; return; }
    const int available = Serial.availableForWrite();
    if (available <= 0) return;
    const size_t count = min(wireLength - wireOffset, static_cast<size_t>(available));
    wireOffset += Serial.write(reinterpret_cast<const uint8_t*>(wire + wireOffset), count);
}

void receiveTelemetry() {
    const int size = telemetry.parsePacket();
    if (size <= 0) return;
    const IPAddress source = telemetry.remoteIP();
    if (wireLength != 0 || size > 16384 || size < 2) {
        while (telemetry.available()) telemetry.read();
        ++dropped;
        return;
    }
    const int length = telemetry.read(reinterpret_cast<uint8_t*>(wire), size);
    if (length != size || wire[0] != '{' || wire[size - 1] != '}' ||
        memchr(wire, '\n', size) || memchr(wire, '\r', size) || memchr(wire, '\t', size)) {
        ++dropped;
        return;
    }
    vehicle = source;
    haveVehicle = true;
    lastData = millis();
    ++received;
    const uint16_t crc = crc16(wire, size);
    const int tail = snprintf(wire + size, sizeof(wire) - size, "\t%04X\n", crc);
    wireLength = static_cast<size_t>(size + tail);
    wireOffset = 0;
}

void serviceProbe() {
    const uint32_t now = millis();
    if (haveVehicle && now - lastData < 5000u && now - lastProbe >= 2000u) {
        lastProbe = now;
        pendingNonce = ++nonce;
        char request[48];
        snprintf(request, sizeof(request), "DJYLRPING:%lu", static_cast<unsigned long>(pendingNonce));
        if (probe.beginPacket(vehicle, DJY_LR_COMMAND_PORT)) {
            probe.write(reinterpret_cast<const uint8_t*>(request), strlen(request));
            probePending = probe.endPacket() == 1;
            probeSent = now;
        }
    }
    const int length = probe.parsePacket();
    if (length > 0) {
        char reply[64] = {};
        const bool peer = haveVehicle && probe.remoteIP() == vehicle && probe.remotePort() == DJY_LR_COMMAND_PORT;
        const int got = probe.read(reinterpret_cast<uint8_t*>(reply), sizeof(reply) - 1);
        while (probe.available()) probe.read();
        char expected[48];
        snprintf(expected, sizeof(expected), "DJYLRPONG:%lu", static_cast<unsigned long>(pendingNonce));
        if (peer && probePending && got == length && strcmp(reply, expected) == 0 && now - probeSent < 2000u) {
            rtt = now - probeSent;
            ++pongCount;
            probePending = false;
        }
    }
}

void reportStatus() {
    static uint32_t previous = 0;
    if (millis() - previous < 5000u || wireLength != 0) return;
    char status[240];
    const int size = snprintf(status, sizeof(status),
        "# LR pit stations=%u rx=%lu dropped=%lu pong=%lu rtt_ms=%lu age_ms=%ld\n",
        WiFi.softAPgetStationNum(), static_cast<unsigned long>(received),
        static_cast<unsigned long>(dropped), static_cast<unsigned long>(pongCount),
        static_cast<unsigned long>(rtt), haveVehicle ? static_cast<long>(millis() - lastData) : -1L);
    if (size > 0 && size < static_cast<int>(sizeof(status)) && Serial.availableForWrite() >= size) {
        Serial.write(reinterpret_cast<const uint8_t*>(status), size);
        previous = millis();
    }
}
}

void setup() {
    Serial.setTxBufferSize(4096);
    Serial.begin(DJY_LR_USB_BAUD);
    WiFi.mode(WIFI_AP);
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_AP, WIFI_PROTOCOL_LR));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW_HT20));
    const IPAddress local(192, 168, 4, 1), mask(255, 255, 255, 0);
    if (!WiFi.softAPConfig(local, local, mask) ||
        !WiFi.softAP(DJY_LR_SSID, DJY_LR_PASSWORD, DJY_LR_CHANNEL, false, 1) ||
        !telemetry.begin(DJY_LR_DATA_PORT) || !probe.begin(9007)) {
        Serial.println("# LR pit initialization failed");
        while (true) delay(1000);
    }
    uint8_t protocol = 0;
    ESP_ERROR_CHECK(esp_wifi_get_protocol(WIFI_IF_AP, &protocol));
    if (protocol != WIFI_PROTOCOL_LR) {
        Serial.println("# LR protocol mismatch; stopped");
        while (true) delay(1000);
    }
    Serial.println("# DJY LR pit ready: LR-only AP, telemetry USB 3000000, bidirectional ping");
}

void loop() {
    // PC usb_start is harmless; no actuator commands are forwarded by this build.
    for (unsigned i = 0; i < 256 && Serial.available(); ++i) Serial.read();
    drainSerial();
    receiveTelemetry();
    serviceProbe();
    drainSerial();
    reportStatus();
    delay(1);
}
