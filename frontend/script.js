const API_URL = "http://127.0.0.1:8000";

const authShell = document.getElementById("authShell");
const appShell = document.getElementById("appShell");
const loginPage = document.getElementById("loginPage");
const registerPage = document.getElementById("registerPage");
const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const passwordInput = document.getElementById("registerPassword");

function setMessage(elementId, text, kind = "") {
    const element = document.getElementById(elementId);
    element.textContent = text;
    element.className = `message ${kind}`.trim();
}

function showAuthPage(page) {
    authShell.classList.remove("hidden");
    appShell.classList.add("hidden");
    loginPage.classList.toggle("hidden", page !== "login");
    registerPage.classList.toggle("hidden", page !== "register");
}

function authHeaders(extra = {}) {
    const token = localStorage.getItem("crystalscout_token");
    return { ...extra, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_URL}${path}`, {
        ...options,
        headers: authHeaders(options.headers || {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
}

function formatSigma(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toExponential(3)} S/cm` : "Unavailable";
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
}

function showApp(name = localStorage.getItem("crystalscout_name") || "Researcher") {
    authShell.classList.add("hidden");
    appShell.classList.remove("hidden");
    const cleanName = name.trim() || "Researcher";
    document.getElementById("userName").textContent = cleanName;
    document.getElementById("userInitial").textContent = cleanName.charAt(0).toUpperCase();
    document.getElementById("welcomeTitle").textContent = `Welcome to CrystalScout, ${cleanName.split(/\s+/)[0]}`;
    navigateTo(location.hash.slice(1) || "dashboard");
    loadDashboard();
}

function navigateTo(page) {
    const validPages = ["dashboard", "screen", "history", "candidates", "models"];
    const selectedPage = validPages.includes(page) ? page : "dashboard";
    document.querySelectorAll("[data-view]").forEach((view) => {
        view.classList.toggle("hidden", view.dataset.view !== selectedPage);
    });
    document.querySelectorAll(".nav-link[data-page]").forEach((link) => {
        const active = link.dataset.page === selectedPage;
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
    });
    const labels = {
        dashboard: "Dashboard", screen: "Screen a material", history: "Screening history",
        candidates: "Candidate ranking", models: "Models"
    };
    document.getElementById("pageCrumb").textContent = labels[selectedPage];
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("menuToggle").setAttribute("aria-expanded", "false");
    if (selectedPage === "history") loadHistory();
    if (selectedPage === "candidates") loadCandidates();
    if (selectedPage === "screen") loadModelStatus();
}

document.getElementById("showRegister").addEventListener("click", (event) => {
    event.preventDefault();
    setMessage("registerMessage", "");
    showAuthPage("register");
});

document.getElementById("showLogin").addEventListener("click", (event) => {
    event.preventDefault();
    setMessage("loginMessage", "");
    showAuthPage("login");
});

document.querySelectorAll(".toggle-password").forEach((button) => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.target);
        const showing = input.type === "password";
        input.type = showing ? "text" : "password";
        button.textContent = showing ? "Hide" : "Show";
        button.setAttribute("aria-label", `${showing ? "Hide" : "Show"} password`);
    });
});

const rules = {
    length: document.getElementById("lengthRule"),
    letter: document.getElementById("letterRule"),
    number: document.getElementById("numberRule"),
    special: document.getElementById("specialRule")
};

function updateRule(element, isValid, label) {
    element.classList.toggle("valid", isValid);
    element.textContent = `${isValid ? "✓" : "○"} ${label}`;
}

function checkPassword() {
    const password = passwordInput.value;
    const checks = {
        length: password.length >= 8,
        letter: /[A-Za-z]/.test(password),
        number: /[0-9]/.test(password),
        special: /[^A-Za-z0-9]/.test(password)
    };
    updateRule(rules.length, checks.length, "At least 8 characters");
    updateRule(rules.letter, checks.letter, "At least one alphabet");
    updateRule(rules.number, checks.number, "At least one number");
    updateRule(rules.special, checks.special, "At least one special character");
    return Object.values(checks).every(Boolean);
}

passwordInput.addEventListener("input", checkPassword);

registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("registerName").value.trim();
    const email = document.getElementById("registerEmail").value.trim();
    const password = passwordInput.value;
    const confirmPassword = document.getElementById("confirmPassword").value;
    if (!checkPassword()) {
        setMessage("registerMessage", "Please satisfy all password requirements.", "error");
        return;
    }
    if (password !== confirmPassword) {
        setMessage("registerMessage", "Passwords do not match.", "error");
        return;
    }
    setMessage("registerMessage", "Creating account...");
    try {
        const data = await apiRequest("/api/auth/register", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, email, password })
        });
        setMessage("registerMessage", data.message || "Account created. Please log in.", "success");
        document.getElementById("loginEmail").value = email;
        registerForm.reset();
        checkPassword();
        window.setTimeout(() => showAuthPage("login"), 1100);
    } catch (error) {
        setMessage("registerMessage", error.message, "error");
    }
});

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;
    setMessage("loginMessage", "Logging in...");
    try {
        const data = await apiRequest("/api/auth/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });
        localStorage.setItem("crystalscout_token", data.access_token);
        localStorage.setItem("crystalscout_name", data.name || "Researcher");
        loginForm.reset();
        showApp(data.name || "Researcher");
    } catch (error) {
        setMessage("loginMessage", error.message, "error");
    }
});

async function loadModelStatus() {
    const notice = document.getElementById("modelNotice");
    try {
        const status = await apiRequest("/api/screening/model-status");
        notice.textContent = status.ready
            ? `${status.model_name} is ready (${status.feature_count} inputs).`
            : status.message;
        notice.classList.toggle("notice-error", !status.ready);
    } catch (error) {
        notice.textContent = error.message;
        notice.classList.add("notice-error");
    }
}

function renderPrediction(data) {
    const panel = document.getElementById("predictionResult");
    panel.innerHTML = `<p class="eyebrow">SCREENING ESTIMATE</p><h2>${escapeHTML(data.formula)}</h2><p class="prediction-value">${formatSigma(data.predicted_conductivity_s_cm)}</p><p class="prediction-detail">Predicted log₁₀ conductivity: ${Number(data.predicted_log10_conductivity).toFixed(3)} S/cm</p><p class="prediction-caveat">This model's held-out MAE is about 1.17 log₁₀ units (about 15× in conductivity). Treat this as a ranking estimate, not a precise measurement.</p>`;
    panel.classList.remove("hidden");
}

document.getElementById("mpForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    const previous = button.innerHTML;
    button.disabled = true;
    button.textContent = "Predicting…";
    try {
        const id = document.getElementById("materialId").value.trim();
        renderPrediction(await apiRequest(`/api/screening/predict-material/${encodeURIComponent(id)}`, { method: "POST" }));
        loadDashboard();
    } catch (error) {
        const notice = document.getElementById("modelNotice");
        notice.textContent = error.message;
        notice.classList.add("notice-error");
    } finally {
        button.disabled = false;
        button.innerHTML = previous;
    }
});

document.getElementById("cifForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const previous = button.innerHTML;
    button.disabled = true;
    button.textContent = "Reading CIF…";
    try {
        const data = new FormData(form);
        renderPrediction(await apiRequest("/api/screening/predict-cif", { method: "POST", body: data }));
        loadDashboard();
    } catch (error) {
        const notice = document.getElementById("modelNotice");
        notice.textContent = error.message;
        notice.classList.add("notice-error");
    } finally {
        button.disabled = false;
        button.innerHTML = previous;
    }
});

function renderHistoryRows(rows) {
    if (!rows.length) {
        return `<div class="empty-inline"><span class="empty-icon">◷</span><h2>No saved screenings yet</h2><p>Predictions you run will appear here.</p></div>`;
    }
    return `<div class="table-wrap"><table><thead><tr><th>Material</th><th>Source</th><th>Predicted conductivity</th><th>Date</th></tr></thead><tbody>${rows.map((row) => `<tr><td><span class="material-name">${escapeHTML(row.formula)}</span><br><span class="row-subtitle">${escapeHTML(row.source_identifier || "")}</span></td><td>${escapeHTML(row.source.replaceAll("_", " "))}</td><td>${formatSigma(row.predicted_s_cm)}</td><td>${new Date(`${row.created_at.replace(" ", "T")}Z`).toLocaleString()}</td></tr>`).join("")}</tbody></table></div>`;
}

