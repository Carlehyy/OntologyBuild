import { Routes, Route } from "react-router";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Ontology from "./pages/Ontology";
import Rules from "./pages/Rules";
import GraphBrowser from "./pages/GraphBrowser";
import Extraction from "./pages/Extraction";
import Inference from "./pages/Inference";
import Feedback from "./pages/Feedback";
import Users from "./pages/Users";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/ontology" element={<Ontology />} />
        <Route path="/rules" element={<Rules />} />
        <Route path="/graph" element={<GraphBrowser />} />
        <Route path="/extraction" element={<Extraction />} />
        <Route path="/inference" element={<Inference />} />
        <Route path="/feedback" element={<Feedback />} />
        <Route path="/users" element={<Users />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  );
}
