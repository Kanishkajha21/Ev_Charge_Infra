"""
Station Telemetry & Hardware Interface Module
=============================================
Provides a hardware-ready charger telemetry and state interface.

Design Principles:
------------------
1. Source Agnostic: Consumes telemetry from a provider abstraction (Simulated or Hardware/ESP32).
   The Station Console UI does not care whether telemetry originates from simulation or real hardware.
2. Authoritative Operational Safety: Telemetry state changes synchronize with Change 27
   Operational Eligibility Gate. A FAULT / OUT_OF_SERVICE state hard-excludes the station from
   journey planning and recommendations.
3. Hardware-Ready Contract: Prepares data models for ESP32 + INA219 current/voltage sensor + relay
   actuator without requiring API or UI refactoring when physical hardware is connected.
"""

from __future__ import annotations

import enum
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from operational_safety import (
    OperationalStatus,
    HardwareState,
    OperationalPolicy,
    OperationalEligibilityResult,
    check_operational_eligibility,
    map_hardware_state_to_operational_status,
    global_feedback_manager,
    parse_datetime_safe,
    set_global_telemetry_provider,
    normalize_station_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TelemetrySource(str, enum.Enum):
    """Origin source of the telemetry stream."""
    SIMULATED = "SIMULATED"
    HARDWARE = "HARDWARE"


class TelemetryQuality(str, enum.Enum):
    """Quality / measurement mode classification of electrical readings."""
    MEASURED = "MEASURED"    # Sourced from real physical current/voltage sensor (e.g. INA219 / INA226)
    NOMINAL = "NOMINAL"      # Nominal/estimated electrical values when hardware runs without physical sensor
    SIMULATED = "SIMULATED"  # Sourced from software simulation/test environment


class StationOperationalState(str, enum.Enum):
    """
    Console & hardware operational states.
    Directly aligns with HardwareState and OperationalStatus.
    """
    AVAILABLE = "AVAILABLE"
    CHARGING = "CHARGING"
    COMPLETE = "COMPLETE"
    FAULTED = "FAULTED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    MAINTENANCE = "MAINTENANCE"


class StationCommandType(str, enum.Enum):
    """Supported hardware command types sent from dashboard."""
    START = "START"
    COMPLETE = "COMPLETE"
    STOP = "STOP"


class StationCommandStatus(str, enum.Enum):
    """Lifecycle tracking states for hardware station commands."""
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass
class StationCommandRecord:
    """Represents a queued or processed command for a physical station."""
    command_id: str
    station_id: str
    command: str
    status: str = StationCommandStatus.QUEUED.value
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    execution_success: Optional[bool] = None
    resulting_state: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "station_id": self.station_id,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "execution_success": self.execution_success,
            "resulting_state": self.resulting_state,
            "error_message": self.error_message,
        }


