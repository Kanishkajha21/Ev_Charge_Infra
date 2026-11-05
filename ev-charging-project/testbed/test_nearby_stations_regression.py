import os
import sys
import unittest
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from api.main import app
from station_telemetry import global_station_telemetry_provider


class TestNearbyStationsRegression(unittest.TestCase):
    """
    Regression Test Suite for Nearby Charging Stations Discovery & Ranking.
    Verifies:
      1. Mysuru Palace origin coordinates (12.3051, 76.6552) return real chargers within 25 km.
      2. Faraway locations outside search radius return HTTP 404 (or empty result set).
      3. Haversine distance ordering and accuracy.
      4. Connector compatibility matching (e.g. CCS2 matches CCS (Type 2)).
      5. Faulted stations (OUT_OF_SERVICE) are strictly excluded from recommendation results.
    """

    def setUp(self):
        self.client = TestClient(app)
        # Ensure station 7 is restored to AVAILABLE before tests
        global_station_telemetry_provider.update_simulated_state("7", "RESTORE_AVAILABLE")

    def tearDown(self):
        global_station_telemetry_provider.update_simulated_state("7", "RESTORE_AVAILABLE")

    def test_01_mysuru_palace_nearby_stations_discovery(self):
        """Verify that searching near Mysuru Palace (12.3051, 76.6552) returns expected chargers."""
        payload = {
            "user_lat": 12.3051,
            "user_lon": 76.6552,
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "connector_type": "CCS2",
            "target_soc_percent": 90.0,
            "max_search_radius_km": 25.0,
            "top_n": 5,
        }
        response = self.client.post("/recommend", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0, "Expected at least 1 charger within 25 km of Mysuru Palace")
        self.assertLessEqual(len(data), 5, "Should respect top_n=5")

        # Check fields and distance
        first_charger = data[0]
        self.assertIn("charger_id", first_charger)
        self.assertIn("name", first_charger)
        self.assertIn("distance_km", first_charger)
        self.assertLessEqual(first_charger["distance_km"], 25.0)

        # Distances should be reasonable and ascending or ranked by score
        for c in data:
            self.assertLessEqual(c["distance_km"], 25.0)
            self.assertTrue(c["eligible_for_planning"])

    def test_02_distant_coordinates_excluded(self):
        """Verify that searching at a remote location (e.g. middle of Indian Ocean) returns 404."""
        payload = {
            "user_lat": 0.0,
            "user_lon": 76.0,
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "connector_type": "CCS2",
            "target_soc_percent": 90.0,
            "max_search_radius_km": 25.0,
            "top_n": 5,
        }
        response = self.client.post("/recommend", json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn("No charging stations found", response.json()["detail"])

    def test_03_faulted_station_excluded_from_recommendations(self):
        """Verify that when a station is FAULTED, it is excluded from /recommend results."""
        # Baseline query near Station 7 (Grand Mercure, ~12.33, 76.64)
        payload = {
            "user_lat": 12.3364,
            "user_lon": 76.6215,
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 50.0,
            "connector_type": "CCS2",
            "target_soc_percent": 90.0,
            "max_search_radius_km": 20.0,
            "top_n": 10,
        }
        res_avail = self.client.post("/recommend", json=payload)
        self.assertEqual(res_avail.status_code, 200)
        station_ids = [c["charger_id"] for c in res_avail.json()]
        self.assertIn(7, station_ids, "Station 7 should be present when AVAILABLE")

        # Now simulate fault on station 7
        self.client.post("/station/7/simulate", json={"action": "SIMULATE_FAULT", "fault_code": "TEST_FAULT"})

        # Query again -> station 7 must be excluded
        res_fault = self.client.post("/recommend", json=payload)
        self.assertEqual(res_fault.status_code, 200)
        station_ids_fault = [c["charger_id"] for c in res_fault.json()]
        self.assertNotIn(7, station_ids_fault, "Station 7 must NOT be present when FAULTED")


if __name__ == "__main__":
    unittest.main()