async function fetchHistory() {
    return apiRequest("/api/screening/history?limit=100");
}

async function loadHistory() {
    const host = document.getElementById("historyContent");
    host.innerHTML = `<p class="form-hint">Loading your history…</p>`;
    try {
        const data = await fetchHistory();
        host.innerHTML = renderHistoryRows(data.results || []);
    } catch (error) {
        host.innerHTML = `<p class="inline-notice notice-error">${escapeHTML(error.message)}</p>`;
    }
}

async function loadDashboard() {
    try {
        const [history, model] = await Promise.all([
            fetchHistory(),
            apiRequest("/api/screening/model-status")
        ]);
        const rows = history.results || [];
        document.getElementById("predictionCount").textContent = String(rows.length);
        const best = rows.reduce((current, item) => Math.max(current, Number(item.predicted_s_cm)), 0);
        document.getElementById("highestPrediction").textContent = best ? formatSigma(best) : "—";
        const statusFootnote = document.querySelector("#dashboardView .model-card .stat-footnote");
        if (statusFootnote && !model.ready) statusFootnote.textContent = "Train the model to enable predictions";
        const recent = document.querySelector("#dashboardView .recent-panel .table-wrap");
        if (recent) recent.innerHTML = renderHistoryRows(rows.slice(0, 5));
    } catch (error) {
        document.getElementById("predictionCount").textContent = "—";
        document.getElementById("highestPrediction").textContent = "—";
    }
}

async function loadCandidates() {
    const host = document.getElementById("candidateContent");
    const notice = document.getElementById("candidateNotice");
    host.innerHTML = "";
    notice.textContent = "Connecting to the Materials Project and ranking eligible lithium materials… This may take a few minutes.";
    notice.classList.remove("notice-error");
    try {
        const data = await apiRequest("/api/screening/candidates?limit=50");
        notice.textContent = `Scanned ${data.candidates_scanned.toLocaleString()} eligible candidates; showing the top ${data.results.length}. ${data.feature_failures} structures could not be featurized.`;
        host.innerHTML = `<table><thead><tr><th>Rank</th><th>Material</th><th>Predicted conductivity</th><th>Band gap</th><th>Energy above hull</th></tr></thead><tbody>${data.results.map((item) => `<tr><td>${item.rank}</td><td><span class="material-name">${escapeHTML(item.formula)}</span><br><span class="row-subtitle">${escapeHTML(item.material_id)}</span></td><td>${formatSigma(item.predicted_conductivity_s_cm)}</td><td>${Number(item.band_gap_ev).toFixed(2)} eV</td><td>${Number(item.energy_above_hull_ev_atom).toFixed(3)} eV/atom</td></tr>`).join("")}</tbody></table>`;
    } catch (error) {
        notice.textContent = error.message;
        notice.classList.add("notice-error");
    }
}

document.getElementById("refreshCandidates").addEventListener("click", loadCandidates);

document.querySelectorAll("[data-page]").forEach((link) => {
    link.addEventListener("click", (event) => {
        event.preventDefault();
        const page = link.dataset.page;
        if (appShell.classList.contains("hidden")) return;
        if (location.hash !== `#${page}`) history.pushState(null, "", `#${page}`);
        navigateTo(page);
    });
});

window.addEventListener("popstate", () => {
    if (!appShell.classList.contains("hidden")) navigateTo(location.hash.slice(1));
});

document.getElementById("menuToggle").addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    const isOpen = sidebar.classList.toggle("open");
    document.getElementById("menuToggle").setAttribute("aria-expanded", String(isOpen));
});

document.getElementById("logoutButton").addEventListener("click", () => {
    localStorage.removeItem("crystalscout_token");
    localStorage.removeItem("crystalscout_name");
    loginForm.reset();
    setMessage("loginMessage", "");
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    showAuthPage("login");
});

if (localStorage.getItem("crystalscout_token")) showApp();
else showAuthPage("login");
