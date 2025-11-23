import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import PersonalizationPage from './pages/PersonalizationPage'
import PreviewPage from './pages/PreviewPage'

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-purple-50">
        <nav className="bg-white/70 backdrop-blur sticky top-0 z-10">
          <div className="mx-auto max-w-7xl px-6 py-3 flex items-center gap-6">
            <Link to="/" className="text-purple-700 font-semibold">Персонализация</Link>
            <Link to="/preview" className="text-purple-700 font-semibold">Предпросмотр</Link>
          </div>
        </nav>
        <Routes>
          <Route path="/" element={<PersonalizationPage />} />
          <Route path="/preview" element={<PreviewPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
