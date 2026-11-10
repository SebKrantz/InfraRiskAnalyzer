import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
// Bundled with the app rather than loaded from a CDN: no third-party request on page load.
import 'maplibre-gl/dist/maplibre-gl.css'
import { Hazard, UploadedFile, AnalysisResult, ColorPalette, Basemap } from '../types'
import { API_BASE_URL } from '../services/api'
import { Select } from './ui/select'
import { formatFeatureProperties } from '../lib/featurePopup'

interface MapViewProps {
  uploadedFile: UploadedFile | null
  selectedHazard: Hazard | null
  analysisResult: AnalysisResult | null
  colorPalette: ColorPalette
  hazardOpacity: number
  basemap: Basemap
  onBasemapChange: (basemap: Basemap) => void
  vulnerabilityAnalysisEnabled?: boolean
  hazardStats?: { min: number; max: number } | null
}

// CARTO's raster basemaps now require an API key. In production the backend
// substitutes it into index.html (window.__CARTO_API_KEY__); `vite dev` leaves the
// placeholder untouched, so fall back to VITE_CARTO_API_KEY there. The fallback is
// dev-only so a key in frontend/.env.local is never baked into a committed build.
// With no key the tiles are requested unkeyed, exactly as before.
const injectedCartoKey = window.__CARTO_API_KEY__
const CARTO_API_KEY =
  injectedCartoKey && injectedCartoKey !== '__CARTO_API_KEY__'
    ? injectedCartoKey
    : import.meta.env.DEV ? import.meta.env.VITE_CARTO_API_KEY ?? '' : ''

const cartoTiles = (style: string): string[] => [
  `https://basemaps.cartocdn.com/rastertiles/${style}/{z}/{x}/{y}.png` +
    (CARTO_API_KEY ? `?key=${encodeURIComponent(CARTO_API_KEY)}` : '')
]

