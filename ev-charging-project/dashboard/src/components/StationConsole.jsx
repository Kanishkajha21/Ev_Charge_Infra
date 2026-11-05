import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export default function StationConsole({ defaultStationId = '7', onNavigateToDriver }) {
  const [stationId] = useState(defaultStationId)
  const [telemetry, setTelemetry] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isActionLoading, setIsActionLoading] = useState(false)
  const [error, setError] = useState(null)
  const [lastActionMessage, setLastActionMessage] = useState(null)
  const [pendingHardwareCommand, setPendingHardwareCommand] = useState(null)

  const pollingRef = useRef(null)
  const pendingCommandRef = useRef(null)

  // Keep ref synchronized with pendingHardwareCommand
  useEffect(() => {
    pendingCommandRef.current = pendingHardwareCommand
  }, [pendingHardwareCommand])

  // Fetch live telemetry from provider abstraction
  const fetchTelemetry = async (silent = false) => {
    if (!silent) setIsLoading(true)
    try {
      const response = await axios.get(`${API_BASE_URL}/station/${stationId}/telemetry`)
      const freshData = response.data
      setTelemetry(freshData)
      setError(null)

      // Verification of pending hardware commands:
      // Only declare success when physical ESP32 telemetry arrives confirming the expected state
      const pending = pendingCommandRef.current
      if (pending) {
        const now = Date.now()
        if (freshData.state === pending.expectedState) {
          setLastActionMessage({
            type: 'success',
            text: `Hardware confirmed: ESP32 transitioned to ${freshData.state}.`,
          })
          setPendingHardwareCommand(null)
        } else if (freshData.fault || freshData.state === 'FAULTED') {
          setLastActionMessage({
            type: 'error',
            text: `Command aborted: Station reported fault (${freshData.fault_code || 'FAULTED'}).`,
          })
          setPendingHardwareCommand(null)
        } else if (now - pending.startTime > 12000) {
          // Timeout after 12 seconds waiting for ESP32 polling + execution + telemetry
          setLastActionMessage({
            type: 'error',
            text: `Timeout: ESP32 did not confirm '${pending.command}' transition within 12s.`,
          })
          setPendingHardwareCommand(null)
        }
      }
    } catch (err) {
      console.error('Failed to fetch station telemetry:', err)
      setError('Unable to reach station telemetry backend. Please ensure FastAPI server is running.')
    } finally {
      if (!silent) setIsLoading(false)
    }
  }

  // Continuous polling loop (1s) for real-time telemetry stream
  useEffect(() => {
    fetchTelemetry(false)

    pollingRef.current = setInterval(() => {
      fetchTelemetry(true)
    }, 1000)

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [stationId])

  // Submit hardware command to FastAPI (Queue -> ESP32 poll -> ESP32 ACK -> Telemetry confirmation)
  const handleHardwareCommand = async (commandName) => {
    if (pendingHardwareCommand || isActionLoading) return

    const expectedStateMap = {
      START: 'CHARGING',
      COMPLETE: 'COMPLETE',
      STOP: 'AVAILABLE',
    }

    const expectedState = expectedStateMap[commandName]
    if (!expectedState) return

    setIsActionLoading(true)
    setLastActionMessage({
      type: 'pending',
      text: `Command ${commandName} queued. Waiting for ESP32 confirmation...`,
    })

    try {
      const response = await axios.post(`${API_BASE_URL}/station/${stationId}/command`, {
        station_id: stationId,
        command: commandName,
      })

      // Backend accepted & queued command.
      // Dashboard does NOT optimistically update state: it waits for confirmed ESP32 telemetry.
      setPendingHardwareCommand({
        command: commandName,
        commandId: response.data.command?.command_id,
        expectedState,
        startTime: Date.now(),
      })
    } catch (err) {
      console.error('Hardware command error:', err)
      setLastActionMessage({
        type: 'error',
        text: err.response?.data?.detail || `Failed to submit command '${commandName}'.`,
      })
      setPendingHardwareCommand(null)
    } finally {
      setIsActionLoading(false)
    }
  }

  // Format ISO timestamp into clean local time
  const formatTime = (isoString) => {
    if (!isoString) return '—'
    try {
      const d = new Date(isoString)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch {
      return '—'
    }
  }

  // Real connection status: derived strictly from hardware source and active controller heartbeat
  const isHardwareConnected =
    telemetry?.source === 'HARDWARE' && telemetry?.controller_connected === true

  // State derivations
  const currentState = telemetry?.state || 'AVAILABLE'
  const isCharging = currentState === 'CHARGING'
  const isComplete = currentState === 'COMPLETE'
  const isFaulted = Boolean(telemetry?.fault || currentState === 'FAULTED' || currentState === 'OUT_OF_SERVICE')
  const isAvailable = currentState === 'AVAILABLE' && !isFaulted

  // Active LEDs (only one active according to confirmed hardware state)
  const ledGreenActive = isAvailable
  const ledBlueActive = isCharging
  const ledWhiteActive = isComplete
  const ledRedActive = isFaulted

  return (
    <div className="min-h-screen bg-[#05070A] text-slate-100 font-sans selection:bg-cockpit-teal selection:text-black flex flex-col">
      {/* Top Header */}
      <header className="sticky top-0 z-40 seamless-glass border-b border-white/5 px-6 lg:px-12 py-3.5">
        <div className="max-w-[1400px] mx-auto flex flex-wrap items-center justify-between gap-4">
          {/* Brand & Identity */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm tracking-tight text-white">VoltGuide</span>
              <span className="text-slate-600">/</span>
              <span className="text-xs uppercase tracking-wider font-mono text-cockpit-teal font-medium">
                Station Console
              </span>
            </div>
            <span className="text-slate-600">·</span>
            <div className="flex items-center gap-2 bg-white/5 px-2.5 py-1 rounded-full border border-white/5 text-xs font-mono">
              <span className="text-slate-400">STATION #{telemetry?.station_id || stationId}</span>
              <span className="text-slate-600">—</span>
              <span className="text-slate-200 font-medium">{telemetry?.station_name || 'Grand Mercure Mysore'}</span>
            </div>
          </div>

          {/* Compact Connection Indicator */}
          <div className="flex items-center gap-2 text-xs font-mono">
            <div
              className={`flex items-center gap-2 px-3 py-1 rounded-full border ${isHardwareConnected
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                }`}
            >
              <span className="relative flex h-2 w-2">
                {isHardwareConnected && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                )}
                <span
                  className={`relative inline-flex rounded-full h-2 w-2 ${isHardwareConnected ? 'bg-emerald-500' : 'bg-rose-500'
                    }`}
                ></span>
              </span>
              <span className="font-semibold tracking-wider">
                {isHardwareConnected ? 'ESP32 CONNECTED' : 'ESP32 DISCONNECTED'}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Console Body */}
      <main className="max-w-[1400px] mx-auto px-6 lg:px-12 py-8 flex-1 w-full space-y-6">
        {/* Error / Feedback Banners */}
        {error && (
          <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-sm flex items-center justify-between font-mono">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => fetchTelemetry(false)}
              className="text-xs underline hover:text-white"
            >
              Retry
            </button>
          </div>
        )}

        {lastActionMessage && (
          <div
            className={`p-3.5 rounded-xl border text-xs font-mono flex items-center justify-between ${lastActionMessage.type === 'success'
              ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
              : lastActionMessage.type === 'pending'
                ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
                : 'bg-rose-500/10 border-rose-500/20 text-rose-300'
              }`}
          >
            <div className="flex items-center gap-2">
              {lastActionMessage.type === 'pending' && (
                <span className="inline-block h-2 w-2 rounded-full bg-amber-400 animate-pulse"></span>
              )}
              <span>{lastActionMessage.text}</span>
            </div>
            <button
              type="button"
              onClick={() => setLastActionMessage(null)}
              className="opacity-70 hover:opacity-100 ml-4"
            >
              ✕
            </button>
          </div>
        )}

        {/* SECTION 1 — Station Status */}
        <section
          className={`p-6 rounded-2xl border transition-all duration-300 ${isCharging
            ? 'bg-sky-950/20 border-sky-500/30 shadow-[0_0_25px_-10px_rgba(56,189,248,0.2)]'
            : isFaulted
              ? 'bg-rose-950/25 border-rose-500/40 shadow-[0_0_25px_-10px_rgba(239,68,68,0.25)]'
              : isComplete
                ? 'bg-slate-900/40 border-white/20'
                : 'bg-emerald-950/20 border-emerald-500/30 shadow-[0_0_25px_-10px_rgba(16,185,129,0.15)]'
            }`}
        >
          <div className="flex flex-wrap items-start justify-between gap-6">
            {/* Primary State Display */}
            <div className="space-y-1.5">
              <span className="text-[11px] uppercase tracking-wider font-mono text-slate-400 block font-medium">
                Current State
              </span>
              <div className="flex items-center gap-3">
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full ${isCharging
                    ? 'bg-sky-400 animate-pulse'
                    : isFaulted
                      ? 'bg-rose-500'
                      : isComplete
                        ? 'bg-white'
                        : 'bg-emerald-400'
                    }`}
                />
                <h1 className="text-3xl font-light tracking-tight text-white font-mono">
                  {currentState}
                </h1>
                {isFaulted && telemetry?.fault_code && (
                  <span className="px-2.5 py-1 rounded bg-rose-500/20 border border-rose-500/40 text-rose-300 text-xs font-mono font-medium">
                    {telemetry.fault_code}
                  </span>
                )}
              </div>
            </div>

            {/* Structured Meta Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 text-xs font-mono">
              <div className="space-y-1">
                <span className="text-slate-400 text-[11px] block">STATION ID</span>
                <span className="text-white font-medium">#{telemetry?.station_id || stationId}</span>
              </div>

              <div className="space-y-1">
                <span className="text-slate-400 text-[11px] block">OPERATIONAL STATUS</span>
                <span
                  className={`font-semibold ${isFaulted ? 'text-rose-400' : 'text-emerald-400'
                    }`}
                >
                  {isFaulted ? 'OUT OF SERVICE' : 'OPERATIONAL'}
                </span>
              </div>

              <div className="space-y-1">
                <span className="text-slate-400 text-[11px] block">ESP32 CONNECTION</span>
                <span
                  className={`font-semibold ${isHardwareConnected ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                >
                  {isHardwareConnected ? 'CONNECTED' : 'DISCONNECTED'}
                </span>
              </div>

              <div className="space-y-1">
                <span className="text-slate-400 text-[11px] block">LAST TELEMETRY UPDATE</span>
                <span className="text-slate-300 tabular-nums">
                  {formatTime(telemetry?.timestamp)}
                </span>
              </div>
            </div>
          </div>

          {/* Fault Warning Banner */}
          {isFaulted && (
            <div className="mt-5 pt-3.5 border-t border-rose-500/20 text-xs text-rose-300 font-mono flex items-start gap-2">
              <span className="font-semibold text-rose-400">FAULT ACTIVE:</span>
              <span>
                {telemetry?.rejection_reason ||
                  (telemetry?.fault_code ? `Reported fault code: ${telemetry.fault_code}` : 'Hardware safety lockout engaged.')}
              </span>
            </div>
          )}
        </section>

        {/* SECTION 2 — ESP32 Hardware Status */}
        <section className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-6">
          <div className="flex flex-wrap items-center justify-between border-b border-white/5 pb-3 gap-2">
            <div>
              <h2 className="text-sm uppercase tracking-wider font-mono text-white font-semibold">
                ESP32 Hardware Status
              </h2>
              <p className="text-[11px] text-slate-400 font-mono mt-0.5">
                Physical LEDs and push button connected to ESP32 DevKit
              </p>
            </div>
            <div className="text-xs font-mono text-slate-400">
              Last Hardware Heartbeat: <span className="text-slate-200 tabular-nums">{formatTime(telemetry?.last_heartbeat)}</span>
            </div>
          </div>

          {/* Four Physical LED Indicators */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {/* GREEN LED — GPIO 26 — AVAILABLE */}
            <div
              className={`p-4 rounded-xl border flex flex-col items-center text-center space-y-2 transition-all ${ledGreenActive
                ? 'bg-emerald-950/30 border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.15)]'
                : 'bg-white/[0.02] border-white/5 opacity-50'
                }`}
            >
              <div className="relative flex items-center justify-center p-1.5 rounded-full bg-slate-900 border border-slate-700/80">
                <div
                  className={`h-5 w-5 rounded-full transition-all duration-300 ${ledGreenActive
                    ? 'bg-emerald-400 border border-emerald-200 shadow-[0_0_16px_rgba(52,211,153,0.9)] scale-105'
                    : 'bg-emerald-950/60 border border-emerald-900/40 opacity-40'
                    }`}
                />
              </div>
              <div>
                <span className={`text-xs font-mono font-bold tracking-tight block ${ledGreenActive ? 'text-emerald-400' : 'text-slate-400'}`}>
                  GREEN LED
                </span>
                <span className="text-[10px] font-mono text-slate-400 block">
                  GPIO 26 · AVAILABLE
                </span>
              </div>
              <span
                className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold ${ledGreenActive ? 'bg-emerald-500/20 text-emerald-300' : 'bg-white/5 text-slate-500'
                  }`}
              >
                {ledGreenActive ? 'ACTIVE' : 'INACTIVE'}
              </span>
            </div>

            {/* BLUE LED — GPIO 27 — CHARGING */}
            <div
              className={`p-4 rounded-xl border flex flex-col items-center text-center space-y-2 transition-all ${ledBlueActive
                ? 'bg-sky-950/30 border-sky-500/40 shadow-[0_0_20px_rgba(56,189,248,0.2)]'
                : 'bg-white/[0.02] border-white/5 opacity-50'
                }`}
            >
              <div className="relative flex items-center justify-center p-1.5 rounded-full bg-slate-900 border border-slate-700/80">
                <div
                  className={`h-5 w-5 rounded-full transition-all duration-300 ${ledBlueActive
                    ? 'bg-sky-400 border border-sky-200 shadow-[0_0_16px_rgba(56,189,248,0.95)] animate-pulse scale-105'
                    : 'bg-sky-950/60 border border-sky-900/40 opacity-40'
                    }`}
                />
              </div>
              <div>
                <span className={`text-xs font-mono font-bold tracking-tight block ${ledBlueActive ? 'text-sky-400' : 'text-slate-400'}`}>
                  BLUE LED
                </span>
                <span className="text-[10px] font-mono text-slate-400 block">
                  GPIO 27 · CHARGING
                </span>
              </div>
              <span
                className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold ${ledBlueActive ? 'bg-sky-500/20 text-sky-300' : 'bg-white/5 text-slate-500'
                  }`}
              >
                {ledBlueActive ? 'ACTIVE' : 'INACTIVE'}
              </span>
            </div>

            {/* WHITE LED — GPIO 14 — COMPLETE */}
            <div
              className={`p-4 rounded-xl border flex flex-col items-center text-center space-y-2 transition-all ${ledWhiteActive
                ? 'bg-slate-900/60 border-slate-400/40 shadow-[0_0_20px_rgba(255,255,255,0.15)]'
                : 'bg-white/[0.02] border-white/5 opacity-50'
                }`}
            >
              <div className="relative flex items-center justify-center p-1.5 rounded-full bg-slate-900 border border-slate-700/80">
                <div
                  className={`h-5 w-5 rounded-full transition-all duration-300 ${ledWhiteActive
                    ? 'bg-white border border-slate-200 shadow-[0_0_16px_rgba(255,255,255,0.95)] scale-105'
                    : 'bg-slate-800/60 border border-slate-700/40 opacity-40'
                    }`}
                />
              </div>
              <div>
                <span className={`text-xs font-mono font-bold tracking-tight block ${ledWhiteActive ? 'text-white' : 'text-slate-400'}`}>
                  WHITE LED
                </span>
                <span className="text-[10px] font-mono text-slate-400 block">
                  GPIO 14 · COMPLETE
                </span>
              </div>
              <span
                className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold ${ledWhiteActive ? 'bg-white/20 text-white' : 'bg-white/5 text-slate-500'
                  }`}
              >
                {ledWhiteActive ? 'ACTIVE' : 'INACTIVE'}
              </span>
            </div>

            {/* RED LED — GPIO 18 — FAULT */}
            <div
              className={`p-4 rounded-xl border flex flex-col items-center text-center space-y-2 transition-all ${ledRedActive
                ? 'bg-rose-950/30 border-rose-500/40 shadow-[0_0_20px_rgba(244,63,94,0.25)]'
                : 'bg-white/[0.02] border-white/5 opacity-50'
                }`}
            >
              <div className="relative flex items-center justify-center p-1.5 rounded-full bg-slate-900 border border-slate-700/80">
                <div
                  className={`h-5 w-5 rounded-full transition-all duration-300 ${ledRedActive
                    ? 'bg-rose-500 border border-rose-300 shadow-[0_0_20px_rgba(244,63,94,1)] scale-105'
                    : 'bg-rose-950/60 border border-rose-900/40 opacity-40'
                    }`}
                />
              </div>
              <div>
                <span className={`text-xs font-mono font-bold tracking-tight block ${ledRedActive ? 'text-rose-400' : 'text-slate-400'}`}>
                  RED LED
                </span>
                <span className="text-[10px] font-mono text-slate-400 block">
                  GPIO 18 · FAULT
                </span>
              </div>
              <span
                className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold ${ledRedActive ? 'bg-rose-500/20 text-rose-300' : 'bg-white/5 text-slate-500'
                  }`}
              >
                {ledRedActive ? 'ACTIVE' : 'INACTIVE'}
              </span>
            </div>
          </div>

          {/* Physical Fault Input Section */}
          <div className="p-4 rounded-xl bg-slate-900/50 border border-white/5 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
              <div className="flex items-center gap-2">
                <span className="text-slate-400 uppercase tracking-wider font-semibold">
                  Physical Fault Input:
                </span>
                <span className="text-slate-400">Push Button (GPIO 32)</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-slate-400">Status:</span>
                <span
                  className={`px-2.5 py-0.5 rounded font-bold uppercase tracking-wider text-[11px] ${isFaulted
                    ? 'bg-rose-500/20 border border-rose-500/40 text-rose-300'
                    : 'bg-emerald-500/20 border border-emerald-500/40 text-emerald-300'
                    }`}
                >
                  {isFaulted ? 'FAULT ACTIVE' : 'FAULT CLEAR'}
                </span>
              </div>
            </div>
            <p className="text-[11px] font-mono text-slate-400 leading-relaxed">
              Fault and restore transitions are triggered directly by the physical push button connected to GPIO 32.
              When a fault is active, hardware lockout disables charging operations.
            </p>
          </div>
        </section>

        {/* SECTION 3 — Station Operator Controls */}
        <section className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-5">
          <div className="border-b border-white/5 pb-3">
            <h2 className="text-sm uppercase tracking-wider font-mono text-white font-semibold">
              Station Operator Controls
            </h2>
            <p className="text-xs text-slate-400 mt-1">
              Bidirectional remote controls. Commands are queued in FastAPI, polled by the ESP32, physically executed (updating LEDs), and confirmed via incoming hardware telemetry.
            </p>
          </div>

          {/* Three Primary Operator Controls */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* 1. START CHARGING */}
            <button
              type="button"
              disabled={isActionLoading || pendingHardwareCommand !== null || !isAvailable || isFaulted || !isHardwareConnected}
              onClick={() => handleHardwareCommand('START')}
              className={`p-4 rounded-xl border text-xs font-mono font-bold uppercase tracking-wider transition-all flex flex-col items-center justify-center gap-1.5 shadow-sm ${isActionLoading || pendingHardwareCommand !== null || !isAvailable || isFaulted || !isHardwareConnected
                ? 'bg-slate-900/60 border-slate-800 text-slate-600 cursor-not-allowed opacity-40'
                : 'bg-sky-500/15 border-sky-400/50 text-sky-200 hover:bg-sky-500/25 hover:border-sky-300 active:scale-[0.99]'
                }`}
            >
              <div className="flex items-center gap-2 text-sm">
                <span>▶</span>
                <span>START CHARGING</span>
              </div>
              <span className="text-[10px] text-sky-400/80 font-normal">
                Target: BLUE LED (GPIO 27)
              </span>
            </button>

            {/* 2. COMPLETE */}
            <button
              type="button"
              disabled={isActionLoading || pendingHardwareCommand !== null || !isCharging || isFaulted || !isHardwareConnected}
              onClick={() => handleHardwareCommand('COMPLETE')}
              className={`p-4 rounded-xl border text-xs font-mono font-bold uppercase tracking-wider transition-all flex flex-col items-center justify-center gap-1.5 shadow-sm ${isActionLoading || pendingHardwareCommand !== null || !isCharging || isFaulted || !isHardwareConnected
                ? 'bg-slate-900/60 border-slate-800 text-slate-600 cursor-not-allowed opacity-40'
                : 'bg-white/10 border-white/30 text-white hover:bg-white/20 hover:border-white/50 active:scale-[0.99]'
                }`}
            >
              <div className="flex items-center gap-2 text-sm">
                <span>✓</span>
                <span>COMPLETE</span>
              </div>
              <span className="text-[10px] text-slate-300 font-normal">
                Target: WHITE LED (GPIO 14)
              </span>
            </button>

            {/* 3. STOP CHARGING */}
            <button
              type="button"
              disabled={isActionLoading || pendingHardwareCommand !== null || !isCharging || isFaulted || !isHardwareConnected}
              onClick={() => handleHardwareCommand('STOP')}
              className={`p-4 rounded-xl border text-xs font-mono font-bold uppercase tracking-wider transition-all flex flex-col items-center justify-center gap-1.5 shadow-sm ${isActionLoading || pendingHardwareCommand !== null || !isCharging || isFaulted || !isHardwareConnected
                ? 'bg-slate-900/60 border-slate-800 text-slate-600 cursor-not-allowed opacity-40'
                : 'bg-emerald-500/15 border-emerald-400/50 text-emerald-200 hover:bg-emerald-500/25 hover:border-emerald-300 active:scale-[0.99]'
                }`}
            >
              <div className="flex items-center gap-2 text-sm">
                <span>⏹</span>
                <span>STOP CHARGING</span>
              </div>
              <span className="text-[10px] text-emerald-400/80 font-normal">
                Target: GREEN LED (GPIO 26)
              </span>
            </button>
          </div>

          {/* Controls Context / Notice */}
          <div className="text-[11px] font-mono text-slate-400 flex flex-wrap items-center justify-between gap-2 pt-1">
            <span>
              {pendingHardwareCommand ? (
                <span className="text-amber-300 font-medium">
                  Waiting for ESP32 hardware acknowledgement for command: {pendingHardwareCommand.command}...
                </span>
              ) : !isHardwareConnected ? (
                <span className="text-rose-400">
                  ESP32 is disconnected. Operator controls are disabled until hardware connection is restored.
                </span>
              ) : isFaulted ? (
                <span className="text-rose-400">
                  Station is FAULTED. Operator controls are locked out until physical fault is cleared at the station.
                </span>
              ) : (
                <span>
                  Operator controls are enabled. State changes require physical ESP32 telemetry confirmation.
                </span>
              )}
            </span>
          </div>
        </section>
      </main>

      {/* Industrial Footer */}
      <footer className="py-4 px-6 lg:px-12 border-t border-white/5 text-center label-quiet font-mono text-[11px]">
        VoltGuide Infrastructure Node · Station ID #{telemetry?.station_id || stationId} · ESP32 Hardware Console
      </footer>
    </div>
  )
}
