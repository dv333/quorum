import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Demo from './components/Demo'
import './index.css'
import { applyTheme, getTheme } from './theme'

applyTheme(getTheme())

// #demo[/name] replays a recorded conundrum and never talks to the backend
const demoName = () => {
  const m = window.location.hash.match(/^#demo(?:\/([\w-]+))?/)
  return m ? m[1] || '' : null
}

function Root() {
  const [demo, setDemo] = useState(demoName)
  useEffect(() => {
    const onHash = () => setDemo(demoName())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return demo === null ? <App /> : <Demo name={demo} />
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