class StationCommandQueue:
    """
    Concurrency-safe in-memory command queue for physical ESP32 station controllers.
    Guarantees FIFO delivery, prevents conflicting concurrent executions,
    and supports safe polling retries until explicit acknowledgement.
    Note: Pending commands in this demo queue are in-memory and will reset if backend restarts.
    """

    VALID_TRANSITIONS = {
        StationCommandType.START.value: {
            "allowed_current": [StationOperationalState.AVAILABLE.value],
            "expected_state": StationOperationalState.CHARGING.value,
        },
        StationCommandType.COMPLETE.value: {
            "allowed_current": [StationOperationalState.CHARGING.value],
            "expected_state": StationOperationalState.COMPLETE.value,
        },
        StationCommandType.STOP.value: {
            "allowed_current": [StationOperationalState.CHARGING.value],
            "expected_state": StationOperationalState.AVAILABLE.value,
        },
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._commands: Dict[str, StationCommandRecord] = {}  # command_id -> record
        self._station_queues: Dict[str, List[str]] = {}       # station_id -> list of command_ids
        self._active_command: Dict[str, Optional[str]] = {}   # station_id -> in-flight command_id

    def submit_command(
        self,
        station_id: Union[int, str],
        command: str,
        current_record: StationTelemetryRecord,
    ) -> StationCommandRecord:
        norm_id = normalize_station_id(station_id) or str(station_id)
        cmd_clean = command.strip().upper()

        if cmd_clean not in self.VALID_TRANSITIONS:
            raise ValueError(
                f"Unsupported command '{command}'. Supported commands are: {list(self.VALID_TRANSITIONS.keys())}"
            )

        with self._lock:
            # 1. Heartbeat & Connection Verification
            now = datetime.now(timezone.utc)
            if current_record.source == TelemetrySource.HARDWARE.value:
                hb = current_record.last_heartbeat
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                age = max(0.0, (now - hb).total_seconds())
                if age > SimulatedStationTelemetryProvider.HARDWARE_HEARTBEAT_TIMEOUT_SECONDS or not current_record.controller_connected:
                    raise ValueError(
                        f"Station #{norm_id} ESP32 controller is disconnected or heartbeat is stale ({round(age, 1)}s old). Command rejected."
                    )
            elif not current_record.controller_connected:
                raise ValueError(f"Station #{norm_id} controller is not connected.")

            # 2. Strict Safety Gate & Fault Lockout
            if current_record.fault or current_record.state in (
                StationOperationalState.FAULTED.value,
                StationOperationalState.OUT_OF_SERVICE.value,
            ):
                raise ValueError(
                    f"Safety lockout active: Station #{norm_id} is FAULTED / OUT_OF_SERVICE. "
                    "Hardware commands cannot clear a fault or bypass safety lockout. "
                    "Fault must be resolved at the physical station."
                )

            # 3. State Transition Validation
            rule = self.VALID_TRANSITIONS[cmd_clean]
            if current_record.state not in rule["allowed_current"]:
                raise ValueError(
                    f"Invalid state transition: Cannot execute '{cmd_clean}' when station state is '{current_record.state}'. "
                    f"Required state: {rule['allowed_current']}."
                )

            # 4. Check if an active unacknowledged command already exists for this station
            active_id = self._active_command.get(norm_id)
            if active_id and active_id in self._commands:
                existing = self._commands[active_id]
                if existing.status in (StationCommandStatus.QUEUED.value, StationCommandStatus.DELIVERED.value):
                    # Check age: if older than 30s, mark expired to avoid deadlock
                    cmd_age = (now - existing.created_at).total_seconds()
                    if cmd_age <= 30.0:
                        raise ValueError(
                            f"Station #{norm_id} already has a pending command in progress ({existing.command}, status: {existing.status}). "
                            "Please wait for ESP32 confirmation."
                        )
                    else:
                        existing.status = StationCommandStatus.EXPIRED.value
                        existing.error_message = "Command timed out waiting for ESP32 acknowledgement."

            # 5. Create and enqueue record
            cmd_id = f"CMD-{uuid.uuid4().hex[:8].upper()}"
            record = StationCommandRecord(
                command_id=cmd_id,
                station_id=norm_id,
                command=cmd_clean,
                status=StationCommandStatus.QUEUED.value,
                created_at=now,
            )
            self._commands[cmd_id] = record
            if norm_id not in self._station_queues:
                self._station_queues[norm_id] = []
            self._station_queues[norm_id].append(cmd_id)
            self._active_command[norm_id] = cmd_id

            logger.info("Queued command %s (%s) for station %s", cmd_id, cmd_clean, norm_id)
            return record

    def get_pending_command(self, station_id: Union[int, str]) -> Optional[StationCommandRecord]:
        norm_id = normalize_station_id(station_id) or str(station_id)
        with self._lock:
            active_id = self._active_command.get(norm_id)
            if active_id and active_id in self._commands:
                rec = self._commands[active_id]
                if rec.status in (StationCommandStatus.QUEUED.value, StationCommandStatus.DELIVERED.value):
                    # Mark DELIVERED upon retrieval while keeping it active for retries until ACK
                    rec.status = StationCommandStatus.DELIVERED.value
                    rec.delivered_at = datetime.now(timezone.utc)
                    return rec
            return None

    def acknowledge_command(
        self,
        station_id: Union[int, str],
        command_id: str,
        execution_success: bool,
        resulting_state: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> StationCommandRecord:
        norm_id = normalize_station_id(station_id) or str(station_id)
        with self._lock:
            rec = self._commands.get(command_id)
            if not rec:
                raise ValueError(f"Command ID '{command_id}' not found.")

            if rec.station_id != norm_id:
                raise ValueError(f"Command '{command_id}' does not belong to station #{norm_id}.")

            # Handle duplicate or already processed delivery gracefully
            if rec.status in (StationCommandStatus.ACKNOWLEDGED.value, StationCommandStatus.FAILED.value):
                logger.info("Duplicate acknowledgement received for command %s (already %s)", command_id, rec.status)
                return rec

            now = datetime.now(timezone.utc)
            rec.acknowledged_at = now
            rec.execution_success = execution_success
            rec.resulting_state = resulting_state
            rec.error_message = error_message
            rec.status = StationCommandStatus.ACKNOWLEDGED.value if execution_success else StationCommandStatus.FAILED.value

            # Clear active command for station if it matches
            if self._active_command.get(norm_id) == command_id:
                self._active_command[norm_id] = None

            logger.info(
                "Command %s acknowledged for station %s (success=%s, state=%s)",
                command_id, norm_id, execution_success, resulting_state
            )
            return rec

    def get_command_status(self, command_id: str) -> Optional[StationCommandRecord]:
        with self._lock:
            return self._commands.get(command_id)


# Global singleton command queue
global_station_command_queue = StationCommandQueue()



# ---------------------------------------------------------------------------
# Telemetry Data Record Contract
# ---------------------------------------------------------------------------

@dataclass
class StationTelemetryRecord:
    """
    Unified hardware-ready station telemetry contract.
    Contains electrical measurements, session tracking, hardware health, and metadata.
    """
    station_id: str
    state: str = StationOperationalState.AVAILABLE.value
    voltage_v: float = 0.0
    current_a: float = 0.0
    power_w: float = 0.0
    energy_wh: float = 0.0
    connector_type: str = "CCS (Type 2)"
    rated_power_kw: float = 120.0
    fault: bool = False
    fault_code: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = TelemetrySource.SIMULATED.value
    telemetry_quality: str = TelemetryQuality.SIMULATED.value
    controller_connected: bool = True
    telemetry_connected: bool = True
    relay_closed: bool = False
    session_id: Optional[str] = None
    session_start_time: Optional[datetime] = None
    session_end_time: Optional[datetime] = None
    session_duration_seconds: float = 0.0
    station_name: str = "Grand Mercure Mysore"
    operator: str = "Zeon Charging"
    address: str = "2203 60 New Sayyaji Rao Road, Nelson Mandela Road"
    city: str = "Mysuru"
    reliability: float = 0.9328
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def power_kw(self) -> float:
        return round(self.power_w / 1000.0, 3)

    @property
    def energy_kwh(self) -> float:
        return round(self.energy_wh / 1000.0, 4)

    @property
    def timestamp_iso(self) -> str:
        if self.timestamp.tzinfo is None:
            return self.timestamp.replace(tzinfo=timezone.utc).isoformat()
        return self.timestamp.isoformat()

    @property
    def session_start_time_iso(self) -> Optional[str]:
        if self.session_start_time is not None:
            if self.session_start_time.tzinfo is None:
                return self.session_start_time.replace(tzinfo=timezone.utc).isoformat()
            return self.session_start_time.isoformat()
        return None

    @property
    def session_end_time_iso(self) -> Optional[str]:
        if self.session_end_time is not None:
            if self.session_end_time.tzinfo is None:
                return self.session_end_time.replace(tzinfo=timezone.utc).isoformat()
            return self.session_end_time.isoformat()
        return None

    @property
    def last_heartbeat_iso(self) -> str:
        if self.last_heartbeat.tzinfo is None:
            return self.last_heartbeat.replace(tzinfo=timezone.utc).isoformat()
        return self.last_heartbeat.isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "station_id": str(self.station_id),
            "state": self.state,
            "voltage_v": round(self.voltage_v, 2),
            "current_a": round(self.current_a, 2),
            "power_w": round(self.power_w, 2),
            "power_kw": self.power_kw,
            "energy_wh": round(self.energy_wh, 2),
            "energy_kwh": self.energy_kwh,
            "connector_type": self.connector_type,
            "rated_power_kw": self.rated_power_kw,
            "fault": self.fault,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp_iso,
            "source": self.source,
            "telemetry_quality": self.telemetry_quality,
            "controller_connected": self.controller_connected,
            "telemetry_connected": self.telemetry_connected,
            "relay_closed": self.relay_closed,
            "session_id": self.session_id,
            "session_start_time": self.session_start_time_iso,
            "session_end_time": self.session_end_time_iso,
            "session_duration_seconds": round(self.session_duration_seconds, 1),
            "station_name": self.station_name,
            "operator": self.operator,
            "address": self.address,
            "city": self.city,
            "reliability": round(self.reliability, 4),
            "last_heartbeat": self.last_heartbeat_iso,
        }


# ---------------------------------------------------------------------------
# Provider Abstraction
# ---------------------------------------------------------------------------

class BaseStationTelemetryProvider:
    """
    Abstract contract for station telemetry providers.
    Both Simulated and future Hardware providers implement this interface.
    """

    def get_telemetry(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        raise NotImplementedError

    def get_live_telemetry_if_exists(self, station_id: Union[int, str]) -> Optional[StationTelemetryRecord]:
        """Return live telemetry record if station is currently tracked, without auto-initializing."""
        return None

    def update_simulated_state(
        self,
        station_id: Union[int, str],
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> StationTelemetryRecord:
        raise NotImplementedError

    def ingest_telemetry(self, payload: Dict[str, Any]) -> StationTelemetryRecord:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Simulated Telemetry Provider
# ---------------------------------------------------------------------------

class SimulatedStationTelemetryProvider(BaseStationTelemetryProvider):
    """
    Simulates physical EV charger telemetry (voltage, current, power, energy, relay, fault).
    Used during development and testing before physical ESP32 hardware is connected.
    """

    # Default real station metadata from database (Grand Mercure Mysore, ID 7)
    DEFAULT_STATION_META = {
        "station_id": "7",
        "station_name": "Grand Mercure Mysore",
        "operator": "Zeon Charging",
        "address": "2203 60 New Sayyaji Rao Road, Nelson Mandela Road",
        "city": "Mysuru",
        "connector_type": "CCS (Type 2)",
        "rated_power_kw": 120.0,
        "reliability": 0.9328,
    }

    # Inactivity window after which hardware telemetry is considered stale/disconnected
    HARDWARE_HEARTBEAT_TIMEOUT_SECONDS = 15.0

    def __init__(self):
        self._lock = threading.RLock()
        self._stations: Dict[str, StationTelemetryRecord] = {}
        self._initialize_default_station()

    def _initialize_default_station(self) -> None:
        def_id = self.DEFAULT_STATION_META["station_id"]
        self._stations[def_id] = StationTelemetryRecord(
            station_id=def_id,
            state=StationOperationalState.AVAILABLE.value,
            voltage_v=0.0,
            current_a=0.0,
            power_w=0.0,
            energy_wh=0.0,
            connector_type=self.DEFAULT_STATION_META["connector_type"],
            rated_power_kw=self.DEFAULT_STATION_META["rated_power_kw"],
            station_name=self.DEFAULT_STATION_META["station_name"],
            operator=self.DEFAULT_STATION_META["operator"],
            address=self.DEFAULT_STATION_META["address"],
            city=self.DEFAULT_STATION_META["city"],
            reliability=self.DEFAULT_STATION_META["reliability"],
            source=TelemetrySource.SIMULATED.value,
            telemetry_quality=TelemetryQuality.SIMULATED.value,
            controller_connected=True,
            telemetry_connected=True,
            relay_closed=False,
            fault=False,
            fault_code=None,
        )

    def _ensure_station_exists(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        c_key = normalize_station_id(station_id) or str(station_id)
        if c_key not in self._stations:
            # Fallback initialized station record
            self._stations[c_key] = StationTelemetryRecord(
                station_id=c_key,
                station_name=f"Station #{c_key}",
                source=TelemetrySource.SIMULATED.value,
                telemetry_quality=TelemetryQuality.SIMULATED.value,
            )
        return self._stations[c_key]

    def get_live_telemetry_if_exists(self, station_id: Union[int, str]) -> Optional[StationTelemetryRecord]:
        """Return live telemetry record if station is currently tracked, without auto-initializing."""
        with self._lock:
            c_key = normalize_station_id(station_id) or str(station_id)
            if c_key in self._stations:
                return self._stations[c_key]
            return None

    def get_telemetry(self, station_id: Union[int, str]) -> StationTelemetryRecord:
        with self._lock:
            record = self._ensure_station_exists(station_id)
            now = datetime.now(timezone.utc)

            # Check if this record originated from physical HARDWARE
            if record.source == TelemetrySource.HARDWARE.value:
                hb = record.last_heartbeat
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                age = max(0.0, (now - hb).total_seconds())

                if age > self.HARDWARE_HEARTBEAT_TIMEOUT_SECONDS:
                    # Hardware has gone stale / lost heartbeat -> safely fallback / mark controller disconnected
                    record.controller_connected = False
                # Preserve actual ESP32 hardware timestamp and last_heartbeat
            else:
                # Software simulator: update heartbeat and timestamp continuously
                record.last_heartbeat = now
                record.timestamp = now

            # If actively charging, update session duration and accumulate energy
            if record.state == StationOperationalState.CHARGING.value and record.session_start_time:
                st = record.session_start_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (now - st).total_seconds())
                record.session_duration_seconds = elapsed

                # Realistic electrical charging simulation (CC stage: ~400V @ ~125A = 50kW)
                # Apply small deterministic variance for realism
                sec_mod = int(elapsed) % 10
                volt_delta = ((sec_mod - 5) * 0.4)
                curr_delta = (((10 - sec_mod) - 5) * 0.3)
                record.voltage_v = round(398.5 + volt_delta, 2)
                record.current_a = round(125.0 + curr_delta, 2)
                record.power_w = round(record.voltage_v * record.current_a, 2)

                # Integrate energy: Wh = Power (W) * (hours elapsed)
                # Base session Wh + live integral
                record.energy_wh = round((record.power_w * (elapsed / 3600.0)), 2)

            return record

    def update_simulated_state(
        self,
        station_id: Union[int, str],
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> StationTelemetryRecord:
        with self._lock:
            record = self._ensure_station_exists(station_id)
            params = params or {}
            now = datetime.now(timezone.utc)
            record.timestamp = now
            record.last_heartbeat = now
            record.source = TelemetrySource.SIMULATED.value
            record.controller_connected = True
            record.telemetry_connected = True
            record.telemetry_quality = TelemetryQuality.SIMULATED.value
            act_clean = action.strip().upper()

            if act_clean in ("START_CHARGING", "START"):
                record.state = StationOperationalState.CHARGING.value
                record.fault = False
                record.fault_code = None
                record.relay_closed = True
                record.session_id = f"SES-{uuid.uuid4().hex[:8].upper()}"
                record.session_start_time = now
                record.session_end_time = None
                record.session_duration_seconds = 0.0
                record.voltage_v = 398.5
                record.current_a = 125.0
                record.power_w = round(record.voltage_v * record.current_a, 2)
                record.energy_wh = 0.0

            elif act_clean in ("STOP_CHARGING", "STOP"):
                record.state = StationOperationalState.AVAILABLE.value
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                # Preserve session stats until new session

            elif act_clean in ("COMPLETE_SESSION", "COMPLETE"):
                record.state = StationOperationalState.COMPLETE.value
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                if record.session_start_time and not record.session_end_time:
                    record.session_end_time = now
                    st = record.session_start_time
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                    record.session_duration_seconds = max(0.0, (now - st).total_seconds())

            elif act_clean in ("SIMULATE_FAULT", "FAULT"):
                fault_code = params.get("fault_code") or "OVERCURRENT_TRIP"
                record.state = StationOperationalState.FAULTED.value
                record.fault = True
                record.fault_code = fault_code
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                if record.session_start_time and not record.session_end_time:
                    record.session_end_time = now

            elif act_clean in ("RESTORE_AVAILABLE", "RESTORE", "RESET"):
                record.state = StationOperationalState.AVAILABLE.value
                record.fault = False
                record.fault_code = None
                record.relay_closed = False
                record.voltage_v = 0.0
                record.current_a = 0.0
                record.power_w = 0.0
                record.session_id = None
                record.session_start_time = None
                record.session_end_time = None
                record.session_duration_seconds = 0.0
                record.energy_wh = 0.0

            else:
                raise ValueError(f"Unknown simulation action '{action}'")

            return record

    def ingest_telemetry(self, payload: Dict[str, Any]) -> StationTelemetryRecord:
        with self._lock:
            station_id = str(payload.get("station_id", "7"))
            record = self._ensure_station_exists(station_id)
            now = datetime.now(timezone.utc)

            record.source = payload.get("source", TelemetrySource.HARDWARE.value)
            record.state = payload.get("state", record.state)
            record.voltage_v = float(payload.get("voltage_v", record.voltage_v))
            record.current_a = float(payload.get("current_a", record.current_a))
            record.power_w = float(payload.get("power_w", record.voltage_v * record.current_a))
            record.energy_wh = float(payload.get("energy_wh", record.energy_wh))
            record.fault = bool(payload.get("fault", False))
            record.fault_code = payload.get("fault_code")
            record.controller_connected = bool(payload.get("controller_connected", True))
            record.telemetry_connected = bool(payload.get("telemetry_connected", False))
            record.relay_closed = bool(payload.get("relay_closed", False))
            record.last_heartbeat = now
            record.timestamp = parse_datetime_safe(payload.get("timestamp")) or now

            # Telemetry quality resolution
            if "telemetry_quality" in payload and payload["telemetry_quality"]:
                record.telemetry_quality = str(payload["telemetry_quality"]).upper()
            elif record.source == TelemetrySource.HARDWARE.value:
                # Without physical sensor or when telemetry_connected is False, explicitly mark NOMINAL
                if record.telemetry_connected and payload.get("telemetry_quality") == TelemetryQuality.MEASURED.value:
                    record.telemetry_quality = TelemetryQuality.MEASURED.value
                else:
                    record.telemetry_quality = TelemetryQuality.NOMINAL.value
            else:
                record.telemetry_quality = TelemetryQuality.SIMULATED.value

            if record.fault:
                record.state = StationOperationalState.FAULTED.value
            elif record.state in (StationOperationalState.FAULTED.value, StationOperationalState.OUT_OF_SERVICE.value) and not record.fault:
                # If state was faulted but incoming payload reports healthy / not faulted, restore state to payload state
                record.state = payload.get("state", StationOperationalState.AVAILABLE.value)
                record.fault_code = None

            return record


# ---------------------------------------------------------------------------
# Global Singleton & Helper Functions
# ---------------------------------------------------------------------------

global_station_telemetry_provider: BaseStationTelemetryProvider = SimulatedStationTelemetryProvider()
set_global_telemetry_provider(global_station_telemetry_provider)


def get_station_telemetry_with_eligibility(
    station_id: Union[int, str],
    provider: Optional[BaseStationTelemetryProvider] = None,
    policy: Optional[OperationalPolicy] = None,
) -> Dict[str, Any]:
    """
    Combines live electrical telemetry with Change 27 Authoritative Operational Safety Gate.
    Guarantees physical/telemetry state is synchronized with journey planning eligibility.
    """
    if provider is None:
        provider = global_station_telemetry_provider
    if policy is None:
        policy = OperationalPolicy()

    record = provider.get_telemetry(station_id)
    raw_dict = record.to_dict()

    # Map station state to Change 27 OperationalStatus
    if record.fault or record.state in (StationOperationalState.FAULTED.value, StationOperationalState.OUT_OF_SERVICE.value):
        op_status = OperationalStatus.OUT_OF_SERVICE
        rej_reason = f"Hardware fault detected ({record.fault_code or 'UNKNOWN_FAULT'}); relay opened."
    elif record.state == StationOperationalState.MAINTENANCE.value:
        op_status = OperationalStatus.MAINTENANCE
        rej_reason = "Station is undergoing maintenance."
    elif record.state in (StationOperationalState.AVAILABLE.value, StationOperationalState.CHARGING.value, StationOperationalState.COMPLETE.value):
        op_status = OperationalStatus.AVAILABLE
        rej_reason = None
    else:
        op_status = OperationalStatus.UNKNOWN
        rej_reason = "Operational state is unverified."

    # Run Change 27 Operational Gate
    gate_data = {
        "charger_id": record.station_id,
        "operational_status": op_status,
        "reliability": record.reliability,
        "has_active_fault": record.fault,
        "status_last_updated": record.timestamp_iso,
        "rejection_reason": rej_reason,
    }

    eligibility: OperationalEligibilityResult = check_operational_eligibility(
        gate_data,
        policy=policy,
        feedback_mgr=global_feedback_manager,
        telemetry_provider=provider,
    )

    result = dict(raw_dict)
    result["operational_status"] = eligibility.operational_status.value
    result["status_confidence"] = eligibility.status_confidence
    result["trust_score"] = eligibility.trust_score
    result["eligible_for_planning"] = eligibility.eligible
    result["rejection_reason"] = eligibility.rejection_reason
    result["data_honesty_note"] = eligibility.data_honesty_note

    return result
