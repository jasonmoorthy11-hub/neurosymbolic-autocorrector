import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ReplayProvider } from './replay/ReplayContext'
import { REPLAY } from './replay/mode'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {REPLAY ? (
      <ReplayProvider>
        <App />
      </ReplayProvider>
    ) : (
      <App />
    )}
  </StrictMode>,
)
