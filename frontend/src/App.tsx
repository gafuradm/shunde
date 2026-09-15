import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import DocumentReaderPage from "./pages/DocumentReaderPage";
import DocumentsPage from "./pages/DocumentsPage";
import HomePage from "./pages/HomePage";
import LecturesPage from "./pages/LecturesPage";
import LiveRoomPage from "./pages/LiveRoomPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/documents/:docId" element={<DocumentReaderPage />} />
        <Route path="/lectures" element={<LecturesPage />} />
        <Route path="/live/:code" element={<LiveRoomPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
