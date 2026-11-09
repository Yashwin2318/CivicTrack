
import { useState } from "react";
import {
  LayoutDashboard,
  MapPin,
  ClipboardList,
  UserRound,
  Bell,
  Search,
  Plus,
  Menu,
  Settings,
  LogOut,
  Clock,
  CheckCircle2,
  AlertTriangle,
  ArrowUpRight,
  Building2,
  ChevronDown,
  MapPinned,
} from "lucide-react";

import "./App.css";

const initialComplaints = [
  {
    id: "CT-2026-001",
    title: "Large pothole near main road",
    category: "Roads",
    location: "Indiranagar, Bengaluru",
    status: "In Progress",
    priority: "High",
    date: "31 Oct 2026",
  },
  {
    id: "CT-2026-002",
    title: "Garbage not collected",
    category: "Sanitation",
    location: "Whitefield, Bengaluru",
    status: "Pending",
    priority: "Medium",
    date: "30 Oct 2026",
  },
  {
    id: "CT-2026-003",
    title: "Streetlight not working",
    category: "Electricity",
    location: "Koramangala, Bengaluru",
    status: "Resolved",
    priority: "Low",
    date: "29 Oct 2026",
  },
];

function App() {
  const [activePage, setActivePage] = useState("Dashboard");
  const [complaints, setComplaints] = useState(initialComplaints);
  const [showForm, setShowForm] = useState(false);
  const [search, setSearch] = useState("");

  const [form, setForm] = useState({
    title: "",
    category: "Roads",
    location: "",
    description: "",
  });

  const stats = [
    {
      label: "Total complaints",
      value: complaints.length,
      icon: ClipboardList,
      color: "blue",
    },
    {
      label: "Pending complaints",
      value: complaints.filter((c) => c.status === "Pending").length,
      icon: Clock,
      color: "orange",
    },
    {
      label: "In progress",
      value: complaints.filter((c) => c.status === "In Progress").length,
      icon: AlertTriangle,
      color: "purple",
    },
    {
      label: "Resolved complaints",
      value: complaints.filter((c) => c.status === "Resolved").length,
      icon: CheckCircle2,
      color: "green",
    },
  ];

  const filteredComplaints = complaints.filter((complaint) =>
    `${complaint.id} ${complaint.title} ${complaint.location}`
      .toLowerCase()
      .includes(search.toLowerCase())
  );

  function submitComplaint(e) {
    e.preventDefault();

    const newComplaint = {
      id: `CT-2026-${String(complaints.length + 1).padStart(3, "0")}`,
      title: form.title,
      category: form.category,
      location: form.location,
      status: "Pending",
      priority: "Medium",
      date: new Date().toLocaleDateString("en-IN"),
    };

    setComplaints((previous) => [newComplaint, ...previous]);
    setForm({
      title: "",
      category: "Roads",
      location: "",
      description: "",
    });
    setShowForm(false);
    setActivePage("Complaints");
  }

  const navigation = [
    { label: "Dashboard", icon: LayoutDashboard },
    { label: "My Complaints", icon: ClipboardList },
    { label: "Track Complaint", icon: MapPin },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">
            <Building2 size={23} />
          </div>
          <div>
            <h2>CivicTrack</h2>
            <span>CITIZEN PORTAL</span>
          </div>
        </div>

        <div className="sidebar-section">MENU</div>

        <nav>
          {navigation.map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activePage === label ? "active" : ""}`}
              onClick={() => setActivePage(label)}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <button className="nav-item">
            <Settings size={19} />
            <span>Settings</span>
          </button>

          <div className="profile">
            <div className="avatar">CT</div>
            <div className="profile-info">
              <strong>Citizen User</strong>
              <span>Resident account</span>
            </div>
            <LogOut size={17} />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Citizen Portal</span>
            <span>/</span>
            <strong>{activePage}</strong>
          </div>

          <div className="topbar-right">
            <span className="location-label">
              <MapPin size={15} /> Bengaluru
            </span>
            <button className="notification-button">
              <Bell size={19} />
              <span className="notification-dot"></span>
            </button>
          </div>
        </header>

        <section className="page-content">
          <div className="welcome-section">
            <div>
              <p className="eyebrow">CIVIC SERVICES PLATFORM</p>
              <h1>
                {activePage === "Dashboard"
                  ? "Good evening, Citizen"
                  : activePage}
              </h1>
              <p className="subtitle">
                Report civic issues, track progress and help build a better city.
              </p>
            </div>

            <button className="primary-button" onClick={() => setShowForm(true)}>
              <Plus size={19} />
              Report an Issue
            </button>
          </div>

          {activePage === "Dashboard" && (
            <>
              <div className="stats-grid">
                {stats.map(({ label, value, icon: Icon, color }) => (
                  <div className="stat-card" key={label}>
                    <div className={`stat-icon ${color}`}>
                      <Icon size={21} />
                    </div>
                    <p>{label}</p>
                    <h2>{value}</h2>
                  </div>
                ))}
              </div>

              <div className="dashboard-grid">
                <div className="panel complaint-panel">
                  <div className="panel-heading">
                    <div>
                      <h3>Recent complaints</h3>
                      <p>Monitor your submitted civic issues</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => setActivePage("Complaints")}
                    >
                      View all <ArrowUpRight size={16} />
                    </button>
                  </div>

                  <div className="complaint-list">
                    {complaints.slice(0, 4).map((complaint) => (
                      <div className="complaint-row" key={complaint.id}>
                        <div className="complaint-symbol">
                          <MapPinned size={19} />
                        </div>
                        <div className="complaint-details">
                          <strong>{complaint.title}</strong>
                          <span>
                            {complaint.id} · {complaint.location}
                          </span>
                        </div>
                        <span
                          className={`status ${complaint.status
                            .toLowerCase()
                            .replace(" ", "-")}`}
                        >
                          {complaint.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="panel action-panel">
                  <div className="panel-heading">
                    <div>
                      <h3>Need assistance?</h3>
                      <p>Help us improve your neighbourhood.</p>
                    </div>
                  </div>

                  <div className="action-illustration">
                    <Building2 size={49} />
                  </div>

                  <h3>See an issue? Report it.</h3>
                  <p>
                    Submit civic issues with location details and help the
                    authorities take action.
                  </p>

                  <button
                    className="secondary-button"
                    onClick={() => setShowForm(true)}
                  >
                    Submit a complaint <ArrowUpRight size={17} />
                  </button>
                </div>
              </div>
            </>
          )}

          {activePage !== "Dashboard" && (
            <div className="panel">
              <div className="panel-heading">
                <div>
                  <h3>
                    {activePage === "Track Complaint"
                      ? "Track your complaints"
                      : "All complaints"}
                  </h3>
                  <p>Search by complaint ID, title or location.</p>
                </div>
                <div className="search-box">
                  <Search size={17} />
                  <input
                    placeholder="Search complaints..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>

              <div className="complaint-list">
                {filteredComplaints.map((complaint) => (
                  <div className="complaint-row" key={complaint.id}>
                    <div className="complaint-symbol">
                      <MapPinned size={19} />
                    </div>
                    <div className="complaint-details">
                      <strong>{complaint.title}</strong>
                      <span>
                        {complaint.id} · {complaint.location}
                      </span>
                    </div>
                    <span
                      className={`status ${complaint.status
                        .toLowerCase()
                        .replace(" ", "-")}`}
                    >
                      {complaint.status}
                    </span>
                  </div>
                ))}
                {filteredComplaints.length === 0 && (
                  <p className="empty-state">No complaints found.</p>
                )}
              </div>
            </div>
          )}
        </section>
      </main>

      {showForm && (
        <div className="modal-overlay">
          <form className="complaint-form" onSubmit={submitComplaint}>
            <div className="form-heading">
              <div>
                <p className="eyebrow">CIVICTRACK SERVICES</p>
                <h2>Report a civic issue</h2>
              </div>
              <button
                type="button"
                className="close-button"
                onClick={() => setShowForm(false)}
              >
                ×
              </button>
            </div>

            <label>Issue title</label>
            <input
              required
              placeholder="e.g. Large pothole near main road"
              value={form.title}
              onChange={(e) =>
                setForm({ ...form, title: e.target.value })
              }
            />

            <label>Complaint category</label>
            <select
              value={form.category}
              onChange={(e) =>
                setForm({ ...form, category: e.target.value })
              }
            >
              <option>Roads</option>
              <option>Sanitation</option>
              <option>Streetlights</option>
              <option>Waterlogging</option>
              <option>Water Supply</option>
              <option>Other</option>
            </select>

            <label>Location</label>
            <input
              required
              placeholder="Enter area, ward or address"
              value={form.location}
              onChange={(e) =>
                setForm({ ...form, location: e.target.value })
              }
            />

            <label>Description</label>
            <textarea
              required
              rows="4"
              placeholder="Describe the civic issue..."
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
            />

            <div className="form-note">
              <MapPin size={17} />
              GPS and photo uploads will be connected in our next phase.
            </div>

            <button type="submit" className="primary-button full-button">
              Submit complaint <ArrowUpRight size={17} />
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

export default App;