const basemapStyles: Record<Basemap, any> = {
  positron: {
    version: 8,
    sources: {
      'carto-positron': {
        type: 'raster',
        tiles: cartoTiles('light_all'),
        tileSize: 256,
        attribution: '© OpenStreetMap © CARTO'
      }
    },
    layers: [
      {
        id: 'carto-positron-layer',
        type: 'raster',
        source: 'carto-positron',
        minzoom: 0,
        maxzoom: 22
      }
    ]
  },
  'dark-matter': {
    version: 8,
    sources: {
      'carto-dark-matter': {
        type: 'raster',
        tiles: cartoTiles('dark_all'),
        tileSize: 256,
        attribution: '© OpenStreetMap © CARTO'
      }
    },
    layers: [
      {
        id: 'carto-dark-matter-layer',
        type: 'raster',
        source: 'carto-dark-matter',
        minzoom: 0,
        maxzoom: 22
      }
    ]
  },
  osm: {
    version: 8,
    sources: {
      'osm': {
        type: 'raster',
        tiles: [
          'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
        ],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors'
      }
    },
    layers: [
      {
        id: 'osm-layer',
        type: 'raster',
        source: 'osm',
        minzoom: 0,
        maxzoom: 19
      }
    ]
  },
  topo: {
    version: 8,
    sources: {
      'opentopomap': {
        type: 'raster',
        tiles: [
          'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
          'https://b.tile.opentopomap.org/{z}/{x}/{y}.png',
          'https://c.tile.opentopomap.org/{z}/{x}/{y}.png'
        ],
        tileSize: 256,
        attribution: '© OpenTopoMap contributors'
      }
    },
    layers: [
      {
        id: 'opentopomap-layer',
        type: 'raster',
        source: 'opentopomap',
        minzoom: 0,
        maxzoom: 17
      }
    ]
  },
  'esri-street': {
    version: 8,
    sources: {
      'esri-street': {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '© Esri'
      }
    },
    layers: [
      {
        id: 'esri-street-layer',
        type: 'raster',
        source: 'esri-street',
        minzoom: 0,
        maxzoom: 16
      }
    ]
  },
  'esri-topo': {
    version: 8,
    sources: {
      'esri-topo': {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '© Esri'
      }
    },
    layers: [
      {
        id: 'esri-topo-layer',
        type: 'raster',
        source: 'esri-topo',
        minzoom: 0,
        maxzoom: 16
      }
    ]
  },
  'esri-terrain': {
    version: 8,
    sources: {
      'esri-terrain': {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '© Esri'
      }
    },
    layers: [
      {
        id: 'esri-terrain-layer',
        type: 'raster',
        source: 'esri-terrain',
        minzoom: 0,
        maxzoom: 13
      }
    ]
  },
  'esri-ocean': {
    version: 8,
    sources: {
      'esri-ocean': {
        type: 'raster',
        tiles: [
          'https://services.arcgisonline.com/arcgis/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '© Esri'
      }
    },
    layers: [
      {
        id: 'esri-ocean-layer',
        type: 'raster',
        source: 'esri-ocean',
        minzoom: 0,
        maxzoom: 16
      }
    ]
  },
  'esri-imagery': {
    version: 8,
    sources: {
      'esri-imagery': {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '© Esri'
      }
    },
    layers: [
      {
        id: 'esri-imagery-layer',
        type: 'raster',
        source: 'esri-imagery',
        minzoom: 0,
        maxzoom: 19
      }
    ]
  },
}

const COLORMAP_STOPS: Record<ColorPalette, string[]> = {
  viridis: ['#440154', '#472777', '#3e4989', '#30678d', '#25828e', '#1e9d88', '#35b778', '#6dce58', '#b5dd2b', '#fde724'],
  magma: ['#000003', '#170f3c', '#430f75', '#711f81', '#9e2e7e', '#cd3f70', '#f0605d', '#fd9567', '#fec98d', '#fbfcbf'],
  inferno: ['#000003', '#1a0b40', '#4a0b6a', '#781c6d', '#a42c60', '#cf4446', '#ed6825', '#fb9b06', '#f7d13c', '#fcfea4'],
  plasma: ['#0c0786', '#45039e', '#7200a8', '#9b179e', '#bc3685', '#d7576b', '#ec7853', '#fa9f3a', '#fcc926', '#eff821'],
  cividis: ['#00224d', '#11356f', '#3a486b', '#575d6d', '#6f7073', '#898678', '#a59b73', '#c3b368', '#e1cc54', '#fde737'],
  turbo: ['#30123b', '#4560d6', '#36a8f9', '#1ae4b6', '#71fd5f', '#c8ee33', '#f9ba38', '#f56817', '#c92903', '#7a0402'],
}

export default function MapView({
  uploadedFile,
  selectedHazard,
  analysisResult,
  colorPalette,
  hazardOpacity,
  basemap,
  onBasemapChange,
  vulnerabilityAnalysisEnabled = false,
  hazardStats = null,
}: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const [mapLoaded, setMapLoaded] = useState(false)
  const isRestoringLayers = useRef(false)
  const popup = useRef<maplibregl.Popup | null>(null)
  
  // Layer visibility state (basemap always on)
  const [infrastructureVisible, setInfrastructureVisible] = useState(true)
  const [hazardVisible, setHazardVisible] = useState(true)

  // Observed max vulnerability across all features (0-1 scale)
  const maxVulnerability = useMemo(() => {
    if (!vulnerabilityAnalysisEnabled || !analysisResult?.infrastructure_features?.features) return 1
    let max = 0
    for (const f of analysisResult.infrastructure_features.features) {
      const v = f.properties?.vulnerability ?? 0
      if (v > max) max = v
    }
    return max || 1
  }, [vulnerabilityAnalysisEnabled, analysisResult])
  
  // Store current prop values for restoration
  const currentProps = useRef({ uploadedFile, selectedHazard, analysisResult, hazardOpacity, colorPalette, vulnerabilityAnalysisEnabled, maxVulnerability })
  
  // Track bounds to prevent unnecessary reloads
  const lastBounds = useRef<string | null>(null)
  const boundsSetForFile = useRef<string | null>(null)
  // One timer per job: the hazard-layer debounce and the zoom-to-data delay used to
  // share one, so each effect's cleanup could cancel the other's.
  const hazardTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fitBoundsTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // True once the current style can take sources and layers: from 'load' / 'style.load'
  // until the next setStyle. map.isStyleLoaded() is no use for this, because it stays
  // false while any tile is still loading, so updates queued behind it piled up.
  const styleReady = useRef(false)
  // The listeners added for each infrastructure layer, so they can be removed again:
  // map.off() without the listener removes nothing.
  const popupHandlers = useRef<Record<string, { click: any; enter: any; leave: any }>>({})
  
  // Update refs when props change
  useEffect(() => {
    currentProps.current = { uploadedFile, selectedHazard, analysisResult, hazardOpacity, colorPalette, vulnerabilityAnalysisEnabled, maxVulnerability }
  }, [uploadedFile, selectedHazard, analysisResult, hazardOpacity, colorPalette, vulnerabilityAnalysisEnabled, maxVulnerability])

  useEffect(() => {
    if (!mapContainer.current || map.current) return

    // Ensure container has dimensions
    if (!mapContainer.current.offsetWidth || !mapContainer.current.offsetHeight) {
      console.warn('Map container has no dimensions')
    }

    // Initialize map with CartoDB Positron as default
    try {
      map.current = new maplibregl.Map({
        container: mapContainer.current,
        style: basemapStyles.positron,
        center: [0, 20],
        zoom: 2,
        attributionControl: false,
      })

      map.current.addControl(
        new maplibregl.AttributionControl({
          compact: true,
          customAttribution:
            'Created by <a href="https://sebastiankrantz.com/" target="_blank" rel="noopener noreferrer">Sebastian Krantz</a>, funded by the World Bank',
        }),
        'bottom-right'
      )

      map.current.on('load', () => {
        styleReady.current = true
        setMapLoaded(true)
      })

      map.current.on('error', (e: any) => {
        console.error('Map error:', e.error || e)
      })

      map.current.on('style.load', () => {
        styleReady.current = true
        setMapLoaded(true)
      })

      map.current.on('style.error', (e: any) => {
        console.error('Map style error:', e)
      })

    } catch (error) {
      console.error('Error initializing map:', error)
    }

    return () => {
      // Clean up popup
      if (popup.current) {
        popup.current.remove()
        popup.current = null
      }
      
      // Clean up timers
      if (hazardTimer.current) clearTimeout(hazardTimer.current)
      if (fitBoundsTimer.current) clearTimeout(fitBoundsTimer.current)
      
      if (map.current) {
        map.current.remove()
        map.current = null
      }
    }
  }, [])

  // Deterministic layer helpers for toggling
  const ensureStyleLoaded = (cb: () => void) => {
    const tryRun = () => {
      if (!map.current) return
      if (styleReady.current) cb()
      else setTimeout(tryRun, 50)
    }
    tryRun()
  }

  const removeHazard = () => {
    if (!map.current) return
    const hazardLayerId = 'hazard-raster-layer'
    const hazardSourceId = 'hazard-raster-source'
    if (map.current.getLayer(hazardLayerId)) map.current.removeLayer(hazardLayerId)
    if (map.current.getSource(hazardSourceId)) map.current.removeSource(hazardSourceId)
  }

  const addHazard = () => {
    if (!map.current) return
    const props = currentProps.current
    const selHazard = props.selectedHazard
    if (!selHazard) return
    const hazardLayerId = 'hazard-raster-layer'
    const hazardSourceId = 'hazard-raster-source'
    const tileUrl = `${API_BASE_URL}/tiles/${selHazard.id}/{z}/{x}/{y}.png?palette=${props.colorPalette}`
    removeHazard()
    try {
      map.current.addSource(hazardSourceId, { type: 'raster', tiles: [tileUrl], tileSize: 256 })
      const beforeLayer = map.current.getLayer('infrastructure-layer') ? 'infrastructure-layer' : undefined
      map.current.addLayer({ id: hazardLayerId, type: 'raster', source: hazardSourceId, paint: { 'raster-opacity': props.hazardOpacity / 100 } }, beforeLayer)
      map.current.setLayoutProperty(hazardLayerId, 'visibility', hazardVisible ? 'visible' : 'none')
    } catch (e) { console.error('addHazard error:', e) }
  }

  // Add popup click handlers to infrastructure layers
  const addPopupHandlers = (layerId: string) => {
    if (!map.current) return
    removePopupHandlers(layerId)

    const handleClick = (e: maplibregl.MapLayerMouseEvent) => {
      if (!map.current || !e.features || e.features.length === 0) return
      
      const feature = e.features[0]
      const properties = feature.properties || {}
      const coordinates = (e.lngLat as any).toArray()
      
      // Close existing popup if any
      if (popup.current) {
        popup.current.remove()
      }
      
      // Create new popup
      popup.current = new maplibregl.Popup({
        closeButton: true,
        closeOnClick: true,
        maxWidth: '300px',
      })
        .setLngLat(coordinates)
        .setHTML(formatFeatureProperties(
          properties,
          !!currentProps.current.vulnerabilityAnalysisEnabled,
          currentProps.current.selectedHazard?.unit,
        ))
        .addTo(map.current)
    }

    // Change cursor on hover
    const handleEnter = () => {
      if (map.current) map.current.getCanvas().style.cursor = 'pointer'
    }
    const handleLeave = () => {
      if (map.current) map.current.getCanvas().style.cursor = ''
    }

    // Use type assertion for layer-specific event handlers
    ;(map.current.on as any)('click', layerId, handleClick)
    ;(map.current.on as any)('mouseenter', layerId, handleEnter)
    ;(map.current.on as any)('mouseleave', layerId, handleLeave)
    popupHandlers.current[layerId] = { click: handleClick, enter: handleEnter, leave: handleLeave }
  }

  // Remove popup click handlers from infrastructure layers
  const removePopupHandlers = (layerId: string) => {
    const handlers = popupHandlers.current[layerId]
    if (!map.current || !handlers) return
    ;(map.current.off as any)('click', layerId, handlers.click)
    ;(map.current.off as any)('mouseenter', layerId, handlers.enter)
    ;(map.current.off as any)('mouseleave', layerId, handlers.leave)
    delete popupHandlers.current[layerId]
  }

  const removeInfrastructure = () => {
    if (!map.current) return
    const sourceId = 'infrastructure-source'
    const layerId = 'infrastructure-layer'
    const affectedLayerId = 'infrastructure-affected'
    
    // Remove popup handlers before removing layers
    removePopupHandlers(layerId)
    removePopupHandlers(affectedLayerId)
    
    // Close popup if open
    if (popup.current) {
      popup.current.remove()
      popup.current = null
    }
    
    if (map.current.getLayer(affectedLayerId)) map.current.removeLayer(affectedLayerId)
    if (map.current.getLayer(layerId)) map.current.removeLayer(layerId)
    if (map.current.getSource(sourceId)) map.current.removeSource(sourceId)
  }

  const getInfraStyle = (isVulnerabilityMode: boolean, maxVuln = 1): { unaffectedColor: any; affectedColor: any; unaffectedFilter: any; affectedFilter: any } => {
    const sqrtMax = Math.sqrt(maxVuln)
    return {
      unaffectedColor: isVulnerabilityMode ? '#6b7280' : '#10b981',
      affectedColor: isVulnerabilityMode
        ? ['interpolate', ['linear'],
            ['sqrt', ['coalesce', ['get', 'vulnerability'], 0]],
            0, '#10b981',
            sqrtMax * 0.5, '#f59e0b',
            sqrtMax, '#ef4444']
        : '#ef4444',
      unaffectedFilter: isVulnerabilityMode
        ? ['boolean', false]
        : ['==', ['get', 'affected'], false],
      affectedFilter: isVulnerabilityMode
        ? ['boolean', true]
        : ['==', ['get', 'affected'], true],
    }
  }

  const addInfrastructure = () => {
    if (!map.current) return
    const props = currentProps.current
    const file = props.uploadedFile
    if (!file) return
    const sourceId = 'infrastructure-source'
    const layerId = 'infrastructure-layer'
    const affectedLayerId = 'infrastructure-affected'
    const geoJson: any = props.analysisResult?.infrastructure_features || file.geojson
    const isPoint = (props.analysisResult?.geometry_type || file.geometry_type) === 'Point'
    const hasAffected = !!props.analysisResult?.infrastructure_features
    const isVulnMode = !!props.vulnerabilityAnalysisEnabled && hasAffected
    const style = getInfraStyle(isVulnMode, props.maxVulnerability)
    if (!geoJson) return
    removeInfrastructure()
    try {
      map.current.addSource(sourceId, { type: 'geojson', data: geoJson })
      if (hasAffected) {
        const needsGradientLayer = isVulnMode
          || (isPoint ? (props.analysisResult?.summary?.affected_count || 0) > 0 : (props.analysisResult?.summary?.affected_meters || 0) > 0)
        // Unaffected first, affected on top, as the infrastructure effect below draws them
        if (isPoint) {
          map.current.addLayer({ id: layerId, type: 'circle', source: sourceId, filter: style.unaffectedFilter, paint: { 'circle-radius': 5, 'circle-color': style.unaffectedColor, 'circle-opacity': 0.8 } })
          map.current.moveLayer(layerId)
          if (needsGradientLayer) {
            map.current.addLayer({ id: affectedLayerId, type: 'circle', source: sourceId, filter: style.affectedFilter, paint: { 'circle-radius': 5, 'circle-color': style.affectedColor, 'circle-opacity': 0.8 } })
            map.current.moveLayer(affectedLayerId)
          }
        } else {
          map.current.addLayer({ id: layerId, type: 'line', source: sourceId, filter: style.unaffectedFilter, paint: { 'line-color': style.unaffectedColor, 'line-width': 3, 'line-opacity': 0.8 } })
          map.current.moveLayer(layerId)
          if (needsGradientLayer) {
            map.current.addLayer({ id: affectedLayerId, type: 'line', source: sourceId, filter: style.affectedFilter, paint: { 'line-color': style.affectedColor, 'line-width': 3, 'line-opacity': 0.8 } })
            map.current.moveLayer(affectedLayerId)
          }
        }
      } else {
        if (isPoint) map.current.addLayer({ id: layerId, type: 'circle', source: sourceId, paint: { 'circle-radius': 5, 'circle-color': '#6b7280', 'circle-opacity': 0.8 } })
        else map.current.addLayer({ id: layerId, type: 'line', source: sourceId, paint: { 'line-color': '#6b7280', 'line-width': 3, 'line-opacity': 0.8 } })
        // Move infrastructure layer to top to ensure it's above hazard
        map.current.moveLayer(layerId)
      }
      map.current.setLayoutProperty(layerId, 'visibility', infrastructureVisible ? 'visible' : 'none')
      if (map.current.getLayer(affectedLayerId)) map.current.setLayoutProperty(affectedLayerId, 'visibility', infrastructureVisible ? 'visible' : 'none')
      
      // Add popup handlers to both layers
      addPopupHandlers(layerId)
      if (map.current.getLayer(affectedLayerId)) {
        addPopupHandlers(affectedLayerId)
      }
    } catch (e) { console.error('addInfrastructure error:', e) }
  }

  // Reset bounds tracking when file changes
  const uploadedFileId = uploadedFile?.file_id
  useEffect(() => {
    if (uploadedFileId) {
      // Reset bounds tracking when a new file is uploaded
      if (boundsSetForFile.current !== uploadedFileId) {
        lastBounds.current = null
      }
    } else {
      // Clear tracking when file is removed
      boundsSetForFile.current = null
      lastBounds.current = null
    }
  }, [uploadedFileId])

  // Add/update hazard raster layer
  useEffect(() => {
    // Skip if we're restoring layers after basemap change
    if (isRestoringLayers.current) return
    
    if (!map.current || !mapLoaded || !selectedHazard) {
      // Remove hazard layer if hazard is deselected
      if (map.current) {
        const hazardLayerId = 'hazard-raster-layer'
        const hazardSourceId = 'hazard-raster-source'
        if (map.current.getLayer(hazardLayerId)) {
          map.current.removeLayer(hazardLayerId)
        }
        if (map.current.getSource(hazardSourceId)) {
          map.current.removeSource(hazardSourceId)
        }
      }
      return
    }

    const hazardSourceId = 'hazard-raster-source'
    const hazardLayerId = 'hazard-raster-layer'

    // Debounce hazard layer updates to prevent rapid successive reloads. A wait still
    // pending when the effect runs again is dropped, so an older update can never land
    // after a newer one.
    let cancelled = false
    if (hazardTimer.current) {
      clearTimeout(hazardTimer.current)
    }

    hazardTimer.current = setTimeout(() => {
      const waitForStyleAndAddHazard = () => {
        if (cancelled || !map.current) return
        if (!styleReady.current || isRestoringLayers.current) {
          setTimeout(waitForStyleAndAddHazard, 100)
          return
        }

        // Check if layer already exists - if so, just update it
        const existingLayer = map.current.getLayer(hazardLayerId)
        const existingSource = map.current.getSource(hazardSourceId)
        
        if (existingLayer && existingSource) {
          // Update existing layer properties (opacity and tile URL if palette changed)
          try {
            map.current.setPaintProperty(hazardLayerId, 'raster-opacity', hazardOpacity / 100)
            
            // Update source if palette changed (remove and re-add source)
            const currentTiles = (existingSource as any).tiles
            const newTileUrl = `${API_BASE_URL}/tiles/${selectedHazard.id}/{z}/{x}/{y}.png?palette=${colorPalette}`
            if (currentTiles && currentTiles[0] !== newTileUrl) {
              map.current.removeLayer(hazardLayerId)
              map.current.removeSource(hazardSourceId)
              
              map.current.addSource(hazardSourceId, {
                type: 'raster',
                tiles: [newTileUrl],
                tileSize: 256,
              })
              
              const beforeLayer = map.current.getLayer('infrastructure-layer') ? 'infrastructure-layer' : undefined
              map.current.addLayer({
                id: hazardLayerId,
                type: 'raster',
                source: hazardSourceId,
                paint: {
                  'raster-opacity': hazardOpacity / 100,
                },
              }, beforeLayer)
              
              // Apply visibility state
              if (!hazardVisible) {
                map.current.setLayoutProperty(hazardLayerId, 'visibility', 'none')
              }
            } else {
              // Just update visibility if layer exists
              if (!hazardVisible) {
                map.current.setLayoutProperty(hazardLayerId, 'visibility', 'none')
              } else {
                map.current.setLayoutProperty(hazardLayerId, 'visibility', 'visible')
              }
            }
            return
          } catch (error) {
            console.error('Error updating hazard layer:', error)
            // Fall through to add new layer
          }
        }

        // Remove existing hazard layer if updating
        if (map.current.getLayer(hazardLayerId)) {
          map.current.removeLayer(hazardLayerId)
        }
        if (map.current.getSource(hazardSourceId)) {
          map.current.removeSource(hazardSourceId)
        }

        // Add hazard raster layer using backend tile service
        try {
          // Use backend tile endpoint
          const tileUrl = `${API_BASE_URL}/tiles/${selectedHazard.id}/{z}/{x}/{y}.png?palette=${colorPalette}`
          
          map.current.addSource(hazardSourceId, {
            type: 'raster',
            tiles: [tileUrl],
            tileSize: 256,
          })

          // Add hazard layer - it should be below infrastructure
          // We'll ensure infrastructure is moved above it after adding
          map.current.addLayer({
            id: hazardLayerId,
            type: 'raster',
            source: hazardSourceId,
            paint: {
              'raster-opacity': hazardOpacity / 100,
            },
          })
          
          // If infrastructure layers exist, ensure they're above the hazard layer
          // Move them in the correct order: infrastructure (green) first, then affected (red) on top
          const infrastructureLayer = map.current.getLayer('infrastructure-layer')
          const affectedLayer = map.current.getLayer('infrastructure-affected')
          if (infrastructureLayer) {
            // Move infrastructure layer (green/unaffected) to top first
            map.current.moveLayer('infrastructure-layer')
          }
          if (affectedLayer) {
            // Move affected layer (red) to top last, so it renders above infrastructure
            map.current.moveLayer('infrastructure-affected')
          }
          
          // Apply visibility state
          if (!hazardVisible) {
            map.current.setLayoutProperty(hazardLayerId, 'visibility', 'none')
          }
        } catch (error) {
          console.error('Error adding hazard layer:', error)
        }
      }

      waitForStyleAndAddHazard()
    }, 100) // Debounce delay

    // Cleanup function
    return () => {
      cancelled = true
      if (hazardTimer.current) {
        clearTimeout(hazardTimer.current)
      }
    }
  }, [selectedHazard, hazardOpacity, colorPalette, mapLoaded, hazardVisible])

  // Add/update infrastructure layer with affected/unaffected styling
  useEffect(() => {
    // Skip if we're restoring layers after basemap change
    if (isRestoringLayers.current) return
    
    if (!map.current || !mapLoaded || !uploadedFile) {
      // Remove infrastructure layers if file is removed
      if (map.current) {
        const sourceId = 'infrastructure-source'
        const layerId = 'infrastructure-layer'
        const affectedLayerId = 'infrastructure-affected'
        
        // Remove popup handlers
        removePopupHandlers(layerId)
        removePopupHandlers(affectedLayerId)
        
        // Close popup if open
        if (popup.current) {
          popup.current.remove()
          popup.current = null
        }
        
        if (map.current.getLayer(affectedLayerId)) {
          map.current.removeLayer(affectedLayerId)
        }
        if (map.current.getLayer(layerId)) {
          map.current.removeLayer(layerId)
        }
        if (map.current.getSource(sourceId)) {
          map.current.removeSource(sourceId)
        }
      }
      return
    }

    const sourceId = 'infrastructure-source'
    const layerId = 'infrastructure-layer'
    const affectedLayerId = 'infrastructure-affected'

    // As for the hazard layer: a wait still pending when the effect runs again is
    // dropped, so the map cannot end up showing an older result than the chart.
    let cancelled = false
    const waitForStyle = () => {
      if (cancelled || !map.current) return
      if (!styleReady.current || isRestoringLayers.current) {
        setTimeout(waitForStyle, 100)
        return
      }

      // Check if we need to update or just add
      const existingSource = map.current.getSource(sourceId)
      const existingLayer = map.current.getLayer(layerId)
      const existingAffectedLayer = map.current.getLayer(affectedLayerId)
      
      // Determine which GeoJSON to use
      let geoJson = null
      let isPoint = false
      let needsUpdate = false
      
      if (analysisResult?.infrastructure_features) {
        // Use analysis result if available (has affected status)
        geoJson = analysisResult.infrastructure_features
        isPoint = analysisResult.geometry_type === 'Point'
        
        // Always update if we don't have the affected layer (switching from plain to affected/unaffected styling)
        // This is critical - we need to show affected/unaffected coloring, not plain styling
        if (existingLayer && !existingAffectedLayer) {
          needsUpdate = true // Force update to show affected/unaffected coloring
        } else if (!existingLayer) {
          // No layer exists yet, need to create it
          needsUpdate = true
        } else {
          // Even if layers exist, check if source data needs updating
          if (existingSource && (existingSource as any)._data) {
            const currentData = JSON.stringify((existingSource as any)._data)
            const newData = JSON.stringify(geoJson)
            needsUpdate = currentData !== newData
            // If data is the same but we have analysis results, we still need to update
            // because the layer styling might need to change
            if (!needsUpdate && !existingAffectedLayer) {
              needsUpdate = true
            }
          } else {
            needsUpdate = true
          }
        }
      } else if (uploadedFile.geojson) {
        // Use uploaded file GeoJSON for initial display
        geoJson = uploadedFile.geojson
        isPoint = uploadedFile.geometry_type === 'Point'
        
        // Check if source data needs updating
        if (existingSource && (existingSource as any)._data) {
          const currentData = JSON.stringify((existingSource as any)._data)
          const newData = JSON.stringify(geoJson)
          needsUpdate = currentData !== newData
        } else {
          needsUpdate = true
        }
        
        // Always update if we're switching from analysis results to plain styling
        if (!needsUpdate && existingAffectedLayer) {
          needsUpdate = true // Switching from affected/unaffected to plain styling
        }
      }

      // If source exists and data hasn't changed and we're not switching between analysis/plain modes, no update needed
      // BUT: always update if we have analysis results and don't have the affected layer yet
      if (existingSource && existingLayer && !needsUpdate) {
        // Double-check: if we have analysis results but no affected layer, we need to update
        if (analysisResult?.infrastructure_features && !existingAffectedLayer) {
          needsUpdate = true
        } else if (!analysisResult?.infrastructure_features && existingAffectedLayer) {
          // Switching from analysis results back to plain - need to update
          needsUpdate = true
        } else {
          return // No update needed
        }
      }

      // Remove existing layers only if we're updating
      if (needsUpdate && geoJson) {
        // Remove popup handlers before removing layers
        removePopupHandlers(layerId)
        removePopupHandlers(affectedLayerId)
        
        // Close popup if open
        if (popup.current) {
          popup.current.remove()
          popup.current = null
        }
        
        if (map.current.getLayer(affectedLayerId)) {
          map.current.removeLayer(affectedLayerId)
        }
        if (map.current.getLayer(layerId)) {
          map.current.removeLayer(layerId)
        }
        if (map.current.getSource(sourceId)) {
          map.current.removeSource(sourceId)
        }
      } else if (!geoJson) {
        // No GeoJSON available
        return
      }

      if (geoJson && needsUpdate) {
        try {
          // Add source (we already removed it above if it existed)
          map.current.addSource(sourceId, {
            type: 'geojson',
            data: geoJson,
          })

          // If we have analysis results, show affected/unaffected coloring
          if (analysisResult?.infrastructure_features) {
            const mainStyle = getInfraStyle(vulnerabilityAnalysisEnabled, maxVulnerability)
            const needsGradientLayer = vulnerabilityAnalysisEnabled
              || (isPoint
                ? (analysisResult.summary.affected_count || 0) > 0
                : (analysisResult.summary.affected_meters || 0) > 0)
            if (isPoint) {
              map.current.addLayer({
                id: layerId,
                type: 'circle',
                source: sourceId,
                filter: mainStyle.unaffectedFilter,
                paint: {
                  'circle-radius': 5,
                  'circle-color': mainStyle.unaffectedColor,
                  'circle-opacity': 0.8,
                },
              })
              if (!infrastructureVisible) {
                map.current.setLayoutProperty(layerId, 'visibility', 'none')
              }
            } else {
              map.current.addLayer({
                id: layerId,
                type: 'line',
                source: sourceId,
                filter: mainStyle.unaffectedFilter,
                paint: {
                  'line-color': mainStyle.unaffectedColor,
                  'line-width': 3,
                  'line-opacity': 0.8,
                },
              })
              if (!infrastructureVisible) {
                map.current.setLayoutProperty(layerId, 'visibility', 'none')
              }
            }

            if (needsGradientLayer) {
              if (isPoint) {
                map.current.addLayer({
                  id: affectedLayerId,
                  type: 'circle',
                  source: sourceId,
                  filter: mainStyle.affectedFilter,
                  paint: {
                    'circle-radius': 5,
                    'circle-color': mainStyle.affectedColor,
                    'circle-opacity': 0.8,
                  },
                })
                
                if (!infrastructureVisible) {
                  map.current.setLayoutProperty(affectedLayerId, 'visibility', 'none')
                }
              } else {
                map.current.addLayer({
                  id: affectedLayerId,
                  type: 'line',
                  source: sourceId,
                  filter: mainStyle.affectedFilter,
                  paint: {
                    'line-color': mainStyle.affectedColor,
                    'line-width': 3,
                    'line-opacity': 0.8,
                  },
                })
                
                if (!infrastructureVisible) {
                  map.current.setLayoutProperty(affectedLayerId, 'visibility', 'none')
                }
              }
              
              addPopupHandlers(layerId)
              if (map.current.getLayer(affectedLayerId)) {
                addPopupHandlers(affectedLayerId)
              }
            }
            
            map.current.moveLayer(layerId)
            if (map.current.getLayer(affectedLayerId)) {
              map.current.moveLayer(affectedLayerId)
            }
          } else {
            // No analysis yet - show all features in dark grey
            if (isPoint) {
              // Add layer without beforeId so it goes on top, then move it after hazard if hazard exists
              map.current.addLayer({
                id: layerId,
                type: 'circle',
                source: sourceId,
                paint: {
                  'circle-radius': 5,
                  'circle-color': '#6b7280', // dark grey
                  'circle-opacity': 0.8,
                },
              })
              // Apply visibility state
              if (!infrastructureVisible) {
                map.current.setLayoutProperty(layerId, 'visibility', 'none')
              }
            } else {
              map.current.addLayer({
                id: layerId,
                type: 'line',
                source: sourceId,
                paint: {
                  'line-color': '#6b7280', // dark grey
                  'line-width': 3,
                  'line-opacity': 0.8,
                },
              })
              // Apply visibility state
              if (!infrastructureVisible) {
                map.current.setLayoutProperty(layerId, 'visibility', 'none')
              }
            }
            
            // Ensure infrastructure is above hazard
            map.current.moveLayer(layerId)
            
            // Add popup handlers
            addPopupHandlers(layerId)
          }

          // Only fit bounds if we haven't set them for this file yet, or if bounds changed
          const bounds = uploadedFile.bounds
          const boundsKey = `${bounds.minx},${bounds.miny},${bounds.maxx},${bounds.maxy}`
          const fileId = uploadedFile.file_id
          
          // Only call fitBounds if:
          // 1. We haven't set bounds for this file yet, OR
          // 2. The bounds have changed
          if (boundsSetForFile.current !== fileId || lastBounds.current !== boundsKey) {
            const bbox: [number, number, number, number] = [
              bounds.minx,
              bounds.miny,
              bounds.maxx,
              bounds.maxy
            ]
            // Debounce fitBounds to avoid rapid successive calls
            if (fitBoundsTimer.current) {
              clearTimeout(fitBoundsTimer.current)
            }
            fitBoundsTimer.current = setTimeout(() => {
              if (map.current) {
                map.current.fitBounds(bbox, { padding: 50, duration: 0 }) // duration: 0 prevents animation that can trigger tile reloads
                boundsSetForFile.current = fileId
                lastBounds.current = boundsKey
              }
            }, 75)
          }
        } catch (error) {
          console.error('Error adding infrastructure layer:', error)
          // Only fit bounds if not already set for this file
          const bounds = uploadedFile.bounds
          const boundsKey = `${bounds.minx},${bounds.miny},${bounds.maxx},${bounds.maxy}`
          const fileId = uploadedFile.file_id
          
          if (boundsSetForFile.current !== fileId || lastBounds.current !== boundsKey) {
            const bbox: [number, number, number, number] = [
              bounds.minx,
              bounds.miny,
              bounds.maxx,
              bounds.maxy
            ]
            if (fitBoundsTimer.current) {
              clearTimeout(fitBoundsTimer.current)
            }
            fitBoundsTimer.current = setTimeout(() => {
              if (map.current) {
                map.current.fitBounds(bbox, { padding: 50, duration: 0 })
                boundsSetForFile.current = fileId
                lastBounds.current = boundsKey
              }
            }, 100)
          }
        }
      } else {
        // Only fit bounds if no GeoJSON available yet and bounds not set
        const bounds = uploadedFile.bounds
        const boundsKey = `${bounds.minx},${bounds.miny},${bounds.maxx},${bounds.maxy}`
        const fileId = uploadedFile.file_id
        
        if (boundsSetForFile.current !== fileId || lastBounds.current !== boundsKey) {
          const bbox: [number, number, number, number] = [
            bounds.minx,
            bounds.miny,
            bounds.maxx,
            bounds.maxy
          ]
          if (fitBoundsTimer.current) {
            clearTimeout(fitBoundsTimer.current)
          }
          fitBoundsTimer.current = setTimeout(() => {
            if (map.current) {
              map.current.fitBounds(bbox, { padding: 50, duration: 0 })
              boundsSetForFile.current = fileId
              lastBounds.current = boundsKey
            }
          }, 100)
        }
      }
    }

    waitForStyle()

    // Cleanup function
    return () => {
      cancelled = true
      if (fitBoundsTimer.current) {
        clearTimeout(fitBoundsTimer.current)
      }
    }
    // The layer helpers are recreated on every render and read the current props through refs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadedFile, analysisResult, mapLoaded, infrastructureVisible, vulnerabilityAnalysisEnabled, maxVulnerability])

  // Basemap style switcher. The style is replaced in full (diff: false), so 'style.load'
  // always fires; a diffed setStyle applied the change in place without it, and the
  // layers came back only through a one-second fallback that re-applied a snapshot
  // taken at the moment of the switch. The layers are re-added from the props as they
  // are when the new style is ready. Until then the layer effects below hold back.
  const lastBasemap = useRef<Basemap>('positron')
  useEffect(() => {
    if (!map.current || !mapLoaded) return
    if (lastBasemap.current === basemap) return
    lastBasemap.current = basemap

    isRestoringLayers.current = true
    styleReady.current = false
    const m = map.current
    const restore = () => {
      isRestoringLayers.current = false
      if (!map.current) return
      const props = currentProps.current
      if (props.selectedHazard && hazardVisible) addHazard()
      if (props.uploadedFile && infrastructureVisible) addInfrastructure()
    }
    // A later switch's restore re-adds from the latest props too, so an earlier one
    // that still fires does no harm
    m.once('style.load', restore)
    m.setStyle(basemapStyles[basemap], { diff: false })
    // Runs on a basemap change only; the restore reads everything else when it fires
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap, mapLoaded])

  // Helper function to apply infrastructure visibility
  const applyInfrastructureVisibility = (visible: boolean) => {
    if (!map.current) return
    
    const layerId = 'infrastructure-layer'
    const affectedLayerId = 'infrastructure-affected'
    
    try {
      if (map.current.getLayer(layerId)) {
        map.current.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none')
      }
      if (map.current.getLayer(affectedLayerId)) {
        map.current.setLayoutProperty(affectedLayerId, 'visibility', visible ? 'visible' : 'none')
      }
    } catch (error) {
      console.error('Error applying infrastructure visibility:', error)
    }
  }

  // Toggle infrastructure layer visibility. The layers are shown or hidden, not removed:
  // the infrastructure effect above adds them, and re-adds them when the data changes.
  useEffect(() => {
    if (!map.current || !mapLoaded) return
    ensureStyleLoaded(() => applyInfrastructureVisibility(infrastructureVisible))
  }, [infrastructureVisible, mapLoaded])

  // Reset visibility when layers are added/removed
  useEffect(() => {
    if (uploadedFile) {
      setInfrastructureVisible(true)
    }
  }, [uploadedFile])

  useEffect(() => {
    if (selectedHazard) {
      setHazardVisible(true)
    }
  }, [selectedHazard])

  const [zoomUi, setZoomUi] = useState<{ z: number; min: number; max: number } | null>(null)

  useEffect(() => {
    if (!map.current || !mapLoaded) {
      setZoomUi(null)
      return
    }
    const m = map.current
    const sync = () => {
      setZoomUi({ z: m.getZoom(), min: m.getMinZoom(), max: m.getMaxZoom() })
    }
    sync()
    m.on('zoom', sync)
    m.on('zoomend', sync)
    m.on('style.load', sync)
    return () => {
      m.off('zoom', sync)
      m.off('zoomend', sync)
      m.off('style.load', sync)
    }
  }, [mapLoaded])

  const mapReady = mapLoaded && map.current != null
  const atMaxZoom = zoomUi != null && zoomUi.z >= zoomUi.max - 1e-6
  const atMinZoom = zoomUi != null && zoomUi.z <= zoomUi.min + 1e-6

  return (
    <div className="relative flex-1 h-full m-0 p-0">
      <div className="absolute top-4 right-4 z-10 flex flex-col items-end gap-2">
        {/* Basemap Selector */}
        <div className="bg-white/80 rounded-lg px-1">
          <Select
            value={basemap}
            onChange={(e) => onBasemapChange(e.target.value as Basemap)}
            className="w-40 border-0 bg-transparent"
          >
            <option value="positron">CartoDB Positron</option>
            <option value="dark-matter">CartoDB Dark Matter</option>
            <option value="osm">OpenStreetMap</option>
            <option value="topo">OpenTopoMap</option>
            <option value="esri-street">Esri World Street Map</option>
            <option value="esri-topo">Esri World Topo Map</option>
            <option value="esri-terrain">Esri World Terrain</option>
            <option value="esri-ocean">Esri Ocean Basemap</option>
            <option value="esri-imagery">Esri World Imagery</option>
          </Select>
        </div>

        {/* Layer Control Panel (only show if at least one layer is available) */}
        {(uploadedFile || selectedHazard) && (
          <div className="bg-white/80 rounded-lg px-3 py-2 space-y-2">
            {uploadedFile && (
              <label className="flex items-center space-x-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={infrastructureVisible}
                  onChange={(e) => setInfrastructureVisible(e.target.checked)}
                  className="w-4 h-4 text-gray-600 rounded"
                />
                <span className="text-sm text-gray-700">Infrastructure</span>
              </label>
            )}

            {selectedHazard && (
              <label className="flex items-center space-x-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={hazardVisible}
                  onChange={(e) => setHazardVisible(e.target.checked)}
                  className="w-4 h-4 text-gray-600 rounded"
                />
                <span className="text-sm text-gray-700">Hazard Layer</span>
              </label>
            )}
          </div>
        )}

        {/* Zoom controls (wheel alternative) */}
        <div className="flex flex-col bg-white/80 rounded-lg overflow-hidden">
          <button
            type="button"
            aria-label="Zoom in"
            disabled={!mapReady || atMaxZoom}
            className="min-h-9 min-w-9 flex items-center justify-center text-base font-medium text-gray-800 hover:bg-gray-100/80 disabled:opacity-40 disabled:pointer-events-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white/80"
            onClick={() => map.current?.zoomIn({ duration: 200 })}
          >
            +
          </button>
          <button
            type="button"
            aria-label="Zoom out"
            disabled={!mapReady || atMinZoom}
            className="min-h-9 min-w-9 flex items-center justify-center text-base font-medium text-gray-800 hover:bg-gray-100/80 disabled:opacity-40 disabled:pointer-events-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white/80"
            onClick={() => map.current?.zoomOut({ duration: 200 })}
          >
            −
          </button>
        </div>
      </div>

      {/* Hazard Color Bar Legend */}
      {selectedHazard && hazardStats && hazardVisible && (() => {
        const sqrtMin = Math.sqrt(hazardStats.min)
        const sqrtMax = Math.sqrt(hazardStats.max)
        const ticks = [0, 0.25, 0.5, 0.75, 1].map(p => {
          const val = Math.pow(p * (sqrtMax - sqrtMin) + sqrtMin, 2)
          return { position: p * 100, label: val >= 100 ? Math.round(val).toLocaleString() : val >= 1 ? val.toFixed(1) : val.toFixed(2) }
        })
        const stops = COLORMAP_STOPS[colorPalette]
        const gradient = `linear-gradient(to right, ${stops.map((c, i) => `${c} ${(i / (stops.length - 1) * 100).toFixed(0)}%`).join(', ')})`

        // Build legend title with hazard unit, avoiding double parentheses like ((index))
        let unitLabel: string | null = null
        if (selectedHazard.unit) {
          let u = selectedHazard.unit.trim()
          if (u.startsWith('(') && u.endsWith(')') && u.length > 2) {
            u = u.slice(1, -1).trim()
          }
          unitLabel = u || null
        }
        const legendTitle = unitLabel ? `Hazard Intensity (${unitLabel})` : 'Hazard Intensity'

        return (
          <div className="absolute top-4 left-4 z-10 bg-white/50 rounded-lg px-3 py-2">
            <div className="text-[10px] text-gray-800 mb-1">{legendTitle}</div>
            <div className="rounded" style={{ width: 220, height: 12, background: gradient }} />
            <div className="relative" style={{ width: 220, height: 16 }}>
              {ticks.map((t, i) => (
                <span
                  key={i}
                  className="absolute text-[10px] text-gray-800"
                  style={{
                    left: `${t.position}%`,
                    transform: i === ticks.length - 1 ? 'translateX(-100%)' : i === 0 ? 'none' : 'translateX(-50%)',
                    top: 2,
                  }}
                >
                  {t.label}
                </span>
              ))}
            </div>
          </div>
        )
      })()}

      {/* Damage Ratio color bar (vulnerability analysis mode) */}
      {vulnerabilityAnalysisEnabled && infrastructureVisible && analysisResult?.infrastructure_features && (() => {
        const maxPct = maxVulnerability * 100
        const ticks = [0, 0.25, 0.5, 0.75, 1].map(p => {
          const pct = Math.pow(p, 2) * maxPct
          return { position: p * 100, label: pct >= 10 ? `${Math.round(pct)}%` : pct >= 0.1 ? `${pct.toFixed(1)}%` : `${pct.toFixed(2)}%` }
        })
        const gradient = 'linear-gradient(to right, #10b981 0%, #f59e0b 50%, #ef4444 100%)'
        return (
          <div className="absolute bottom-4 left-4 z-10 bg-white/50 rounded-lg px-3 py-2">
            <div className="text-[10px] text-gray-800 mb-1">Damage Ratio</div>
            <div className="rounded" style={{ width: 220, height: 12, background: gradient }} />
            <div className="relative" style={{ width: 220, height: 16 }}>
              {ticks.map((t, i) => (
                <span
                  key={i}
                  className="absolute text-[10px] text-gray-800"
                  style={{
                    left: `${t.position}%`,
                    transform: i === ticks.length - 1 ? 'translateX(-100%)' : i === 0 ? 'none' : 'translateX(-50%)',
                    top: 2,
                  }}
                >
                  {t.label}
                </span>
              ))}
            </div>
          </div>
        )
      })()}

      {/* Map Container */}
      <div 
        ref={mapContainer} 
        className="w-full h-full" 
        style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
      />
    </div>
  )
}

