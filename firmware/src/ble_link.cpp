#include "ble_link.h"

#include <Arduino.h>
#include <NimBLEDevice.h>

#include "config.h"

namespace {

proto::StateFrame g_frame{};
volatile uint32_t g_lastPayloadMs = 0;
volatile bool g_updated = false;
volatile bool g_connected = false;
volatile bool g_everReceived = false;

// Writes arrive on the NimBLE host task, not the Arduino loop task. The frame
// is small and written whole, so a short critical section is enough to keep
// the renderer from reading a half-updated struct.
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server) override {
    g_connected = true;
    Serial.println("[ble] central connected");
  }

  void onDisconnect(NimBLEServer* server) override {
    g_connected = false;
    Serial.println("[ble] central disconnected; advertising again");
    // Without this the device is invisible after the Mac sleeps.
    NimBLEDevice::startAdvertising();
  }
};

class StateCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic) override {
    const std::string value = characteristic->getValue();

    proto::StateFrame decoded{};
    if (!proto::decode(reinterpret_cast<const uint8_t*>(value.data()), value.size(),
                       decoded)) {
      Serial.printf("[ble] rejected %u byte payload\n",
                    static_cast<unsigned>(value.size()));
      return;
    }

    portENTER_CRITICAL(&g_mux);
    g_frame = decoded;
    g_lastPayloadMs = millis();
    g_updated = true;
    g_everReceived = true;
    portEXIT_CRITICAL(&g_mux);
  }
};

}  // namespace

namespace ble_link {

void begin() {
  NimBLEDevice::init(DEVICE_NAME);

  // The default 23-byte MTU leaves 20 usable bytes, which is less than one
  // payload. 247 gives comfortable headroom over the 108-byte maximum and is
  // within what CoreBluetooth negotiates.
  NimBLEDevice::setMTU(247);

  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  NimBLEService* service = server->createService(SERVICE_UUID);
  NimBLECharacteristic* state =
      service->createCharacteristic(STATE_CHAR_UUID, NIMBLE_PROPERTY::WRITE);
  state->setCallbacks(new StateCallbacks());
  service->start();

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->start();

  Serial.printf("[ble] advertising as %s\n", DEVICE_NAME);
}

bool linkUp(uint32_t nowMs) {
  if (!g_everReceived) return false;
  return (nowMs - g_lastPayloadMs) < LINK_TIMEOUT_MS;
}

bool hasFrame() { return g_everReceived; }

const proto::StateFrame& frame() { return g_frame; }

bool consumeUpdate() {
  bool updated = false;
  portENTER_CRITICAL(&g_mux);
  updated = g_updated;
  g_updated = false;
  portEXIT_CRITICAL(&g_mux);
  return updated;
}

}  // namespace ble_link
