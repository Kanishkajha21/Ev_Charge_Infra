# 🚗⚡ VoltGuide — How to Start the Project (Simple Guide)

> This guide explains how to get the project running on your computer, step by step.
> No prior experience needed — just follow along!

---

## 🧱 What This Project Is

**VoltGuide** is a smart EV (Electric Vehicle) charging station finder. Think of it like Google Maps, but specifically for finding the best places to charge your electric car. It has:

- A **Backend** (the brain) — a Python server that handles data, runs AI models, and plans routes
- A **Frontend** (the face) — a React website with maps, charts, and a dashboard you interact with
- A **Database** — PostgreSQL stores all the charging station data, user info, etc.

---

## 📋 Prerequisites — What You Need Installed First

Before starting, make sure these are installed on your Mac:

| Tool | What It Is | How to Install |
|---|---|---|
| **Python 3.12+** | The programming language for the backend | `brew install python` |
| **Node.js 18+** | Runs the frontend JavaScript code | `brew install node` |
| **Docker Desktop** | Runs the database in a container (like a mini virtual machine) | Download from [docker.com](https://docker.com) |
| **Git** | Version control (to download the code) | `brew install git` |

> [!TIP]
> If you don't have `brew` (Homebrew), install it first:
> ```bash
> /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
> ```

---

## 🚀 Step-by-Step Setup

### Step 1: Open Terminal

Open the **Terminal** app on your Mac (search for "Terminal" in Spotlight with `Cmd + Space`).

---

### Step 2: Go to the Project Folder

```bash
cd /Users/arnav/Documents/Major/Major_Project/ev-charging-project
```

> This is where all the project code lives.

---

### Step 3: Start the Database 🗄️

The project uses **PostgreSQL** (a database) running inside Docker. Start it with:

```bash
docker compose up -d
```

**What this does:** Spins up a PostgreSQL database in the background with:
- Username: `ev_admin`
- Password: `changeme`
- Database name: `ev_charging`
- Port: `5433`

> [!NOTE]
> Make sure Docker Desktop is running before you run this command!
> You can check by looking for the whale 🐳 icon in your menu bar.

**Verify it's running:**
```bash
docker ps
```
You should see a container named `ev_charging_db` with status `Up`.

---

### Step 4: Set Up the Python Backend 🐍

#### 4a. Create a Virtual Environment (one-time only)

A virtual environment is like a separate box where we install Python packages so they don't mess with your system.

```bash
python3 -m venv venv
```

#### 4b. Activate the Virtual Environment

```bash
source venv/bin/activate
```

> Your terminal prompt should now show `(venv)` at the beginning — that means it's active!

#### 4c. Install Python Dependencies

```bash
pip install -r requirements.txt
```

**What this installs:** FastAPI (web server), pandas (data handling), scikit-learn & XGBoost (AI/ML models), SQLAlchemy (database connector), and more.

> [!NOTE]
> This can take a few minutes the first time — it's downloading a lot of packages including PyTorch.

---

### Step 5: Initialize the Database Tables 📊

```bash
python database/init_db.py
```

**What this does:** Creates all the tables (chargers, users, trips, reviews, etc.) inside your PostgreSQL database.

---

### Step 6: Start the Backend Server 🖥️

```bash
PYTHONPATH=models:api uvicorn api.main:app --reload --port 8000
```

**What this does:**
- Starts the FastAPI backend on `http://localhost:8000`
- `--reload` means it auto-restarts when you change code (handy for development!)
- `PYTHONPATH=models:api` tells Python where to find our code files

**Verify it works:** Open your browser and go to:
- 📖 **API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs) — interactive page where you can test all the API endpoints

> [!IMPORTANT]
> Keep this terminal window open! The server needs to stay running.
> Open a **new terminal tab** (`Cmd + T`) for the next steps.

---

### Step 7: Start the Frontend Dashboard 🎨

Open a **new terminal tab**, then:

```bash
cd /Users/arnav/Documents/Major/Major_Project/ev-charging-project/dashboard
```

#### 7a. Install Frontend Dependencies (one-time only)

```bash
npm install
```

#### 7b. Start the Development Server

```bash
npm run dev
```

**What this does:** Starts the React + Vite frontend on `http://localhost:5173`

**Open in browser:** [http://localhost:5173](http://localhost:5173)

> You should see the VoltGuide dashboard with a map, charger recommendations, trip planner, and station console! 🎉

---

## ✅ Quick Summary — The 3 Things to Run

Once everything is set up, you only need **3 commands in 3 terminals** to start the project:

| Terminal | Command | What It Starts |
|---|---|---|
| **Terminal 1** | `docker compose up -d` | Database |
| **Terminal 2** | `source venv/bin/activate && PYTHONPATH=models:api uvicorn api.main:app --reload --port 8000` | Backend API (port 8000) |
| **Terminal 3** | `cd dashboard && npm run dev` | Frontend UI (port 5173) |

---

## 🧪 Running Tests (Optional)

To verify everything works correctly:

```bash
source venv/bin/activate
cd /Users/arnav/Documents/Major/Major_Project/ev-charging-project
./venv/bin/pytest testbed/ -v
```

---

## 🔍 Project Structure — What's Where

```
ev-charging-project/
├── api/                  ← Backend API (FastAPI routes, endpoints)
│   └── main.py           ← The main server file
├── models/               ← AI/ML models and business logic
│   ├── recommendation_engine.py  ← Charger ranking algorithm
│   ├── energy_xgboost_model.py   ← Energy prediction AI model
│   ├── operational_safety.py     ← Safety & eligibility gate
│   └── station_telemetry.py      ← Live station monitoring
├── dashboard/            ← Frontend React app
│   └── src/              ← React components, pages, styles
├── database/             ← Database schema, models, seed data
├── testbed/              ← All test files
├── data/                 ← CSV datasets
├── hardware/             ← ESP32 IoT hardware code (optional)
├── .env                  ← Secret keys & config (don't share!)
├── docker-compose.yml    ← Database container config
└── requirements.txt      ← Python package list
```

---

## 🛑 How to Stop Everything

| What | Command |
|---|---|
| Stop the backend | Press `Ctrl + C` in Terminal 2 |
| Stop the frontend | Press `Ctrl + C` in Terminal 3 |
| Stop the database | `docker compose down` |
| Deactivate Python env | `deactivate` |

---

## ❓ Common Issues

| Problem | Solution |
|---|---|
| `docker: command not found` | Install Docker Desktop and restart terminal |
| `ModuleNotFoundError` | Make sure venv is activated: `source venv/bin/activate` |
| `port 8000 already in use` | Kill the old process: `lsof -ti:8000 \| xargs kill` |
| `npm: command not found` | Install Node.js: `brew install node` |
| Database connection error | Make sure Docker is running: `docker compose up -d` |
