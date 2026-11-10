import { useMemo, useState, useEffect, useRef } from 'react'
import { ChevronLeft, ChevronRight, Info, ChevronUp, ChevronDown, Download } from 'lucide-react'
import { Hazard, UploadedFile, AnalysisResult, ColorPalette, Basemap } from '../types'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Select } from './ui/select'
import { Slider } from './ui/slider'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import BarChart from './BarChart'
import { exportBarchart, exportMap, exportData } from '../services/api'

interface SidebarProps {
  isOpen: boolean
  onToggle: () => void
  uploadedFile: UploadedFile | null
  onFileUpload: (file: File) => Promise<void>
  onClearData: () => void
  hazards: Hazard[]
  selectedHazard: Hazard | null
  onSelectHazard: (hazard: Hazard | null) => void
  colorPalette: ColorPalette
  onColorPaletteChange: (palette: ColorPalette) => void
  hazardOpacity: number
  onHazardOpacityChange: (opacity: number) => void
  intensityThreshold: number
  onIntensityThresholdChange: (threshold: number) => void
  hazardStats: { min: number; max: number } | null
  analysisResult: AnalysisResult | null
  loadingUpload?: boolean
  loadingAnalysis?: boolean
  error?: string | null
  vulnerabilityAnalysisEnabled: boolean
  onVulnerabilityAnalysisEnabledChange: (enabled: boolean) => void
  vulnerabilityCurveFile: File | null
  onVulnerabilityCurveFileChange: (file: File | null) => void
  replacementValue: number | null
  onReplacementValueChange: (value: number | null) => void
  basemap: Basemap
}

const colorPalettes: ColorPalette[] = ['viridis', 'magma', 'inferno', 'plasma', 'cividis', 'turbo']

