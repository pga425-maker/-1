import { Navigate, Route, Routes } from 'react-router-dom'
import { GameProvider } from './store'
import Onboard from './pages/Onboard'
import Play from './pages/Play'
import StockDetail from './pages/StockDetail'
import News from './pages/News'
import Market from './pages/Market'
import Rank from './pages/Rank'
import Result from './pages/Result'
import Board from './pages/Board'
import Admin from './pages/Admin'

/**
 * 보드와 관리자 화면은 참가자 상태(WebSocket, 토큰)가 필요 없다.
 * 불필요한 연결을 만들지 않도록 GameProvider 밖에 둔다.
 */
export default function App() {
  return (
    <Routes>
      <Route path="/board" element={<Board />} />
      <Route path="/admin" element={<Admin />} />
      <Route path="/onboard" element={<Onboard />} />
      <Route
        path="*"
        element={
          <GameProvider>
            <Routes>
              <Route path="/" element={<Navigate to="/play" replace />} />
              <Route path="/play" element={<Play />} />
              <Route path="/stock/:symbol" element={<StockDetail />} />
              <Route path="/market" element={<Market />} />
              <Route path="/news" element={<News />} />
              <Route path="/rank" element={<Rank />} />
              <Route path="/result" element={<Result />} />
              <Route path="*" element={<Navigate to="/play" replace />} />
            </Routes>
          </GameProvider>
        }
      />
    </Routes>
  )
}
