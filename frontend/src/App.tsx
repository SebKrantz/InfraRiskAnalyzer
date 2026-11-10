import { useState, useEffect, useRef } from 'react'
import Sidebar from './components/Sidebar'
import MapView from './components/MapView'
import DisclaimerDialog from './components/DisclaimerDialog'
import { Hazard, UploadedFile, AnalysisResult, ColorPalette, Basemap } from './types'
import { getHazards, uploadFile, deleteUpload, analyze as analyzeApi, getHazardStats } from './services/api'

function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [uploadedFile, setUploadedFile] = useState<UploadedFile | null>(null)
  const [selectedHazard, setSelectedHazard] = useState<Hazard | null>(null)
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)
  const [colorPalette, setColorPalette] = useState<ColorPalette>('turbo')
  const [intensityThreshold, setIntensityThreshold] = useState<number>(0)
  const [hazardOpacity, setHazardOpacity] = useState<number>(60)
  const [hazardStats, setHazardStats] = useState<{ min: number; max: number } | null>(null)
  // The layer hazardStats (and so the threshold) belongs to. The analysis waits for it, so a
  // layer change runs one analysis, at the threshold chosen for the new layer.
  const [statsHazardId, setStatsHazardId] = useState<string | null>(null)
  // Unit and minimum of the layer the current threshold was set on
  const thresholdLayer = useRef<{ unit: string; min: number } | null>(null)
  const [basemap, setBasemap] = useState<Basemap>('positron')
  const [hazards, setHazards] = useState<Hazard[]>([])
  const [loadingUpload, setLoadingUpload] = useState(false)
  const [loadingAnalysis, setLoadingAnalysis] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [vulnerabilityAnalysisEnabled, setVulnerabilityAnalysisEnabled] = useState(false)
  const [vulnerabilityCurveFile, setVulnerabilityCurveFile] = useState<File | null>(null)
  const [replacementValue, setReplacementValue] = useState<number | null>(null)

  // Fetch hazards on mount
  useEffect(() => {
    const fetchHazards = async () => {
      try {
        const hazardsData = await getHazards()
        setHazards(hazardsData)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load hazards')
      }
    }
    fetchHazards()
  }, [])

  // Fetch hazard statistics when hazard is selected
  useEffect(() => {
    // A slower answer for a layer selected earlier must not overwrite this one's
    let cancelled = false
    const fetchStats = async () => {
      if (!selectedHazard) {
        setHazardStats(null)
        setStatsHazardId(null)
        setIntensityThreshold(0)
        thresholdLayer.current = null
        return
      }

      try {
        const stats = await getHazardStats(selectedHazard.id)
        if (cancelled) return
        setHazardStats({ min: stats.min, max: stats.max })
        // A threshold set on the previous layer carries over to a layer in the same unit
        // (another return period or scenario) when it lies within the new layer's range.
        // Otherwise the threshold starts at the layer minimum.
        const unit = selectedHazard.unit ?? ''
        const prev = thresholdLayer.current
        setIntensityThreshold((t) =>
          prev && prev.unit === unit && t > prev.min && t >= stats.min && t <= stats.max
            ? t
            : stats.min
        )
        thresholdLayer.current = { unit, min: stats.min }
      } catch (err) {
        if (cancelled) return
        console.error('Failed to fetch hazard statistics:', err)
        // Fallback to default range if stats fail
        setHazardStats({ min: 0, max: 100 })
        thresholdLayer.current = null
      }
      setStatsHazardId(selectedHazard.id)
    }
    fetchStats()
    return () => {
      cancelled = true
    }
  }, [selectedHazard])

  // Handle file upload
  const handleFileUpload = async (file: File) => {
    try {
      setLoadingUpload(true)
      setError(null)
      const uploaded = await uploadFile(file)
      ownUploads.current.add(uploaded.file_id)
      setUploadedFile(uploaded)
      // Clear previous analysis when new file is uploaded
      setAnalysisResult(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload file')
      setUploadedFile(null)
    } finally {
      setLoadingUpload(false)
    }
  }

  // The server keeps a file this browser uploaded only while it is on screen: replacing or
  // clearing it, or leaving the page, deletes it there too. Datasets the page did not upload
  // itself are never deleted from here. (A page kept in the back/forward cache keeps its
  // file; the server's own expiry catches that and any request that never arrives.)
  const ownUploads = useRef(new Set<string>())
  const uploadedFileId = uploadedFile?.file_id
  useEffect(() => {
    if (!uploadedFileId || !ownUploads.current.has(uploadedFileId)) return
    const drop = () => {
      if (ownUploads.current.delete(uploadedFileId)) deleteUpload(uploadedFileId)
    }
    const onPageHide = (e: PageTransitionEvent) => {
      if (!e.persisted) drop()
    }
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      drop()
    }
  }, [uploadedFileId])

  // Handle clear data
  const handleClearData = () => {
    setUploadedFile(null)
    setAnalysisResult(null)
    setError(null)
  }

  // Perform analysis when hazard, threshold, or vulnerability analysis changes
  useEffect(() => {
    if (!uploadedFile || !selectedHazard) {
      setAnalysisResult(null)
      return
    }

    // The threshold for a newly selected layer is set once its statistics arrive
    if (statsHazardId !== selectedHazard.id) return

    // If vulnerability analysis is enabled, require both curve file and a positive replacement value
    const vulnerabilityInputsIncomplete =
      vulnerabilityAnalysisEnabled &&
      (!vulnerabilityCurveFile ||
        replacementValue === null ||
        replacementValue <= 0)

    if (vulnerabilityInputsIncomplete) {
      setAnalysisResult(null)
      return
    }

    const performAnalysis = async () => {
      try {
        setLoadingAnalysis(true)
        setError(null)
        // Use threshold only if it's greater than the min value
        const threshold = hazardStats && intensityThreshold > hazardStats.min 
          ? intensityThreshold 
          : undefined
        
        const result = await analyzeApi(
          uploadedFile.file_id,
          selectedHazard.id,
          selectedHazard.url,
          threshold,
          vulnerabilityAnalysisEnabled ? vulnerabilityCurveFile : null,
          vulnerabilityAnalysisEnabled ? replacementValue : null
        )
        setAnalysisResult(result)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to perform analysis')
        setAnalysisResult(null)
      } finally {
        setLoadingAnalysis(false)
      }
    }

    // Debounce analysis calls
    const timeoutId = setTimeout(performAnalysis, 300)
    return () => clearTimeout(timeoutId)
  }, [uploadedFile, selectedHazard, statsHazardId, intensityThreshold, vulnerabilityAnalysisEnabled, vulnerabilityCurveFile, replacementValue])

  return (
    <div className="flex h-screen w-screen overflow-hidden m-0 p-0">
      <DisclaimerDialog />
      <Sidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        uploadedFile={uploadedFile}
        onFileUpload={handleFileUpload}
        onClearData={handleClearData}
        hazards={hazards}
        selectedHazard={selectedHazard}
        onSelectHazard={setSelectedHazard}
        colorPalette={colorPalette}
        onColorPaletteChange={setColorPalette}
        hazardOpacity={hazardOpacity}
        onHazardOpacityChange={setHazardOpacity}
        intensityThreshold={intensityThreshold}
        onIntensityThresholdChange={setIntensityThreshold}
        hazardStats={hazardStats}
        analysisResult={analysisResult}
        loadingUpload={loadingUpload}
        loadingAnalysis={loadingAnalysis}
        error={error}
        vulnerabilityAnalysisEnabled={vulnerabilityAnalysisEnabled}
        onVulnerabilityAnalysisEnabledChange={setVulnerabilityAnalysisEnabled}
        vulnerabilityCurveFile={vulnerabilityCurveFile}
        onVulnerabilityCurveFileChange={setVulnerabilityCurveFile}
        replacementValue={replacementValue}
        onReplacementValueChange={setReplacementValue}
        basemap={basemap}
      />
      <MapView
        uploadedFile={uploadedFile}
        selectedHazard={selectedHazard}
        analysisResult={analysisResult}
        colorPalette={colorPalette}
        intensityThreshold={intensityThreshold}
        hazardOpacity={hazardOpacity}
        basemap={basemap}
        onBasemapChange={setBasemap}
        loadingAnalysis={loadingAnalysis}
        vulnerabilityAnalysisEnabled={vulnerabilityAnalysisEnabled}
        hazardStats={hazardStats}
      />
    </div>
  )
}

export default App

