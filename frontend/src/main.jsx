import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import FlowChartPage from './FlowChartPage.jsx'
import { applyTheme, savedTheme } from './theme'

// Apply the saved theme before the first render so the page doesn't flash orange.
applyTheme(savedTheme())

const params = new URLSearchParams(window.location.search)
const flowchartRepo = params.get('flowchart')

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {flowchartRepo
      ? <FlowChartPage repo={flowchartRepo} branch={params.get('branch') || 'main'} />
      : <App />}
  </StrictMode>,
)
