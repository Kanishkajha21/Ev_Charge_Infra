import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  loadGooglePlacesApi,
  PlacesSession,
  getDestinationPredictions,
  getSelectedPlaceCoordinates,
} from '../trip/googlePlacesService'
import { useTrip } from '../trip/TripContext'
import { useTelemetry } from '../telemetry/TelemetryContext'

/**
 * Individual Waypoint Search Input (Reusable for Starting Point & Destination)
 */
function WaypointSearchInput({
  id,
  label,
  sublabel,
  placeholder,
  valueText = '',
  iconType = 'destination', // 'origin' | 'destination'
  allowCurrentLocation = false,
  isCurrentLocationSelected = false,
  currentLocationCoords = null,
  onSelectPlace,
  onSelectCurrentLocation,
  onFallbackToMap,
  isSelectingOnMap = false,
  apiStatus = 'ready',
}) {
  const [query, setQuery] = useState(valueText)
  const [predictions, setPredictions] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [isResolving, setIsResolving] = useState(false)
  const [isOpen, setIsOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)

  const sessionRef = useRef(null)
  const debounceTimerRef = useRef(null)
  const wrapperRef = useRef(null)
  const inputRef = useRef(null)

  if (!sessionRef.current) {
    sessionRef.current = new PlacesSession()
  }

  // Synchronize internal query with external value changes
  useEffect(() => {
    setQuery(valueText || '')
  }, [valueText])

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Debounced input change
  const handleInputChange = useCallback((e) => {
    const value = e.target.value
    setQuery(value)
    setHighlightedIndex(-1)

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }

    if (!value || value.trim().length < 2) {
      setPredictions([])
      setIsLoading(false)
      // If allowCurrentLocation is true, keep dropdown open to allow picking Current Location
      if (allowCurrentLocation) {
        setIsOpen(true)
      } else {
        setIsOpen(false)
      }
      return
    }

    setIsLoading(true)
    debounceTimerRef.current = setTimeout(async () => {
      const { predictions: results, error } = await getDestinationPredictions(
        value,
        sessionRef.current
      )
      setIsLoading(false)
      if (error) {
        console.warn(`[WaypointSearchInput:${id}] Autocomplete notice:`, error)
      }
      setPredictions(results || [])
      setIsOpen(true)
    }, 300)
  }, [id, allowCurrentLocation])

  const handleSelectPrediction = useCallback(
    async (prediction) => {
      if (!prediction) return
      setIsOpen(false)
      setIsResolving(true)
      setQuery(prediction.mainText || prediction.description)

      try {
        const { place, error } = await getSelectedPlaceCoordinates(
          prediction,
          sessionRef.current
        )
        setIsResolving(false)

        if (error || !place) {
          console.warn('Coordinates resolution failed:', error)
          return
        }

        if (onSelectPlace) {
          onSelectPlace(place)
        }
      } catch (err) {
        setIsResolving(false)
        console.error('Failed to select prediction:', err)
      }
    },
    [onSelectPlace]
  )

  const handleCurrentLocationClick = useCallback(() => {
    setIsOpen(false)
    setQuery('Current Location')
    if (onSelectCurrentLocation) {
      onSelectCurrentLocation()
    }
  }, [onSelectCurrentLocation])

  // Total selectable items in dropdown list:
  // If allowCurrentLocation: item index 0 is Current Location, and item index (idx + 1) is predictions[idx]
  const totalItems = allowCurrentLocation ? predictions.length + 1 : predictions.length

  const handleKeyDown = (e) => {
    if (!isOpen || totalItems === 0) {
      if (e.key === 'ArrowDown') {
        setIsOpen(true)
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((prev) => (prev < totalItems - 1 ? prev + 1 : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((prev) => (prev > 0 ? prev - 1 : totalItems - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightedIndex >= 0) {
        if (allowCurrentLocation && highlightedIndex === 0) {
          handleCurrentLocationClick()
        } else {
          const predIndex = allowCurrentLocation ? highlightedIndex - 1 : highlightedIndex
          if (predictions[predIndex]) {
            handleSelectPrediction(predictions[predIndex])
          }
        }
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
    }
  }

  const handleClear = () => {
    setQuery('')
    setPredictions([])
    setHighlightedIndex(-1)
    if (allowCurrentLocation) {
      setIsOpen(true)
    } else {
      setIsOpen(false)
    }
    if (inputRef.current) {
      inputRef.current.focus()
    }
  }

  const hasCurrentLocOption = allowCurrentLocation && currentLocationCoords

  return (
    <div ref={wrapperRef} className="relative w-full space-y-1.5">
      {/* Label and Subhead */}
      <div className="flex items-center justify-between text-xs">
        <label
          htmlFor={id}
          className="label-subhead flex items-center gap-1.5 font-medium text-slate-300"
        >
          {iconType === 'origin' ? (
            <span className="w-2 h-2 rounded-full bg-emerald-400 ring-2 ring-emerald-400/20"></span>
          ) : (
            <span className="w-2 h-2 rounded-full bg-cockpit-teal ring-2 ring-cockpit-teal/20"></span>
          )}
          <span>{label}</span>
        </label>
        {sublabel && <span className="label-quiet text-slate-500 text-[10px]">{sublabel}</span>}
      </div>

      {/* Input Field */}
      <div className="relative group">
        <div className="relative flex items-center">
          {/* Leading Icon */}
          <div className="absolute left-3.5 pointer-events-none flex items-center justify-center">
            {isLoading || isResolving ? (
              <div className="w-4 h-4 border-2 border-cockpit-teal border-t-transparent rounded-full animate-spin" />
            ) : iconType === 'origin' ? (
              <div className="w-4 h-4 flex items-center justify-center text-emerald-400">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
                  />
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
              </div>
            ) : (
              <div className="w-4 h-4 flex items-center justify-center text-slate-400 group-focus-within:text-cockpit-teal transition-colors">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                  />
                </svg>
              </div>
            )}
          </div>

          {/* Text Input */}
          <input
            ref={inputRef}
            id={id}
            type="text"
            value={query}
            onChange={handleInputChange}
            onFocus={() => {
              if (allowCurrentLocation || (predictions.length > 0 && query.trim().length >= 2)) {
                setIsOpen(true)
              }
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            autoComplete="off"
            className={`w-full pl-10 pr-16 py-3 bg-[#080B11] border rounded-2xl text-white placeholder-slate-500 text-xs font-light tracking-wide focus:outline-none transition-all duration-200 shadow-inner ${
              isOpen
                ? 'border-cockpit-teal/50 ring-1 ring-cockpit-teal/30 bg-[#0A0E17]'
                : 'border-white/10 hover:border-white/20'
            }`}
          />

          {/* Action Buttons inside Input */}
          <div className="absolute right-2.5 flex items-center gap-1">
            {/* Quick GPS button for Origin */}
            {allowCurrentLocation && (
              <button
                type="button"
                onClick={handleCurrentLocationClick}
                title="Use Current Device Location"
                className={`p-1.5 rounded-lg transition cursor-pointer ${
                  isCurrentLocationSelected
                    ? 'text-emerald-400 bg-emerald-400/10'
                    : 'text-slate-500 hover:text-emerald-300 hover:bg-white/5'
                }`}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M12 8c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4zm8.94 3A8.994 8.994 0 0013 3.06V1h-2v2.06A8.994 8.994 0 003.06 11H1v2h2.06A8.994 8.994 0 0011 20.94V23h2v-2.06A8.994 8.994 0 0020.94 13H23v-2h-2.06z"
                  />
                </svg>
              </button>
            )}

            {/* Clear Button */}
            {query && (
              <button
                type="button"
                onClick={handleClear}
                className="p-1 text-slate-500 hover:text-slate-300 transition cursor-pointer"
                title="Clear input"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* Prediction & Option Dropdown */}
        {isOpen && (
          <div className="absolute top-full left-0 right-0 mt-2 z-50 seamless-glass rounded-2xl border border-white/10 shadow-2xl overflow-hidden backdrop-blur-2xl">
            <ul className="py-1.5 max-h-72 overflow-y-auto divide-y divide-white/[0.04]">
              {/* Option 1: Current Device Location (Always at the top for starting point) */}
              {hasCurrentLocOption && (
                <li
                  onMouseDown={(e) => {
                    e.preventDefault()
                    handleCurrentLocationClick()
                  }}
                  onMouseEnter={() => setHighlightedIndex(0)}
                  className={`px-4 py-3 flex items-start gap-3 cursor-pointer transition-colors duration-150 ${
                    highlightedIndex === 0
                      ? 'bg-cockpit-teal/15 text-white'
                      : isCurrentLocationSelected
                      ? 'bg-emerald-500/10 text-white'
                      : 'hover:bg-white/[0.04] text-slate-300'
                  }`}
                >
                  <div className="mt-0.5 text-emerald-400 flex-shrink-0">
                    <div className="relative w-4 h-4 flex items-center justify-center">
                      <span className="absolute w-full h-full rounded-full bg-emerald-400/30 animate-ping"></span>
                      <svg className="w-4 h-4 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                          d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
                        />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                          d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
                        />
                      </svg>
                    </div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-white">Current Location</span>
                      {isCurrentLocationSelected && (
                        <span className="px-1.5 py-0.5 text-[9px] font-mono tracking-wider uppercase rounded bg-emerald-400/20 text-emerald-300 border border-emerald-400/30">
                          Active
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-slate-400 truncate mt-0.5 font-mono">
                      Device GPS · {currentLocationCoords.latitude?.toFixed(4)}°N, {currentLocationCoords.longitude?.toFixed(4)}°E
                    </div>
                  </div>
                </li>
              )}

              {/* Autocomplete Predictions */}
              {predictions.map((item, idx) => {
                const itemIndex = allowCurrentLocation ? idx + 1 : idx
                const isHighlighted = itemIndex === highlightedIndex
                return (
                  <li
                    key={item.placeId || idx}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      handleSelectPrediction(item)
                    }}
                    onMouseEnter={() => setHighlightedIndex(itemIndex)}
                    className={`px-4 py-2.5 flex items-start gap-3 cursor-pointer transition-colors duration-150 ${
                      isHighlighted
                        ? 'bg-cockpit-teal/15 text-white'
                        : 'hover:bg-white/[0.04] text-slate-300'
                    }`}
                  >
                    <div className="mt-0.5 text-slate-500 flex-shrink-0">
                      <svg
                        className={`w-4 h-4 ${isHighlighted ? 'text-cockpit-teal' : 'text-slate-500'}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                          d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
                        />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                          d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
                        />
                      </svg>
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-normal text-white truncate">
                        {item.mainText}
                      </div>
                      {item.secondaryText && (
                        <div className="text-[11px] text-slate-400 truncate mt-0.5">
                          {item.secondaryText}
                        </div>
                      )}
                    </div>
                  </li>
                )
              })}

              {/* Hint when query is short / no predictions */}
              {query.trim().length < 2 && !allowCurrentLocation && (
                <div className="px-4 py-3 text-center text-xs text-slate-500">
                  Type at least 2 characters to search places...
                </div>
              )}

              {query.trim().length >= 2 && predictions.length === 0 && !isLoading && (
                <div className="px-4 py-3 text-center text-xs text-slate-400">
                  No matching places found. Try another landmark or city.
                </div>
              )}
            </ul>

            {/* Map Selection Footer Option */}
            {onFallbackToMap && (
              <div className="p-2 border-t border-white/5 bg-white/[0.02]">
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    setIsOpen(false)
                    onFallbackToMap()
                  }}
                  className="w-full py-2 px-3 rounded-xl hover:bg-white/5 text-slate-300 hover:text-cockpit-teal text-xs flex items-center justify-center gap-2 transition cursor-pointer"
                >
                  <span>⚑</span>
                  <span>Select {iconType === 'origin' ? 'starting point' : 'destination'} directly on map</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Map selection indicator */}
      {isSelectingOnMap && (
        <div className="flex items-center gap-2 text-amber-300 text-[11px] font-medium animate-pulse pt-0.5">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
          <span>Click anywhere on the map to set {iconType === 'origin' ? 'starting point' : 'destination'}</span>
        </div>
      )}
    </div>
  )
}

/**
 * DestinationSearch Component
 * 
 * Supports both Starting Point (Origin) and Destination selection:
 * - Starting point defaults to Device / Current Location
 * - Dropdown provides "📍 Current Location" at the top of the list
 * - Autocomplete search for both Starting Point and Destination
 * - Map pin fallback for both
 * - Quick waypoint swap
 */
export default function DestinationSearch({
  onDestinationSelect,
  onFallbackToMap,
  isSelectingOnMap = false,
  selectedDestination = null,
}) {
  const {
    trip,
    origin,
    setOrigin,
    isSelectingOrigin,
    startSelectingOrigin,
    cancelSelectingOrigin,
    destination,
    setDestination,
    isSelectingDestination,
    startSelectingDestination,
    cancelSelectingDestination,
    swapOriginAndDestination,
    planJourneyAction,
    isPlanning,
  } = useTrip()

  const { telemetry } = useTelemetry()

  const [apiStatus, setApiStatus] = useState('initializing')
  const [statusMessage, setStatusMessage] = useState('')

  // Load Google Places SDK once
  useEffect(() => {
    let isMounted = true
    const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || ''

    if (!apiKey || apiKey.trim() === '' || apiKey.includes('your_google_maps_api_key')) {
      if (isMounted) {
        setApiStatus('missing_key')
        setStatusMessage('Google Places API key is not configured.')
      }
      return
    }

    loadGooglePlacesApi(apiKey)
      .then((res) => {
        if (!isMounted) return
        if (res.success) {
          setApiStatus('ready')
          setStatusMessage('')
        } else {
          setApiStatus(res.reason === 'missing_key' ? 'missing_key' : 'error')
          setStatusMessage(res.message || 'Google Places could not be loaded.')
        }
      })
      .catch((err) => {
        if (!isMounted) return
        setApiStatus('error')
        setStatusMessage(err?.message || 'Failed to initialize Google Maps Platform.')
      })

    return () => {
      isMounted = false
    }
  }, [])

  const currentCoords = {
    latitude: Number(telemetry.latitude) || 12.2958,
    longitude: Number(telemetry.longitude) || 76.6394,
  }

  const originDisplayText = origin?.isCurrentLocation
    ? 'Current Location'
    : (origin?.name || '')

  const destDisplayText = destination?.name || selectedDestination?.name || ''

  return (
    <div className="relative w-full space-y-4">
      {/* Route Planner Card */}
      <div className="seamless-glass p-5 rounded-3xl border border-white/10 shadow-2xl space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between pb-2 border-b border-white/5">
          <div>
            <h2 className="text-sm font-medium text-white tracking-tight flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-cockpit-teal"></span>
              Plan Journey
            </h2>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Choose your starting point and destination to find optimal charging stops
            </p>
          </div>

          {/* Waypoint Swap Button */}
          {destination && (
            <button
              type="button"
              onClick={swapOriginAndDestination}
              title="Swap Starting Point and Destination"
              className="p-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 hover:text-cockpit-teal border border-white/5 transition flex items-center gap-1.5 text-xs cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
              </svg>
              <span className="text-[11px] font-medium hidden sm:inline">Swap</span>
            </button>
          )}
        </div>

        {/* Inputs with Connecting Visual Line */}
        <div className="space-y-4">
          {/* Starting Point Search Input */}
          <WaypointSearchInput
            id="origin-search-input"
            label="Starting Point"
            sublabel="Origin"
            placeholder="Search starting place or use Current Location..."
            valueText={originDisplayText}
            iconType="origin"
            allowCurrentLocation={true}
            isCurrentLocationSelected={!!origin?.isCurrentLocation}
            currentLocationCoords={currentCoords}
            onSelectPlace={(place) => setOrigin(place)}
            onSelectCurrentLocation={() => setOrigin({ isCurrentLocation: true })}
            onFallbackToMap={() => {
              if (isSelectingOrigin) {
                cancelSelectingOrigin()
              } else {
                startSelectingOrigin()
              }
            }}
            isSelectingOnMap={isSelectingOrigin}
            apiStatus={apiStatus}
          />

          {/* Destination Search Input */}
          <WaypointSearchInput
            id="destination-search-input"
            label="Destination"
            sublabel="Target"
            placeholder="Where do you want to go? (e.g. Kempegowda Airport, Mysore...)"
            valueText={destDisplayText}
            iconType="destination"
            allowCurrentLocation={false}
            onSelectPlace={(place) => {
              if (onDestinationSelect) onDestinationSelect(place)
              else setDestination(place)
            }}
            onFallbackToMap={() => {
              if (onFallbackToMap) {
                onFallbackToMap()
              } else {
                if (isSelectingDestination) {
                  cancelSelectingDestination()
                } else {
                  startSelectingDestination()
                }
              }
            }}
            isSelectingOnMap={isSelectingOnMap || isSelectingDestination}
            apiStatus={apiStatus}
          />
        </div>

        {/* API Notice / Fallback Message (Non-Crashing) */}
        {apiStatus === 'missing_key' && (
          <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs text-amber-200 flex items-start gap-2.5">
            <span className="text-amber-400 text-sm mt-0.5">ⓘ</span>
            <div className="space-y-1">
              <p className="font-medium text-amber-100">
                Google Places API key is not configured.
              </p>
              <p className="text-[11px] text-amber-200/80 leading-relaxed">
                Add <code className="font-mono text-amber-300">VITE_GOOGLE_MAPS_API_KEY</code> to your environment file, or select your waypoints directly on the map.
              </p>
            </div>
          </div>
        )}

        {apiStatus === 'error' && (
          <div className="p-3 rounded-xl bg-rose-950/20 border border-rose-800/30 text-xs text-rose-300 flex items-center justify-between">
            <span>{statusMessage || 'Google Places service unavailable.'}</span>
            <button
              type="button"
              onClick={() => startSelectingDestination()}
              className="text-[11px] underline hover:text-white cursor-pointer"
            >
              Select on map
            </button>
          </div>
        )}

        {/* Plan Journey Action Button when destination is set */}
        {destination && (
          <div className="pt-2">
            <button
              type="button"
              id="plan-journey-route-button"
              onClick={() => planJourneyAction()}
              disabled={isPlanning}
              className="btn-primary w-full py-3.5 text-xs flex items-center justify-center gap-2 cursor-pointer shadow-lg shadow-teal-500/10 font-medium"
            >
              {isPlanning ? (
                <>
                  <div className="w-4 h-4 border-2 border-slate-900 border-t-transparent rounded-full animate-spin" />
                  <span>Computing Optimal Route & Charging Stops...</span>
                </>
              ) : (
                <>
                  <span>Plan Journey Route</span>
                  <span className="text-sm font-normal">→</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