export default function Sidebar({
  isOpen,
  onToggle,
  uploadedFile,
  onFileUpload,
  onClearData,
  hazards,
  selectedHazard,
  onSelectHazard,
  colorPalette,
  onColorPaletteChange,
  hazardOpacity,
  onHazardOpacityChange,
  intensityThreshold,
  onIntensityThresholdChange,
  hazardStats,
  analysisResult,
  loadingUpload = false,
  loadingAnalysis = false,
  error = null,
  vulnerabilityAnalysisEnabled,
  onVulnerabilityAnalysisEnabledChange,
  vulnerabilityCurveFile,
  onVulnerabilityCurveFileChange,
  replacementValue,
  onReplacementValueChange,
  basemap,
}: SidebarProps) {
  const [infoOpen, setInfoOpen] = useState(false)
  const [uploadInfoOpen, setUploadInfoOpen] = useState(false)
  const [thresholdInfoOpen, setThresholdInfoOpen] = useState(false)
  const [paletteInfoOpen, setPaletteInfoOpen] = useState(false)
  const [vulnerabilityCurveInfoOpen, setVulnerabilityCurveInfoOpen] = useState(false)
  const [replacementValueInfoOpen, setReplacementValueInfoOpen] = useState(false)
  const [vulnerabilityAnalysisInfoOpen, setVulnerabilityAnalysisInfoOpen] = useState(false)
  const [analysisResultsInfoOpen, setAnalysisResultsInfoOpen] = useState(false)
  const [exportingBarchart, setExportingBarchart] = useState(false)
  const [exportingMap, setExportingMap] = useState(false)
  const [exportingData, setExportingData] = useState(false)
  const [dataMenuOpen, setDataMenuOpen] = useState(false)
  const dataMenuRef = useRef<HTMLDivElement>(null)

  // Local (immediate) state for the Replacement Value input. The parent
  // (and therefore recalculation) is only updated after a short debounce so a
  // multi-digit number can be typed without triggering analysis on each digit.
  const [replacementValueInput, setReplacementValueInput] = useState<string>(
    replacementValue !== null ? String(replacementValue) : ''
  )
  // Tracks whether the latest change came from the user typing, so external
  // updates to `replacementValue` (e.g. reset) don't fight the local state.
  const replacementValueEditing = useRef(false)

  // Resync local input when the prop changes externally (not from our own typing).
  useEffect(() => {
    if (replacementValueEditing.current) return
    setReplacementValueInput(replacementValue !== null ? String(replacementValue) : '')
  }, [replacementValue])

  // Debounce propagating the typed value to the parent.
  useEffect(() => {
    if (!replacementValueEditing.current) return
    const handle = setTimeout(() => {
      const val = replacementValueInput === '' ? null : parseFloat(replacementValueInput)
      const normalized = val !== null && Number.isNaN(val) ? null : val
      if (normalized !== replacementValue) {
        onReplacementValueChange(normalized)
      }
      replacementValueEditing.current = false
    }, 700)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replacementValueInput])

  useEffect(() => {
    if (!dataMenuOpen) return
    const onDoc = (e: MouseEvent) => {
      if (dataMenuRef.current && !dataMenuRef.current.contains(e.target as Node)) {
        setDataMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [dataMenuOpen])

  const vulnerabilityWarning = useMemo(() => {
    if (!vulnerabilityAnalysisEnabled) return null
    if (!vulnerabilityCurveFile) {
      return 'Upload a vulnerability curve CSV to run vulnerability analysis.'
    }
    if (replacementValue === null) {
      return 'Enter a replacement value (USD) to calculate damage costs.'
    }
    if (replacementValue <= 0) {
      return 'Replacement value must be greater than zero.'
    }
    return null
  }, [vulnerabilityAnalysisEnabled, vulnerabilityCurveFile, replacementValue])

  const sliderExponent = 2

  const clamp = (value: number, min: number, max: number) => {
    if (value < min) return min
    if (value > max) return max
    return value
  }

  const toSliderValue = (value: number, min: number, max: number) => {
    if (max <= min) return 0
    const normalized = (clamp(value, min, max) - min) / (max - min)
    return Math.pow(normalized, 1 / sliderExponent)
  }

  const fromSliderValue = (value: number, min: number, max: number) => {
    if (max <= min) return min
    const normalized = Math.pow(clamp(value, 0, 1), sliderExponent)
    return min + normalized * (max - min)
  }

  // Local (immediate) state for the threshold number input, so an exact value
  // can be typed instead of only dragged. The parent — and therefore the
  // analysis — is only updated after a debounce, so typing "1500" doesn't
  // re-run the analysis once per digit.
  //
  // The pending timer is held in a ref rather than driven by an effect's
  // dependencies: the slider and the input write to the same value, and an
  // effect-based debounce let a half-typed number land *after* a later drag
  // and silently overwrite it. Cancelling explicitly makes that unrepresentable.
  const [thresholdInput, setThresholdInput] = useState<string>('')
  const thresholdEditing = useRef(false)
  const thresholdTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const cancelPendingThreshold = () => {
    if (thresholdTimer.current !== null) {
      clearTimeout(thresholdTimer.current)
      thresholdTimer.current = null
    }
    thresholdEditing.current = false
  }

  useEffect(() => cancelPendingThreshold, [])

  // A trailing ".0" is noise in a field you type into; the min/max labels
  // beside it keep their fixed precision.
  const formatThreshold = (value: number) =>
    Number.isFinite(value) ? String(Math.round(value * 10) / 10) : ''

  // Resync when the threshold changes from somewhere else: dragging the
  // slider, or the threshold set for a newly selected hazard layer.
  useEffect(() => {
    if (thresholdEditing.current) return
    setThresholdInput(formatThreshold(intensityThreshold))
  }, [intensityThreshold])

  // Parse, clamp to the layer's range, and push. `normalize` is set by Enter
  // and blur, which are done-editing signals: an empty or half-typed field is
  // then put back to the live value. The debounce passes false — clearing the
  // box on the way to retyping is legitimate, and refilling it mid-keystroke
  // would fight the user.
  const commitThreshold = (raw: string, normalize: boolean) => {
    cancelPendingThreshold()
    const parsed = parseFloat(raw)
    if (!hazardStats || Number.isNaN(parsed)) {
      if (normalize) setThresholdInput(formatThreshold(intensityThreshold))
      return
    }
    const next = clamp(parsed, hazardStats.min, hazardStats.max)
    setThresholdInput(formatThreshold(next))
    if (next !== intensityThreshold) {
      onIntensityThresholdChange(next)
    }
  }

  const onThresholdTyped = (raw: string) => {
    thresholdEditing.current = true
    setThresholdInput(raw)
    if (thresholdTimer.current !== null) clearTimeout(thresholdTimer.current)
    thresholdTimer.current = setTimeout(() => commitThreshold(raw, false), 700)
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    await onFileUpload(file)
    // Reset file input
    e.target.value = ''
  }

  const handleHazardSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const hazardId = e.target.value
    const hazard = hazards.find(h => h.id === hazardId) || null
    onSelectHazard(hazard)
  }

  const hazardSelectGroups = useMemo(() => {
    const groups: { label: string; items: Hazard[] }[] = []
    for (const h of hazards) {
      const label = h.category?.trim() || 'Other'
      const last = groups[groups.length - 1]
      if (last && last.label === label) {
        last.items.push(h)
      } else {
        groups.push({ label, items: [h] })
      }
    }
    return groups
  }, [hazards])

  return (
    <>
      <div
        className={`bg-gray-900 transition-all duration-300 flex flex-col ${
          isOpen ? 'w-80' : 'w-0 overflow-hidden'
        }`}
      >
        {isOpen && (
          <div className="flex flex-col h-full overflow-y-auto p-4">
            {/* Header */}
            <div className="flex items-center justify-between gap-2 mb-5">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <h1 className="min-w-0 flex-1 text-xl font-bold leading-tight text-white">
                  Infrastructure Risk Analyzer
                </h1>
                <div className="shrink-0">
                  <a
                    href="infra-risk-analyzer-guide.pdf"
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open user guide (PDF)"
                    aria-label="Open user guide (PDF)"
                    className="inline-flex items-center justify-center rounded-md border-0 px-3 py-1 text-sm font-medium bg-blue-600 text-white no-underline transition-colors hover:bg-blue-500"
                  >
                    Guide
                  </a>
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={onToggle} className="shrink-0 text-gray-300 hover:text-white hover:bg-gray-800">
                <ChevronLeft className="h-4 w-4" />
              </Button>
            </div>

            {/* Input Fields Section */}
            <div className="flex flex-col flex-1">
              {/* File Upload */}
              <div className="mb-5">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-sm font-medium text-gray-300">
                    Upload Infrastructure Data
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setUploadInfoOpen(true)}
                    className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {uploadedFile ? (
                  <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                    <div className="flex items-center justify-between">
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-gray-300 truncate">
                          ✓ {uploadedFile.filename}
                        </p>
                        <p className="text-xs text-gray-400 mt-0.5">
                          {uploadedFile.feature_count} features
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={onClearData}
                        disabled={loadingUpload || loadingAnalysis}
                        className="ml-2 text-xs text-gray-300 bg-gray-700 hover:text-red-500 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        Clear Data
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                      <Input
                        type="file"
                        accept=".shp,.gpkg,.csv,.zip,.geojson"
                        onChange={handleFileChange}
                        disabled={loadingUpload}
                        className="cursor-pointer border-0 bg-transparent px-0 py-0 h-auto text-gray-300 file:mr-4 file:py-1 file:px-3 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-blue-600 file:text-white hover:file:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed [&::file-selector-button]:mr-4"
                      />
                    </div>
                    {loadingUpload && (
                      <p className="text-xs text-blue-400 mt-1">Uploading...</p>
                    )}
                    {error && (
                      <p className="text-xs text-red-400 mt-1">{error}</p>
                    )}
                  </>
                )}
              </div>

              {/* Hazard Selector */}
              <div className="mb-5">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-sm font-medium text-gray-300">
                    Select Hazard Layer
                  </label>
                  {selectedHazard && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setInfoOpen(true)}
                      className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                    >
                      <Info className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
                <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                  <Select
                    value={selectedHazard?.id || ''}
                    onChange={handleHazardSelect}
                    title={selectedHazard?.name}
                    className="border-0 bg-transparent px-0 py-0 h-auto text-gray-300"
                  >
                    <option value="" className="bg-gray-800 text-gray-300">Select a hazard...</option>
                    {hazardSelectGroups.map((group, idx) => (
                      <optgroup key={`${idx}-${group.label}`} label={group.label}>
                        {group.items.map(hazard => (
                          <option
                            key={hazard.id}
                            value={hazard.id}
                            title={hazard.name}
                            className="bg-gray-800 text-gray-300"
                          >
                            {hazard.name}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </Select>
                </div>
              </div>

              {/* Color Palette and Opacity */}
              <div className="mb-5">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-sm font-medium text-gray-300">
                    Color Palette for Hazard Layer
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setPaletteInfoOpen(true)}
                    className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm flex items-center gap-3">
                  <Select
                    value={colorPalette}
                    onChange={(e) => onColorPaletteChange(e.target.value as ColorPalette)}
                    className="border-0 bg-transparent px-0 py-0 h-auto text-gray-300 flex-1"
                  >
                    {colorPalettes.map(palette => (
                      <option key={palette} value={palette} className="bg-gray-800 text-gray-300">
                        {palette.charAt(0).toUpperCase() + palette.slice(1)}
                      </option>
                    ))}
                  </Select>
                  <div className="flex items-center border-l border-gray-700 pl-3">
                    <div className="relative flex items-center">
                      <Input
                        type="number"
                        min="0"
                        max="100"
                        value={hazardOpacity}
                        onChange={(e) => {
                          const val = parseInt(e.target.value) || 0
                          if (val >= 0 && val <= 100) {
                            onHazardOpacityChange(val)
                          }
                        }}
                        className="opacity-input w-16 border-0 bg-transparent px-2 py-0 h-auto text-gray-300 text-sm text-right"
                      />
                      <span className="text-xs text-gray-400">%</span>
                      <div className="flex flex-col ml-1 gap-0">
                        <button
                          type="button"
                          onClick={() => {
                            if (hazardOpacity < 100) {
                              onHazardOpacityChange(hazardOpacity + 1)
                            }
                          }}
                          disabled={hazardOpacity >= 100}
                          className="p-0 text-gray-400 hover:text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed"
                        >
                          <ChevronUp className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (hazardOpacity > 0) {
                              onHazardOpacityChange(hazardOpacity - 1)
                            }
                          }}
                          disabled={hazardOpacity <= 0}
                          className="p-0 text-gray-400 hover:text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed"
                        >
                          <ChevronDown className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Intensity Slider. Shown in vulnerability mode too: the
                  threshold decides which assets take damage, so hiding it left
                  a value the user could not see still driving the result. */}
              {selectedHazard && hazardStats && (
                <div className="mb-3">
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-sm font-medium text-gray-300">
                      Hazard Intensity Threshold
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setThresholdInfoOpen(true)}
                      className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                    >
                      <Info className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  <div className="space-y-1">
                    <Slider
                      value={toSliderValue(intensityThreshold, hazardStats.min, hazardStats.max)}
                      onValueChange={(value) => {
                        // Dragging wins over a half-typed value.
                        cancelPendingThreshold()
                        onIntensityThresholdChange(
                          fromSliderValue(value, hazardStats.min, hazardStats.max)
                        )
                      }}
                      min={0}
                      max={1}
                      step={0.001}
                    />
                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <span>{hazardStats.min.toFixed(1)}</span>
                      <div className="flex items-center gap-1">
                        <Input
                          type="number"
                          step="any"
                          min={hazardStats.min}
                          max={hazardStats.max}
                          value={thresholdInput}
                          onChange={(e) => onThresholdTyped(e.target.value)}
                          onBlur={(e) => commitThreshold(e.target.value, true)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              commitThreshold((e.target as HTMLInputElement).value, true)
                            }
                          }}
                          aria-label="Hazard intensity threshold"
                          title="Type an exact threshold, or drag the slider"
                          className="w-24 h-auto rounded-md border-gray-700 bg-gray-800 px-2 py-1 text-xs text-right text-gray-200 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0 [&::-webkit-outer-spin-button]:m-0"
                        />
                        {selectedHazard?.unit ? <span>{selectedHazard.unit}</span> : null}
                      </div>
                      <span>{hazardStats.max.toFixed(1)}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Horizontal Separator */}
              <hr className={`border-gray-700 ${selectedHazard && hazardStats ? 'my-4' : 'mt-1 mb-4'}`} />

              {/* Vulnerability Analysis Section */}
              <div className={vulnerabilityAnalysisEnabled ? "mb-2" : "mb-1"}>
                <div className={`flex items-center justify-between ${vulnerabilityAnalysisEnabled ? "mb-3" : "mb-1"}`}>
                  <div className="flex items-center">
                    <input
                      type="checkbox"
                      id="vulnerability-analysis"
                      checked={vulnerabilityAnalysisEnabled}
                      onChange={(e) => onVulnerabilityAnalysisEnabledChange(e.target.checked)}
                      disabled={loadingUpload || loadingAnalysis}
                      className="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded focus:ring-blue-500 focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                    <label
                      htmlFor="vulnerability-analysis"
                      className="ml-2 text-sm font-medium text-gray-300 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Vulnerability Analysis
                    </label>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setVulnerabilityAnalysisInfoOpen(true)}
                    className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </Button>
                </div>

                {vulnerabilityAnalysisEnabled && (
                  <div className="space-y-4">
                    {/* Vulnerability Curve Upload */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="block text-sm font-medium text-gray-300">
                          Vulnerability Curve
                          <span className="text-red-400 ml-0.5">*</span>
                        </label>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setVulnerabilityCurveInfoOpen(true)}
                          className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      {vulnerabilityCurveFile ? (
                        <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                          <div className="flex items-center justify-between">
                            <div className="flex-1 min-w-0">
                              <p className="text-xs text-gray-300 truncate">
                                ✓ {vulnerabilityCurveFile.name}
                              </p>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onVulnerabilityCurveFileChange(null)}
                              disabled={loadingUpload || loadingAnalysis}
                              className="ml-2 text-xs text-gray-300 bg-gray-700 hover:text-red-500 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              Clear
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                          <Input
                            type="file"
                            accept=".csv"
                            onChange={(e) => {
                              const file = e.target.files?.[0]
                              if (file) {
                                onVulnerabilityCurveFileChange(file)
                              }
                            }}
                            disabled={loadingUpload || loadingAnalysis}
                            className="cursor-pointer border-0 bg-transparent px-0 py-0 h-auto text-gray-300 file:mr-4 file:py-1 file:px-3 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-blue-600 file:text-white hover:file:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed [&::file-selector-button]:mr-4"
                          />
                        </div>
                      )}
                    </div>

                    {/* Replacement Value */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="block text-sm font-medium text-gray-300">
                          Replacement Value (USD)
                          <span className="text-red-400 ml-0.5">*</span>
                        </label>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setReplacementValueInfoOpen(true)}
                          className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      <div className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 shadow-sm">
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          value={replacementValueInput}
                          onChange={(e) => {
                            replacementValueEditing.current = true
                            setReplacementValueInput(e.target.value)
                          }}
                          // Not disabled while an analysis runs: a pause in typing starts
                          // one, and disabling the field then dropped the digits typed next
                          disabled={loadingUpload}
                          placeholder="e.g. 100000"
                          className="border-0 bg-transparent px-0 py-0 h-auto text-gray-300 placeholder:text-gray-500 disabled:opacity-50 disabled:cursor-not-allowed"
                        />
                      </div>
                    </div>

                    {vulnerabilityWarning && (
                      <p className="text-xs text-amber-400 border border-amber-500/40 rounded-md px-3 py-2 bg-amber-500/10">
                        {vulnerabilityWarning}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Horizontal Separator */}
            <hr className={vulnerabilityAnalysisEnabled ? "my-4 border-gray-700" : "my-2 border-gray-700"} />

            {/* Bar Chart Section */}
            <div className="flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-medium text-gray-300">Analysis Results</h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setAnalysisResultsInfoOpen(true)}
                  className="h-6 w-6 p-0 text-gray-300 hover:text-white hover:bg-gray-800"
                >
                  <Info className="h-3.5 w-3.5" />
                </Button>
              </div>
              {loadingAnalysis && !analysisResult && (
                <div className="w-full h-48 flex items-center justify-center text-sm text-gray-500 border border-gray-700 rounded bg-gray-800">
                  <div className="text-center">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500 mx-auto mb-2"></div>
                    <p>Analyzing...</p>
                  </div>
                </div>
              )}
              {!loadingAnalysis && analysisResult ? (
                <BarChart data={analysisResult} />
              ) : !loadingAnalysis && !analysisResult ? (
                <div className="w-full h-48 flex items-center justify-center text-sm text-gray-500 border border-gray-700 rounded bg-gray-800 px-4 text-center">
                  {vulnerabilityWarning
                    ? 'Complete vulnerability inputs above to run analysis.'
                    : uploadedFile && selectedHazard
                      ? 'No analysis data yet'
                      : 'Upload data and select a hazard to analyze'}
                </div>
              ) : null}
              {/* Without a dataset the error is shown under the upload box instead */}
              {error && uploadedFile && (
                <p className="text-xs text-red-400 mt-2">{error}</p>
              )}
              
              {/* Export Buttons */}
              {!loadingAnalysis && analysisResult && uploadedFile && selectedHazard && (
                <div className="mt-4 grid grid-cols-3 gap-2">
                  <div className="relative min-w-0" ref={dataMenuRef}>
                    {dataMenuOpen && uploadedFile.geometry_type === 'LineString' && (
                      <div className="absolute bottom-full left-0 z-30 mb-1 flex min-w-max flex-col overflow-hidden rounded border border-gray-600 bg-gray-900 shadow-lg">
                        <button
                          type="button"
                          title="Download data (CSV, aggregate)"
                          className="whitespace-nowrap px-4 py-2 text-left text-sm text-gray-200 hover:bg-gray-800"
                          onClick={async () => {
                            if (!uploadedFile || !selectedHazard) return
                            setDataMenuOpen(false)
                            try {
                              setExportingData(true)
                              const threshold = hazardStats && intensityThreshold > hazardStats.min
                                ? intensityThreshold
                                : undefined
                              await exportData(
                                uploadedFile.file_id,
                                selectedHazard.id,
                                'csv_lines_aggregate',
                                threshold
                              )
                            } catch (err) {
                              console.error('Failed to export line CSV:', err)
                              alert(err instanceof Error ? err.message : 'Failed to export data')
                            } finally {
                              setExportingData(false)
                            }
                          }}
                        >
                          CSV (aggregate)
                        </button>
                        <button
                          type="button"
                          title="Download data (GPKG, split segments)"
                          className="whitespace-nowrap border-t border-gray-700 px-4 py-2 text-left text-sm text-gray-200 hover:bg-gray-800"
                          onClick={async () => {
                            if (!uploadedFile || !selectedHazard) return
                            setDataMenuOpen(false)
                            try {
                              setExportingData(true)
                              const threshold = hazardStats && intensityThreshold > hazardStats.min
                                ? intensityThreshold
                                : undefined
                              await exportData(
                                uploadedFile.file_id,
                                selectedHazard.id,
                                'gpkg_lines_split',
                                threshold
                              )
                            } catch (err) {
                              console.error('Failed to export line GPKG:', err)
                              alert(err instanceof Error ? err.message : 'Failed to export data')
                            } finally {
                              setExportingData(false)
                            }
                          }}
                        >
                          GPKG (split)
                        </button>
                      </div>
                    )}
                    <Button
                      title="Download data (CSV)"
                      onClick={async () => {
                        if (!uploadedFile || !selectedHazard) return
                        if (uploadedFile.geometry_type === 'LineString') {
                          setDataMenuOpen((open) => !open)
                          return
                        }
                        try {
                          setExportingData(true)
                          const threshold = hazardStats && intensityThreshold > hazardStats.min
                            ? intensityThreshold
                            : undefined
                          await exportData(
                            uploadedFile.file_id,
                            selectedHazard.id,
                            'csv_points',
                            threshold
                          )
                        } catch (err) {
                          console.error('Failed to export data:', err)
                          alert(err instanceof Error ? err.message : 'Failed to export data')
                        } finally {
                          setExportingData(false)
                        }
                      }}
                      disabled={exportingBarchart || exportingMap || exportingData}
                      className="w-full min-w-0 bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {exportingData ? (
                        <>
                          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-300 mr-2"></div>
                          Exporting...
                        </>
                      ) : (
                        <>
                          <Download className="h-4 w-4 mr-2 shrink-0" />
                          Data
                          {uploadedFile.geometry_type === 'LineString' && (
                            <ChevronDown className="h-4 w-4 ml-1 shrink-0 opacity-70" />
                          )}
                        </>
                      )}
                    </Button>
                  </div>
                  <Button
                    title="Download chart (PNG)"
                    onClick={async () => {
                      if (!uploadedFile || !selectedHazard) return
                      try {
                        setExportingBarchart(true)
                        const threshold = hazardStats && intensityThreshold > hazardStats.min 
                          ? intensityThreshold 
                          : undefined
                        await exportBarchart(
                          uploadedFile.file_id,
                          selectedHazard.id,
                          threshold
                        )
                      } catch (err) {
                        console.error('Failed to export barchart:', err)
                        alert(err instanceof Error ? err.message : 'Failed to export barchart')
                      } finally {
                        setExportingBarchart(false)
                      }
                    }}
                    disabled={exportingBarchart || exportingMap || exportingData}
                    className="w-full min-w-0 bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {exportingBarchart ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-300 mr-2"></div>
                        Generating...
                      </>
                    ) : (
                      <>
                        <Download className="h-4 w-4 mr-2" />
                        Barchart
                      </>
                    )}
                  </Button>
                  <Button
                    title="Download map (PNG)"
                    onClick={async () => {
                      if (!uploadedFile || !selectedHazard) return
                      try {
                        setExportingMap(true)
                        const threshold = hazardStats && intensityThreshold > hazardStats.min 
                          ? intensityThreshold 
                          : undefined
                        await exportMap(
                          uploadedFile.file_id,
                          selectedHazard.id,
                          colorPalette,
                          hazardOpacity,
                          threshold,
                          basemap
                        )
                      } catch (err) {
                        console.error('Failed to export map:', err)
                        alert(err instanceof Error ? err.message : 'Failed to export map')
                      } finally {
                        setExportingMap(false)
                      }
                    }}
                    disabled={exportingBarchart || exportingMap || exportingData}
                    className="w-full min-w-0 bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {exportingMap ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-300 mr-2"></div>
                        Generating...
                      </>
                    ) : (
                      <>
                        <Download className="h-4 w-4 mr-2" />
                        Map
                      </>
                    )}
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Collapse/Expand Button */}
      {!isOpen && (
        <button
          onClick={onToggle}
          className="absolute left-0 top-1/2 -translate-y-1/2 z-10 bg-gray-900 rounded-r-lg p-2 shadow-lg hover:bg-gray-800 text-white"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      )}

      {/* Hazard Info Dialog */}
      <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
        <DialogContent onClose={() => setInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>{selectedHazard?.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            {selectedHazard?.description && (
              <p className="text-sm text-gray-700">{selectedHazard.description}</p>
            )}
            {selectedHazard?.metadata && (
              <div style={{ paddingTop: '0.7rem' }}>
                <p className="text-sm font-bold text-gray-600 mb-1">Background Paper</p>
                <p className="text-sm text-gray-700">
                  {(() => {
                    const text = selectedHazard.metadata
                    // URL regex pattern
                    const urlRegex = /(https?:\/\/[^\s]+)/g
                    const parts = text.split(urlRegex)
                    
                    return parts.map((part, index) => {
                      // Check if part matches URL pattern
                      const isUrl = /^https?:\/\/[^\s]+$/.test(part)
                      if (isUrl) {
                        return (
                          <a
                            key={index}
                            href={part}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:underline"
                          >
                            {part}
                          </a>
                        )
                      }
                      return <span key={index}>{part}</span>
                    })
                  })()}
                </p>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Upload Info Dialog */}
      <Dialog open={uploadInfoOpen} onOpenChange={setUploadInfoOpen}>
        <DialogContent onClose={() => setUploadInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Accepted File Formats</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Shapefile (.shp)</p>
              <p className="text-sm text-gray-600">
                Upload as a ZIP file containing all required components (.shp, .shx, .dbf, and optionally .prj).
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">GeoPackage (.gpkg)</p>
              <p className="text-sm text-gray-600">
                Single-file geospatial database format.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">GeoJSON (.geojson)</p>
              <p className="text-sm text-gray-600">
                GeoJSON format.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">CSV (.csv)</p>
              <p className="text-sm text-gray-600">
                Comma or semicolon-delimited file with coordinate columns. Supported column names:
              </p>
              <ul className="text-xs text-gray-500 mt-1 ml-4 list-disc">
                <li>lat/lon, lat/lng</li>
                <li>latitude/longitude</li>
                <li>y/x (case-insensitive)</li>
              </ul>
              <p className="text-xs text-gray-500 mt-1">
                Coordinate-based CSVs are interpreted as Point geometries. Alternatively,
                a WKT geometry column (e.g. <code>geometry</code>, <code>geom</code>, or
                <code> wkt</code>) holding POINT or LINESTRING values is supported.
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Note:</strong> Maximum file size is 100 MB. Supported geometry types are Point, LineString, and Polygon/MultiPolygon (converted to centroid points for analysis – hazard layers have a resolution of 100m which is too coarse for most buildings). Files should be homogeneous (all features should be of the same type).
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Threshold Info Dialog */}
      <Dialog open={thresholdInfoOpen} onOpenChange={setThresholdInfoOpen}>
        <DialogContent onClose={() => setThresholdInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Hazard Intensity Threshold</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm text-gray-700">
                The intensity threshold determines the minimum hazard value required for infrastructure to be considered <strong>"affected"</strong>.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">How it works:</p>
              <ul className="text-sm text-gray-600 space-y-1 ml-4 list-disc">
                <li>
                  <strong>Point features:</strong> A point is marked as affected (red) if the hazard raster value at that location is greater than or equal to the threshold.
                </li>
                <li>
                  <strong>Line features:</strong> A line is marked as affected (red) if any point along the line has a hazard value greater than or equal to the threshold.
                </li>
              </ul>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Impact on results:</p>
              <ul className="text-sm text-gray-600 space-y-1 ml-4 list-disc">
                <li>
                  <strong>Bar chart:</strong> Shows the count (for points) or length in meters (for lines) of affected vs. unaffected infrastructure based on the threshold.
                </li>
                <li>
                  <strong>Map:</strong> Infrastructure features are color-coded: <span className="text-red-600 font-semibold">red</span> for affected and <span className="text-green-600 font-semibold">green</span> for unaffected.
                </li>
                <li>
                  <strong>Damage costs:</strong> Only affected assets take damage. Anything below the threshold is excluded from both the damage cost and the average damage ratio, even where the vulnerability curve gives a positive damage ratio at its intensity.
                </li>
              </ul>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Tip:</strong> Adjust the slider to explore different risk scenarios. A lower threshold means more infrastructure will be considered at risk — and, with vulnerability analysis enabled, a higher total damage cost.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Palette Info Dialog */}
      <Dialog open={paletteInfoOpen} onOpenChange={setPaletteInfoOpen}>
        <DialogContent onClose={() => setPaletteInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Color Palette & Opacity</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Color Palette:</p>
              <p className="text-sm text-gray-600">
                The color palette determines how hazard intensity values are visualized on the map. Different palettes are optimized for different types of data:
              </p>
              <ul className="text-sm text-gray-600 space-y-1 mt-2 ml-4 list-disc">
                <li><strong>Viridis:</strong> Perceptually uniform, colorblind-friendly (blue to yellow)</li>
                <li><strong>Magma:</strong> Black to purple to pink to yellow</li>
                <li><strong>Inferno:</strong> Black to red to yellow</li>
                <li><strong>Plasma:</strong> Purple to pink to yellow</li>
                <li><strong>Cividis:</strong> Dark to bright, with good contrast</li>
                <li><strong>Turbo:</strong> Rainbow-like spectrum (blue to yellow via red)</li>
              </ul>
              {/* <p className="text-sm text-gray-600 mt-2">
                Colors are automatically scaled from the minimum to maximum hazard values in your dataset. Lower values map to the left side of the palette, higher values to the right.
              </p> */}
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Opacity:</p>
              <p className="text-sm text-gray-600">
                The opacity percentage controls the transparency of the hazard layer.
              </p>
              {/* <ul className="text-sm text-gray-600 space-y-1 mt-2 ml-4 list-disc">
                <li>View the underlying basemap through the hazard layer</li>
                <li>Compare hazard intensity with infrastructure features</li>
                <li>Adjust visibility for better visual clarity</li>
              </ul> */}
              <p className="text-sm text-gray-600 mt-2">
                <strong>0%</strong> = fully transparent (hazard layer invisible)<br />
                <strong>100%</strong> = fully opaque (hazard layer completely covers basemap)
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Tip:</strong> Experiment with different palettes and opacity levels to find the best visualization for your analysis. The hazard layer is displayed between the basemap and infrastructure layers.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Vulnerability Curve Info Dialog */}
      <Dialog open={vulnerabilityCurveInfoOpen} onOpenChange={setVulnerabilityCurveInfoOpen}>
        <DialogContent onClose={() => setVulnerabilityCurveInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Vulnerability Curve</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm text-gray-700">
                Upload a CSV file containing a vulnerability curve that defines the relationship between hazard intensity and the proportion of infrastructure destroyed.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">CSV Format:</p>
              <p className="text-sm text-gray-600 mb-2">
                The CSV file must contain either <strong>2 columns</strong> (standard) or <strong>4 columns</strong> (with uncertainty bounds). A header row is optional.
              </p>
              <p className="text-sm font-medium text-gray-700 mb-1">Standard (2 columns):</p>
              <ul className="text-sm text-gray-600 space-y-1 ml-4 list-disc mb-3">
                <li><strong>Column 1:</strong> Hazard intensity (numeric)</li>
                <li><strong>Column 2:</strong> Proportion destroyed (0–1)</li>
              </ul>
              <p className="text-sm font-medium text-gray-700 mb-1">With uncertainty bands (4 columns):</p>
              <ul className="text-sm text-gray-600 space-y-1 ml-4 list-disc">
                <li><strong>Column 1:</strong> Hazard intensity (numeric)</li>
                <li><strong>Column 2:</strong> Lower-bound proportion destroyed (0–1)</li>
                <li><strong>Column 3:</strong> Central proportion destroyed (0–1)</li>
                <li><strong>Column 4:</strong> Upper-bound proportion destroyed (0–1)</li>
              </ul>
              <p className="text-sm text-gray-500 mt-2">
                When 4 columns are provided, error bars appear on the Damage Cost bar and the map popup shows the damage range per feature.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">How it works:</p>
              <p className="text-sm text-gray-600">
                The vulnerability curve is interpolated to calculate the proportion of each infrastructure feature that would be destroyed based on the hazard intensity at that location. This proportion is then multiplied by the replacement value to calculate total damage costs.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Dataset with some curves:</p>
              <p className="text-sm text-gray-600 mb-2">
                Navigate to{' '}
                <a
                  href="https://github.com/SebKrantz/VulnerabilityCurves"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline break-all"
                >
                  https://github.com/SebKrantz/VulnerabilityCurves
                </a>
                , which contains curves from Nirandjan, S. et al.:{' '}
                <em>A spatially-explicit harmonized global dataset of critical infrastructure vulnerability curves</em>, Nat. Hazards Earth Syst. Sci., 24, 4341–4369, 2024.{' '}
                <a
                  href="https://doi.org/10.5194/nhess-24-4341-2024"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  DOI: 10.5194/nhess-24-4341-2024
                </a>
              </p>
              <p className="text-sm font-bold text-gray-700 mb-1">Downloading curves (no GitHub account needed):</p>
              <ol className="text-sm text-gray-600 space-y-1.5 ml-4 list-decimal">
                <li>On the repository page, click <strong>Code</strong>, then <strong>Download ZIP</strong>, and extract the archive on your computer.</li>
                <li>
                  Use the CSV files in the{' '}
                  <code className="text-xs bg-gray-100 px-1 rounded">curves_csv</code>{' '}
                  folder when uploading a vulnerability curve here.
                </li>
                <li>
                  Make sure the intensity unit in the curve matches the hazard layer you selected.
                </li>
              </ol>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Note:</strong> The curve is linearly interpolated. Values below the minimum intensity in the curve use the first proportion value, and values above the maximum use the last proportion value.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Replacement Value Info Dialog */}
      <Dialog open={replacementValueInfoOpen} onOpenChange={setReplacementValueInfoOpen}>
        <DialogContent onClose={() => setReplacementValueInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Replacement Value</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm text-gray-700">
                Enter the monetary cost of replacing the infrastructure asset in US dollars (USD). All damage costs shown in the chart, map popups, and exports are reported in USD.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">For Point features:</p>
              <p className="text-sm text-gray-600">
                The replacement value represents the total cost of the asset. Damage cost is calculated as: <strong>replacement_value × damage ratio</strong>
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">For LineString features:</p>
              <p className="text-sm text-gray-600">
                The replacement value represents the cost per meter of the line. Damage cost is calculated as: <strong>replacement_value × damage ratio × length (in meters)</strong>
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Tip:</strong> The total damage cost shown in the barchart is the sum of all individual feature damage costs.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Vulnerability Analysis Info Dialog */}
      <Dialog open={vulnerabilityAnalysisInfoOpen} onOpenChange={setVulnerabilityAnalysisInfoOpen}>
        <DialogContent onClose={() => setVulnerabilityAnalysisInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Vulnerability Analysis</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <p className="text-sm text-gray-700">
                Enable vulnerability analysis to calculate the proportion of infrastructure destroyed based on hazard intensity and estimate total damage costs.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">What it does:</p>
              <p className="text-sm text-gray-600">
                When enabled, the analysis applies a vulnerability curve to determine how much of each infrastructure feature would be destroyed at different hazard intensities. Combined with a replacement value, this calculates monetary damage costs.
              </p>
              <p className="text-sm text-gray-600 mt-2">
                The hazard intensity threshold still applies: damage is calculated for <strong>affected assets only</strong>. Raising the threshold narrows the set of assets that take damage and so lowers the total damage cost. What is not exposed suffers no damage, whatever the curve says at that intensity.
              </p>
            </div>
            <div>
              <p className="text-sm font-bold text-gray-700 mb-1">Display changes:</p>
              <ul className="text-sm text-gray-600 space-y-1 ml-4 list-disc">
                <li><strong>Map viewer:</strong> The map display now shows the damage ratio in percentage points - with a color legend in the bottom left corner.</li>
                <li><strong>Barchart:</strong> Shows damage cost, exposure percentage, and average damage ratio instead of just affected/unaffected counts.</li>
                <li><strong>Map popups:</strong> When clicking on affected lines or points, popups will display damage ratio (proportion destroyed) and damage cost in addition to exposure information.</li>
              </ul>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-300">
              <p className="text-xs text-gray-500">
                <strong>Note:</strong> You must upload a vulnerability curve CSV and enter a replacement value for the analysis to run. The vulnerability curve defines the relationship between hazard intensity and proportion destroyed.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Analysis Results Info Dialog */}
      <Dialog open={analysisResultsInfoOpen} onOpenChange={setAnalysisResultsInfoOpen}>
        <DialogContent onClose={() => setAnalysisResultsInfoOpen(false)}>
          <DialogHeader>
            <DialogTitle>Analysis Results</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            {vulnerabilityAnalysisEnabled && analysisResult?.summary.total_damage_cost !== undefined ? (
              <>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Vulnerability Analysis Mode</p>
                  <p className="text-sm text-gray-700">
                    The barchart displays three metrics:
                  </p>
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Damage Cost (left axis):</p>
                  <p className="text-sm text-gray-600">
                    The total monetary cost of damage to infrastructure, calculated by applying the vulnerability curve and replacement value to all affected features.
                  </p>
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Exposure (right axis):</p>
                  <p className="text-sm text-gray-600">
                    The percentage of infrastructure assets (for points) or meters (for lines) that are exposed to hazards above the intensity threshold.
                  </p>
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Damage ratio (right axis):</p>
                  <p className="text-sm text-gray-600">
                    The average damage ratio (proportion destroyed) of exposed infrastructure. This is calculated <strong>only across exposed assets</strong> (those above the intensity threshold). For lines, this is a length-weighted average.
                  </p>
                </div>
                <div className="mt-4 pt-3 border-t border-gray-300">
                  <p className="text-xs text-gray-500">
                    <strong>Important:</strong> Both exposure and damage ratio depend on the <strong>hazard intensity threshold</strong>. Only infrastructure exposed to hazards above the threshold is considered in these calculations.
                  </p>
                </div>
              </>
            ) : (
              <>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Exposure Analysis Mode</p>
                  <p className="text-sm text-gray-700">
                    The barchart displays the count (for points) or length in meters (for lines) of infrastructure that is affected vs. unaffected by the hazard.
                  </p>
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Affected (red bar):</p>
                  <p className="text-sm text-gray-600">
                    Infrastructure exposed to hazard intensities at or above the intensity threshold.
                  </p>
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-700 mb-1">Unaffected (green bar):</p>
                  <p className="text-sm text-gray-600">
                    Infrastructure exposed to hazard intensities below the intensity threshold.
                  </p>
                </div>
                <div className="mt-4 pt-3 border-t border-gray-300">
                  <p className="text-xs text-gray-500">
                    <strong>Important:</strong> The classification of infrastructure as affected or unaffected depends entirely on the <strong>hazard intensity threshold</strong>. Adjusting the threshold will change which features are considered exposed.
                  </p>
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

