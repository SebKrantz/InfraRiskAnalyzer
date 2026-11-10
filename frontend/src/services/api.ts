import { Hazard, UploadedFile, AnalysisResult } from '../types'

const API_BASE_URL = '/api'

/**
 * Upload a spatial file
 */
export async function uploadFile(file: File): Promise<UploadedFile> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }))
    throw new Error(error.detail || 'Failed to upload file')
  }

  return response.json()
}

/**
 * Delete an upload on the server (its data and cached results). Fire and forget, and
 * `keepalive` so the request still goes out while the page is closing.
 */
export function deleteUpload(fileId: string): void {
  fetch(`${API_BASE_URL}/upload/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
    keepalive: true,
  }).catch(() => {})
}

/**
 * Get list of available hazard layers
 */
export async function getHazards(): Promise<Hazard[]> {
  const response = await fetch(`${API_BASE_URL}/hazards`)

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch hazard layers' }))
    throw new Error(error.detail || 'Failed to fetch hazard layers')
  }

  const data = await response.json()
  return data.hazards || []
}

/**
 * Analyze intersections between infrastructure and hazard. The server reads the
 * hazard raster from its own catalogue, by hazardId.
 */
export async function analyze(
  fileId: string,
  hazardId: string,
  intensityThreshold?: number,
  vulnerabilityCurveFile?: File | null,
  replacementValue?: number | null
): Promise<AnalysisResult> {
  // Use FormData if we have a vulnerability curve file, otherwise use JSON
  const hasVulnerabilityData = vulnerabilityCurveFile !== null && vulnerabilityCurveFile !== undefined
  
  let body: FormData | string
  let headers: HeadersInit
  
  if (hasVulnerabilityData) {
    const formData = new FormData()
    formData.append('file_id', fileId)
    formData.append('hazard_id', hazardId)
    if (intensityThreshold !== undefined) {
      formData.append('intensity_threshold', intensityThreshold.toString())
    }
    formData.append('vulnerability_curve_file', vulnerabilityCurveFile)
    if (replacementValue !== null && replacementValue !== undefined) {
      formData.append('replacement_value', replacementValue.toString())
    }
    body = formData
    headers = {} // Let browser set Content-Type with boundary for FormData
  } else {
    body = JSON.stringify({
      file_id: fileId,
      hazard_id: hazardId,
      intensity_threshold: intensityThreshold,
    })
    headers = {
      'Content-Type': 'application/json',
    }
  }

  const response = await fetch(`${API_BASE_URL}/analyze`, {
    method: 'POST',
    headers,
    body,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Analysis failed' }))
    throw new Error(error.detail || 'Failed to perform analysis')
  }

  return response.json()
}

/**
 * Get hazard raster statistics (min, max)
 */
export async function getHazardStats(hazardId: string): Promise<{ min: number; max: number }> {
  const response = await fetch(`${API_BASE_URL}/hazards/${hazardId}/stats`)

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = typeof body?.detail === 'string' ? body.detail : 'Failed to fetch hazard statistics'
    throw new Error(detail)
  }

  return response.json()
}

/**
 * Export analysis results as a high-resolution PNG barchart
 */
export async function exportBarchart(
  fileId: string,
  hazardId: string,
  intensityThreshold?: number
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/export/barchart`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      file_id: fileId,
      hazard_id: hazardId,
      intensity_threshold: intensityThreshold,
    }),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Export failed' }))
    throw new Error(error.detail || 'Failed to export barchart')
  }

  // Get filename from Content-Disposition header or generate one
  const contentDisposition = response.headers.get('Content-Disposition')
  let filename = `barchart_${fileId.slice(0, 8)}_${hazardId.slice(0, 8)}.png`
  if (contentDisposition) {
    const filenameMatch = contentDisposition.match(/filename="(.+)"/)
    if (filenameMatch) {
      filename = filenameMatch[1]
    }
  }

  // Download the blob
  const blob = await response.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
}

/**
 * Export analysis results as a high-resolution PNG map
 */
export async function exportMap(
  fileId: string,
  hazardId: string,
  colorPalette: string,
  hazardOpacity: number,
  intensityThreshold?: number,
  basemap?: string
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/export/map`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      file_id: fileId,
      hazard_id: hazardId,
      color_palette: colorPalette,
      hazard_opacity: hazardOpacity / 100,
      intensity_threshold: intensityThreshold,
      basemap: basemap,
    }),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Export failed' }))
    throw new Error(error.detail || 'Failed to export map')
  }

  // Get filename from Content-Disposition header or generate one
  const contentDisposition = response.headers.get('Content-Disposition')
  let filename = `map_${fileId.slice(0, 8)}_${hazardId.slice(0, 8)}.png`
  if (contentDisposition) {
    const filenameMatch = contentDisposition.match(/filename="(.+)"/)
    if (filenameMatch) {
      filename = filenameMatch[1]
    }
  }

  // Download the blob
  const blob = await response.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
}

export type DataExportMode = 'csv_points' | 'csv_lines_aggregate' | 'gpkg_lines_split'

/**
 * Export analysis results as CSV or GPKG (data download).
 */
export async function exportData(
  fileId: string,
  hazardId: string,
  mode: DataExportMode,
  intensityThreshold?: number
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/export/data`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      file_id: fileId,
      hazard_id: hazardId,
      intensity_threshold: intensityThreshold,
      mode,
    }),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Export failed' }))
    throw new Error(error.detail || 'Failed to export data')
  }

  const contentDisposition = response.headers.get('Content-Disposition')
  const ext = mode === 'gpkg_lines_split' ? 'gpkg' : 'csv'
  let filename = `data_${fileId.slice(0, 8)}_${hazardId.slice(0, 8)}.${ext}`
  if (contentDisposition) {
    const filenameMatch = contentDisposition.match(/filename="(.+)"/)
    if (filenameMatch) {
      filename = filenameMatch[1]
    }
  }

  const blob = await response.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
}

