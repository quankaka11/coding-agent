import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Chữ đi kèm bản build: công cụ chạy localhost, không nên phụ thuộc mạng để
// hiện đúng dấu tiếng Việt. `wdth` là trục mà từ phán quyết dùng tới.
import '@fontsource-variable/archivo/wdth.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'

import './styles/tokens.css'
import './styles/app.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
