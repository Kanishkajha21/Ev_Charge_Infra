"""
Change 30: Station Console & Hardware Telemetry Interface Test Suite
====================================================================
Verifies:
  1. Station telemetry contract structure and default values
  2. Simulated provider state transitions (AVAILABLE -> CHARGING -> COMPLETE -> RESTORE)
  3. Fault simulation and recovery (FAULT -> OUT_OF_SERVICE -> RESTORE)
  4. Real-time electrical calculation (voltage, current, power, energy accumulation)
  5. Authoritative Operational Safety Gate integration (FAULT -> hard-excluded)
  6. Hardware telemetry ingestion contract (source=HARDWARE)
  7. API endpoints:
     - GET /station/{station_id}/telemetry
     - POST /station/{station_id}/simulate
     - POST /station/telemetry/ingest
     - GET /station/stations
  8. Unchanged trip plan and recommendation behavior
"""

import os
import sys
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from api.main import app
from station_telemetry import (
    TelemetrySource,
    StationOperationalState,
    StationTelemetryRecord,
    SimulatedStationTelemetryProvider,
    get_station_telemetry_with_eligibility,
    global_station_telemetry_provider,
)
from operational_safety import OperationalStatus


class TestChange30StationTelemetry(unittest.TestCase):
    """Test suite for Station Console & Hardware-Ready Telemetry Interface."""

    def setUp(self):
        self.client = TestClient(app)
        self.provider = SimulatedStationTelemetryProvider()

    def test_01_telemetry_contract_structure(self):
        """Verify the station telemetry record meets all required contract fields."""
        rec = self.provider.get_telemetry("7")
        d = rec.to_dict()

        required_fields = [
            "station_id", "state", "voltage_v", "current_a", "power_w",
            "energy_wh", "connector_type", "rated_power_kw", "fault",
            "fault_code", "timestamp", "source", "controller_connected",
            "telemetry_connected", "relay_closed", "session_id",
            "session_start_time", "session_end_time", "session_duration_seconds",
            "station_name", "operator", "address", "city", "reliability",
            "last_heartbeat"
        ]
        for field in required_fields:
            self.assertIn(field, d, f"Missing required contract field: {field}")

        self.assertEqual(d["station_id"], "7")
        self.assertEqual(d["state"], "AVAILABLE")
        self.assertEqual(d["source"], "SIMULATED")
        self.assertEqual(d["telemetry_quality"], "SIMULATED")
        self.assertFalse(d["fault"])
        self.assertIsNone(d["fault_code"])
        self.assertTrue(d["controller_connected"])
        self.assertTrue(d["telemetry_connected"])
        self.assertFalse(d["relay_closed"])

    def test_02_simulated_charging_flow(self):
        """Verify state transitions: AVAILABLE -> CHARGING -> COMPLETE -> AVAILABLE."""
        # 1. Start Charging
        rec = self.provider.update_simulated_state("7", "START_CHARGING")
        self.assertEqual(rec.state, StationOperationalState.CHARGING.value)
        self.assertEqual(rec.telemetry_quality, "SIMULATED")
        self.assertTrue(rec.relay_closed)
        self.assertIsNotNone(rec.session_id)
        self.assertIsNotNone(rec.session_start_time)
        self.assertGreater(rec.voltage_v, 300.0)
        self.assertGreater(rec.current_a, 50.0)
        self.assertGreater(rec.power_w, 10000.0)

        # 2. Query telemetry while charging (energy accumulates)
        rec2 = self.provider.get_telemetry("7")
        self.assertEqual(rec2.state, StationOperationalState.CHARGING.value)
        self.assertEqual(rec2.telemetry_quality, "SIMULATED")
        self.assertGreaterEqual(rec2.power_kw, 40.0)

        # 3. Complete Session
        rec3 = self.provider.update_simulated_state("7", "COMPLETE_SESSION")
        self.assertEqual(rec3.state, StationOperationalState.COMPLETE.value)
        self.assertFalse(rec3.relay_closed)
        self.assertEqual(rec3.voltage_v, 0.0)
        self.assertEqual(rec3.current_a, 0.0)
        self.assertIsNotNone(rec3.session_end_time)

        # 4. Restore Available
        rec4 = self.provider.update_simulated_state("7", "RESTORE_AVAILABLE")
        self.assertEqual(rec4.state, StationOperationalState.AVAILABLE.value)
        self.assertFalse(rec4.relay_closed)
        self.assertIsNone(rec4.session_id)

    def test_03_fault_simulation_and_recovery(self):
        """Verify fault simulation transitions to FAULTED, shuts relay, and restores."""
        # 1. Start charging first
        self.provider.update_simulated_state("7", "START_CHARGING")

        # 2. Simulate Fault (e.g. Overcurrent)
        rec_fault = self.provider.update_simulated_state(
            "7", "SIMULATE_FAULT", {"fault_code": "OVERCURRENT_TRIP"}
        )
        self.assertEqual(rec_fault.state, StationOperationalState.FAULTED.value)
        self.assertTrue(rec_fault.fault)
        self.assertEqual(rec_fault.fault_code, "OVERCURRENT_TRIP")
        self.assertFalse(rec_fault.relay_closed)
        self.assertEqual(rec_fault.current_a, 0.0)
        self.assertEqual(rec_fault.voltage_v, 0.0)

        # 3. Restore Available
        rec_restored = self.provider.update_simulated_state("7", "RESTORE_AVAILABLE")
        self.assertEqual(rec_restored.state, StationOperationalState.AVAILABLE.value)
        self.assertFalse(rec_restored.fault)
        self.assertIsNone(rec_restored.fault_code)

    def test_04_operational_eligibility_synchronization(self):
        """Verify telemetry fault state automatically synchronizes with Change 27 Operational Gate."""
        # When AVAILABLE
        self.provider.update_simulated_state("7", "RESTORE_AVAILABLE")
        data_avail = get_station_telemetry_with_eligibility("7", provider=self.provider)
        self.assertEqual(data_avail["operational_status"], "AVAILABLE")
        self.assertTrue(data_avail["eligible_for_planning"])

        # When FAULTED
        self.provider.update_simulated_state("7", "SIMULATE_FAULT", {"fault_code": "RELAY_WELD_DETECTED"})
        data_fault = get_station_telemetry_with_eligibility("7", provider=self.provider)
        self.assertEqual(data_fault["operational_status"], "OUT_OF_SERVICE")
        self.assertFalse(data_fault["eligible_for_planning"])
        self.assertIn("Hardware fault detected", data_fault["rejection_reason"])

    def test_05_hardware_ingestion_contract(self):
        """Verify hardware ingestion accepts source=HARDWARE payloads and updates state."""
        payload = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "CHARGING",
            "voltage_v": 412.3,
            "current_a": 130.5,
            "power_w": 53805.15,
            "energy_wh": 15420.0,
            "fault": False,
            "fault_code": None,
            "controller_connected": True,
            "telemetry_connected": True,
            "telemetry_quality": "MEASURED",
            "relay_closed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        rec = self.provider.ingest_telemetry(payload)
        self.assertEqual(rec.source, "HARDWARE")
        self.assertEqual(rec.telemetry_quality, "MEASURED")
        self.assertEqual(rec.voltage_v, 412.3)
        self.assertEqual(rec.current_a, 130.5)
        self.assertEqual(rec.energy_wh, 15420.0)
        self.assertTrue(rec.relay_closed)

    def test_06_station_api_endpoints(self):
        """Verify FastAPI station endpoints."""
        # 1. GET /station/7/telemetry
        resp = self.client.get("/station/7/telemetry")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["station_id"], "7")
        self.assertIn("operational_status", body)
        self.assertIn("voltage_v", body)
        self.assertIn("telemetry_quality", body)

        # 2. POST /station/7/simulate -> START_CHARGING
        resp_sim = self.client.post("/station/7/simulate", json={"action": "START_CHARGING"})
        self.assertEqual(resp_sim.status_code, 200)
        body_sim = resp_sim.json()
        self.assertEqual(body_sim["state"], "CHARGING")
        self.assertTrue(body_sim["relay_closed"])

        # 3. POST /station/7/simulate -> SIMULATE_FAULT
        resp_fault = self.client.post(
            "/station/7/simulate",
            json={"action": "SIMULATE_FAULT", "fault_code": "INA219_COMM_LOSS"}
        )
        self.assertEqual(resp_fault.status_code, 200)
        body_fault = resp_fault.json()
        self.assertEqual(body_fault["state"], "FAULTED")
        self.assertEqual(body_fault["operational_status"], "OUT_OF_SERVICE")
        self.assertFalse(body_fault["eligible_for_planning"])

        # 4. POST /station/7/simulate -> RESTORE_AVAILABLE
        resp_rest = self.client.post("/station/7/simulate", json={"action": "RESTORE_AVAILABLE"})
        self.assertEqual(resp_rest.status_code, 200)
        body_rest = resp_rest.json()
        self.assertEqual(body_rest["state"], "AVAILABLE")
        self.assertEqual(body_rest["operational_status"], "AVAILABLE")

        # 5. POST /station/telemetry/ingest (Hardware payload)
        hw_payload = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "AVAILABLE",
            "voltage_v": 0.0,
            "current_a": 0.0,
            "power_w": 0.0,
            "energy_wh": 0.0,
            "fault": False,
            "controller_connected": True,
            "telemetry_connected": True,
            "relay_closed": False,
        }
        resp_hw = self.client.post("/station/telemetry/ingest", json=hw_payload)
        self.assertEqual(resp_hw.status_code, 200)
        body_hw = resp_hw.json()
        self.assertEqual(body_hw["source"], "HARDWARE")

        # 6. GET /station/stations
        resp_list = self.client.get("/station/stations")
        self.assertEqual(resp_list.status_code, 200)
        body_list = resp_list.json()
        self.assertIn("default_station_id", body_list)
        self.assertEqual(body_list["default_station_id"], "7")

    def test_07_simulated_provider_reports_simulated_quality(self):
        """Verify simulated provider explicitly reports SIMULATED telemetry quality."""
        rec = self.provider.get_telemetry("7")
        self.assertEqual(rec.telemetry_quality, "SIMULATED")
        data = get_station_telemetry_with_eligibility("7", provider=self.provider)
        self.assertEqual(data["telemetry_quality"], "SIMULATED")

    def test_08_hardware_ingestion_without_sensor_reports_nominal(self):
        """
        Verify that when ESP32 posts real hardware telemetry without INA219 (telemetry_connected=False),
        electrical telemetry quality is marked as NOMINAL while hardware state is REAL.
        """
        payload = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "CHARGING",
            "voltage_v": 400.0,       # Nominal rated voltage
            "current_a": 125.0,       # Nominal rated current
            "power_w": 50000.0,       # Nominal active power
            "energy_wh": 2500.0,      # Estimated session energy
            "fault": False,
            "controller_connected": True,
            "telemetry_connected": False,  # Physical INA219 sensor is absent
            "relay_closed": True,          # Real physical MOSFET/contactor switch is closed
        }
        resp = self.client.post("/station/telemetry/ingest", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        self.assertEqual(body["source"], "HARDWARE")
        self.assertEqual(body["telemetry_quality"], "NOMINAL")
        self.assertEqual(body["state"], "CHARGING")
        self.assertTrue(body["relay_closed"])
        self.assertTrue(body["controller_connected"])
        self.assertFalse(body["telemetry_connected"])
        self.assertEqual(body["operational_status"], "AVAILABLE")
        self.assertTrue(body["eligible_for_planning"])

    def test_09_hardware_fault_remains_authoritative(self):
        """
        Verify that physical fault input (e.g. real emergency push-button on hardware)
        is authoritative and hard-excludes the charger from planning immediately.
        """
        fault_payload = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "FAULTED",
            "voltage_v": 0.0,
            "current_a": 0.0,
            "power_w": 0.0,
            "energy_wh": 2500.0,
            "fault": True,
            "fault_code": "PHYSICAL_EMERGENCY_BUTTON",
            "controller_connected": True,
            "telemetry_connected": False,
            "relay_closed": False,
        }
        resp = self.client.post("/station/telemetry/ingest", json=fault_payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "HARDWARE")
        self.assertEqual(body["state"], "FAULTED")
        self.assertTrue(body["fault"])
        self.assertEqual(body["fault_code"], "PHYSICAL_EMERGENCY_BUTTON")
        self.assertEqual(body["operational_status"], "OUT_OF_SERVICE")
        self.assertFalse(body["eligible_for_planning"])

    def test_11_hardware_telemetry_authoritative_over_get_endpoint_and_stale_fallback(self):
        """
        Verify that:
        1. When HARDWARE telemetry is ingested with controller_connected=true,
           GET /station/7/telemetry reflects source=HARDWARE, controller_connected=true,
           and uses the actual hardware timestamp.
        2. When a restore payload (state=AVAILABLE, fault=False) is posted from hardware,
           GET /station/7/telemetry updates immediately to AVAILABLE and eligible_for_planning=True.
        3. When hardware heartbeat ages beyond the timeout threshold, controller_connected transitions
           to False safely to signal stale/disconnected hardware.
        """
        import time
        from datetime import datetime, timezone, timedelta

        # Ingest fresh hardware telemetry
        hw_avail = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "AVAILABLE",
            "voltage_v": 0.0,
            "current_a": 0.0,
            "power_w": 0.0,
            "energy_wh": 0.0,
            "fault": False,
            "fault_code": None,
            "controller_connected": True,
            "telemetry_connected": False,
            "telemetry_quality": "SIMULATED",
            "relay_closed": False,
        }
        resp = self.client.post("/station/telemetry/ingest", json=hw_avail)
        self.assertEqual(resp.status_code, 200)

        # Query GET /station/7/telemetry
        resp_get = self.client.get("/station/7/telemetry")
        self.assertEqual(resp_get.status_code, 200)
        data = resp_get.json()
        self.assertEqual(data["source"], "HARDWARE")
        self.assertTrue(data["controller_connected"])
        self.assertFalse(data["telemetry_connected"])
        self.assertEqual(data["state"], "AVAILABLE")
        self.assertEqual(data["operational_status"], "AVAILABLE")
        self.assertTrue(data["eligible_for_planning"])

        # Physical button press simulated: FAULT
        hw_fault = dict(hw_avail)
        hw_fault["state"] = "FAULTED"
        hw_fault["fault"] = True
        hw_fault["fault_code"] = "MANUAL_FAULT"
        resp_fault = self.client.post("/station/telemetry/ingest", json=hw_fault)
        self.assertEqual(resp_fault.status_code, 200)

        resp_get_fault = self.client.get("/station/7/telemetry")
        self.assertEqual(resp_get_fault.status_code, 200)
        data_fault = resp_get_fault.json()
        self.assertEqual(data_fault["source"], "HARDWARE")
        self.assertEqual(data_fault["state"], "FAULTED")
        self.assertTrue(data_fault["fault"])
        self.assertEqual(data_fault["operational_status"], "OUT_OF_SERVICE")
        self.assertFalse(data_fault["eligible_for_planning"])

        # Physical button press simulated: RESTORE
        resp_restore = self.client.post("/station/telemetry/ingest", json=hw_avail)
        self.assertEqual(resp_restore.status_code, 200)
        resp_get_restored = self.client.get("/station/7/telemetry")
        self.assertEqual(resp_get_restored.status_code, 200)
        data_restored = resp_get_restored.json()
        self.assertEqual(data_restored["source"], "HARDWARE")
        self.assertEqual(data_restored["state"], "AVAILABLE")
        self.assertFalse(data_restored["fault"])
        self.assertEqual(data_restored["operational_status"], "AVAILABLE")
        self.assertTrue(data_restored["eligible_for_planning"])

        # Stale heartbeat detection test
        rec = global_station_telemetry_provider._ensure_station_exists("7")
        rec.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=20)
        resp_stale = self.client.get("/station/7/telemetry")
        self.assertEqual(resp_stale.status_code, 200)
        data_stale = resp_stale.json()
        self.assertFalse(data_stale["controller_connected"])

    def test_12_bidirectional_command_queue_and_safety_rules(self):
        """
        Verify bidirectional command queue functionality and safety boundaries:
        1. AVAILABLE -> START -> CHARGING flow (submit command -> poll -> ACK -> telemetry confirmation).
        2. CHARGING -> COMPLETE -> COMPLETE flow.
        3. CHARGING -> STOP -> AVAILABLE flow.
        4. START rejected while FAULTED.
        5. COMPLETE rejected when not CHARGING.
        6. Command retrieval and acknowledgement lifecycle.
        7. Duplicate command delivery handled gracefully.
        8. ESP32 disconnected / stale heartbeat causes command rejection.
        9. Hardware fault remains strictly protected from dashboard commands.
        """
        from datetime import datetime, timezone, timedelta

        # Ensure station 7 is in AVAILABLE state with fresh heartbeat
        hw_avail = {
            "station_id": "7",
            "source": "HARDWARE",
            "state": "AVAILABLE",
            "voltage_v": 0.0,
            "current_a": 0.0,
            "power_w": 0.0,
            "energy_wh": 0.0,
            "fault": False,
            "controller_connected": True,
            "telemetry_connected": False,
        }
        self.client.post("/station/telemetry/ingest", json=hw_avail)

        # 1. AVAILABLE -> START -> QUEUED
        resp_cmd = self.client.post("/station/7/command", json={"station_id": "7", "command": "START"})
        self.assertEqual(resp_cmd.status_code, 200)
        cmd_body = resp_cmd.json()
        self.assertEqual(cmd_body["status"], "QUEUED")
        cmd_id = cmd_body["command"]["command_id"]
        self.assertEqual(cmd_body["command"]["command"], "START")

        # ESP32 polls for pending command
        resp_poll = self.client.get("/station/7/command/pending")
        self.assertEqual(resp_poll.status_code, 200)
        poll_body = resp_poll.json()
        self.assertTrue(poll_body["has_command"])
        self.assertEqual(poll_body["command_id"], cmd_id)
        self.assertEqual(poll_body["command"], "START")

        # ESP32 executes and acknowledges execution
        resp_ack = self.client.post("/station/7/command/ack", json={
            "station_id": "7",
            "command_id": cmd_id,
            "execution_success": True,
            "resulting_state": "CHARGING",
        })
        self.assertEqual(resp_ack.status_code, 200)
        self.assertEqual(resp_ack.json()["command"]["status"], "ACKNOWLEDGED")

        # ESP32 sends updated telemetry confirming CHARGING state
        hw_charging = dict(hw_avail)
        hw_charging["state"] = "CHARGING"
        hw_charging["relay_closed"] = True
        self.client.post("/station/telemetry/ingest", json=hw_charging)

        resp_get = self.client.get("/station/7/telemetry")
        self.assertEqual(resp_get.json()["state"], "CHARGING")

        # 2. Duplicate delivery check: duplicate ACK does not fail
        resp_dup = self.client.post("/station/7/command/ack", json={
            "station_id": "7",
            "command_id": cmd_id,
            "execution_success": True,
            "resulting_state": "CHARGING",
        })
        self.assertEqual(resp_dup.status_code, 200)

        # 3. CHARGING -> COMPLETE -> COMPLETE
        resp_comp = self.client.post("/station/7/command", json={"station_id": "7", "command": "COMPLETE"})
        self.assertEqual(resp_comp.status_code, 200)
        comp_id = resp_comp.json()["command"]["command_id"]

        resp_poll_comp = self.client.get("/station/7/command/pending")
        self.assertEqual(resp_poll_comp.json()["command_id"], comp_id)

        self.client.post("/station/7/command/ack", json={
            "station_id": "7",
            "command_id": comp_id,
            "execution_success": True,
            "resulting_state": "COMPLETE",
        })
        hw_complete = dict(hw_avail)
        hw_complete["state"] = "COMPLETE"
        self.client.post("/station/telemetry/ingest", json=hw_complete)

        # 4. Rejection: COMPLETE cannot be executed when station is COMPLETE (not CHARGING)
        resp_invalid = self.client.post("/station/7/command", json={"station_id": "7", "command": "COMPLETE"})
        self.assertEqual(resp_invalid.status_code, 400)
        self.assertIn("Invalid state transition", resp_invalid.json()["detail"])

        # Reset back to CHARGING to test STOP -> AVAILABLE
        hw_charging_2 = dict(hw_avail)
        hw_charging_2["state"] = "CHARGING"
        self.client.post("/station/telemetry/ingest", json=hw_charging_2)

        # 5. CHARGING -> STOP -> AVAILABLE
        resp_stop = self.client.post("/station/7/command", json={"station_id": "7", "command": "STOP"})
        self.assertEqual(resp_stop.status_code, 200)
        stop_id = resp_stop.json()["command"]["command_id"]

        self.client.post("/station/7/command/ack", json={
            "station_id": "7",
            "command_id": stop_id,
            "execution_success": True,
            "resulting_state": "AVAILABLE",
        })
        self.client.post("/station/telemetry/ingest", json=hw_avail)
        self.assertEqual(self.client.get("/station/7/telemetry").json()["state"], "AVAILABLE")

        # 6. Safety Rule: Fault station and verify commands are strictly rejected
        hw_fault = dict(hw_avail)
        hw_fault["state"] = "FAULTED"
        hw_fault["fault"] = True
        hw_fault["fault_code"] = "PHYSICAL_BUTTON_ESTOP"
        self.client.post("/station/telemetry/ingest", json=hw_fault)

        # START must be strictly rejected while FAULTED
        resp_start_fault = self.client.post("/station/7/command", json={"station_id": "7", "command": "START"})
        self.assertEqual(resp_start_fault.status_code, 400)
        self.assertIn("Safety lockout active", resp_start_fault.json()["detail"])

        # STOP must also be strictly rejected while FAULTED (cannot bypass or clear fault)
        resp_stop_fault = self.client.post("/station/7/command", json={"station_id": "7", "command": "STOP"})
        self.assertEqual(resp_stop_fault.status_code, 400)
        self.assertIn("Safety lockout active", resp_stop_fault.json()["detail"])

        # 7. Disconnected / Stale Heartbeat Rejection
        # Restore to healthy available state
        self.client.post("/station/telemetry/ingest", json=hw_avail)
        # Age heartbeat past timeout threshold
        rec = global_station_telemetry_provider._ensure_station_exists("7")
        rec.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=25)

        resp_stale_cmd = self.client.post("/station/7/command", json={"station_id": "7", "command": "START"})
        self.assertEqual(resp_stale_cmd.status_code, 400)
        self.assertIn("disconnected or heartbeat is stale", resp_stale_cmd.json()["detail"])

    def tearDown(self):
        # Always restore global provider station 7 state to AVAILABLE after tests
        global_station_telemetry_provider.update_simulated_state("7", "RESTORE_AVAILABLE")

    def test_10_fault_station_recommend_exclusion_end_to_end(self):
        """
        Regression Test:
        1. When station 7 (Grand Mercure) is AVAILABLE -> /recommend includes station 7 with eligible_for_planning=True.
        2. When station 7 is faulted via simulate API -> /station/7/telemetry reports eligible_for_planning=False.
        3. /recommend request strictly excludes station 7 from the candidates list (not merely hidden in UI).
        4. When station 7 is restored -> /recommend once again includes station 7.
        """
        # Step 1: Ensure station is restored
        resp_restore = self.client.post("/station/7/simulate", json={"action": "RESTORE_AVAILABLE"})
        self.assertEqual(resp_restore.status_code, 200)
        self.assertTrue(resp_restore.json()["eligible_for_planning"])

        rec_req = {
            "user_lat": 12.33,
            "user_lon": 76.64,
            "connector_type": "CCS2",
            "battery_capacity_kwh": 60.0,
            "current_soc_percent": 40.0,
            "target_soc_percent": 85.0,
            "max_search_radius_km": 20.0,
            "top_n": 10,
        }

        # Step 2: Query recommendations while AVAILABLE
        resp_rec_avail = self.client.post("/recommend", json=rec_req)
        self.assertEqual(resp_rec_avail.status_code, 200)
        chargers_avail = resp_rec_avail.json()
        matching_ids_avail = [c["charger_id"] for c in chargers_avail]
        self.assertIn(7, matching_ids_avail, "Station 7 (Grand Mercure) should be present in recommendations when AVAILABLE")

        # Step 3: Fault station 7
        resp_fault = self.client.post("/station/7/simulate", json={"action": "SIMULATE_FAULT", "fault_code": "OVERCURRENT_TRIP"})
        self.assertEqual(resp_fault.status_code, 200)
        fault_body = resp_fault.json()
        self.assertEqual(fault_body["operational_status"], "OUT_OF_SERVICE")
        self.assertFalse(fault_body["eligible_for_planning"])

        # Step 4: Query recommendations while FAULTED -> station 7 MUST be completely absent
        resp_rec_fault = self.client.post("/recommend", json=rec_req)
        self.assertEqual(resp_rec_fault.status_code, 200)
        chargers_fault = resp_rec_fault.json()
        matching_ids_fault = [c["charger_id"] for c in chargers_fault]
        self.assertNotIn(7, matching_ids_fault, "Station 7 (Grand Mercure) must be completely ABSENT from /recommend when faulted")

        # Step 5: Restore station 7 -> station 7 returns to recommendations
        resp_restore_2 = self.client.post("/station/7/simulate", json={"action": "RESTORE_AVAILABLE"})
        self.assertEqual(resp_restore_2.status_code, 200)
        resp_rec_restored = self.client.post("/recommend", json=rec_req)
        self.assertEqual(resp_rec_restored.status_code, 200)
        matching_ids_restored = [c["charger_id"] for c in resp_rec_restored.json()]
        self.assertIn(7, matching_ids_restored, "Station 7 (Grand Mercure) must return to /recommend after RESTORE_AVAILABLE")


if __name__ == "__main__":
    unittest.main()
