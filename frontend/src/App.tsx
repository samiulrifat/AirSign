import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppProvider } from './lib/app'
import Home from './pages/Home'
import Enroll from './pages/Enroll'
import Pay from './pages/Pay'
import Pos from './pages/Pos'
import Checkin from './pages/Checkin'
import How from './pages/How'
import Lab from './pages/Lab'
import WalletReturn from './pages/WalletReturn'

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/enroll" element={<Enroll />} />
          <Route path="/pay" element={<Pay />} />
          <Route path="/pay/:id" element={<Pay />} />
          <Route path="/c" element={<Checkin />} />
          <Route path="/pos" element={<Pos />} />
          <Route path="/how" element={<How />} />
          <Route path="/lab" element={<Lab />} />
          <Route path="/wallet/return" element={<WalletReturn />} />
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  )
}
