import { useEffect, useState } from 'react'
import './App.css'

interface HealthResponse {
  status: string
  service: string
  environment: string
  model_loaded: boolean
  stage: string
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    async function checkBackend() {
      try {
        const response = await fetch(
          `${API_BASE_URL}/health`,
          { signal: controller.signal }
        )
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const data: HealthResponse = await response.json()
        setHealth(data)
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(
            err instanceof Error ? err.message : 'Unknown error'
          )
        }
      }
    }
    checkBackend()
    return () => controller.abort()
  }, [])

  return (
    <main className="app-shell">
      <div className="header">
        <div className="logo">🚲 BIKE PULSE</div>
        <div className="version">AWS MLOps Platform</div>
      </div>

      <section className="hero">
        <span className="eyebrow">
          SEOUL BIKE SHARING DEMAND
        </span>
        <h1>
          Predict the Pulse
          <br />
          <span>of Seoul.</span>
        </h1>
        <p>
          서울 따릉이 대여량 예측 및<br />
          AWS 기반 MLOps 통합 플랫폼
        </p>
      </section>

      <section className="status-panel">
        <h2>System Connection</h2>
        
        {health && (
          <div className="status-success">
            <div className="status-title">
              ✅ Backend Connected
            </div>
            <p>Service: {health.service}</p>
            <p>Environment: {health.environment}</p>
            <p>API Status: {health.status}</p>
            <p>Stage: {health.stage}</p>
            <p>
              ML Model: {' '}
              {health.model_loaded ? 'Loaded' : 'Not deployed yet'}
            </p>
          </div>
        )}

        {error && (
          <div className="status-error">
            ❌ Backend Connection Failed: {error}
          </div>
        )}

        {!health && !error && (
          <p>Connecting to Backend...</p>
        )}
      </section>

      <footer>
        Amazon ECS · AWS Step Functions · Python ML
      </footer>
    </main>
  )
}

export default App
