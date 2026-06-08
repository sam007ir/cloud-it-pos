# Cloud IT POS - Installation Guide (Windows)

This guide explains how to install and run Cloud IT POS on Windows.

## Quick Installation

1. **Download or clone** the project to your computer.

2. **Run the installer**:
   - Double-click on `install.bat`
   - Wait for it to finish (it will create a virtual environment and install all dependencies)

3. **Start the application**:
   - Double-click on `start.bat`, **or**
   - Use the desktop shortcut that was created during installation

4. **Open your browser** and go to:
   ```
   http://127.0.0.1:5000
   ```

5. **First-time setup**:
   - You will be redirected to the Setup Wizard at `/setup`
   - Create your first Branch and Administrator account

**Recommended**: After setup, download and read the "How to Begin" guide:
`app/static/help/Cloud_IT_POS_How_to_Begin_Guide.pdf`
(also available as a direct link from the Dashboard Help card)

---

## Files Included

| File              | Purpose                              |
|-------------------|--------------------------------------|
| `install.bat`     | Installs the application             |
| `start.bat`       | Starts the Cloud IT POS application  |
| `uninstall.bat`   | Removes the virtual environment      |
| `INSTALL.md`      | This installation guide              |

---

## Manual Installation (Advanced)

If you prefer to install manually:

```powershell
cd C:\path\to\cloud-it-pos

# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python run.py
```

---

## Uninstallation

Simply run `uninstall.bat`. This will only remove the virtual environment. Your database and settings will remain safe in the `instance` folder.

---

## Troubleshooting

### "Python is not recognized"
- Install Python 3.10+ from https://www.python.org/downloads/
- Make sure to check **"Add Python to PATH"** during installation.

### Port 5000 is already in use
- Close any other applications using port 5000, or
- Edit `run.py` and change the port number.

### Permission errors
- Right-click on `install.bat` → **Run as administrator**

---

## After Installation

- The application runs locally on your machine.
- All data is stored in the `instance` folder.
- You can move the entire folder to another location if needed (just run `install.bat` again after moving).

## Getting a Completely Fresh Install (Important)

If the system "already has a database" from previous testing or runs and you want a true fresh start (recommended when you say "I want a fresh install"):

1. Stop any running instance of the app (close the black console window).
2. In PowerShell, from inside the `cloud-it-pos` folder, run for the specific database you want to reset:

   ```powershell
   Remove-Item -Force instance\frcs_vms_pos.db
   # or: Remove-Item -Force instance\my_other_company.db
   ```

3. Double-click `start.bat` again, **or** after start visit `http://127.0.0.1:5000/choose-db` to create a completely new named database.

This will delete all previous data for that .db file. The next time you select or land on it, it will redirect to the Setup Wizard with a completely empty, brand new database.

**Multiple databases**: At login (or go directly to /choose-db) you will see a list of all .db files in `instance/`. You can pick any existing one or type a name to "Create a brand new database". Each is fully isolated (own users, data, tax rates, rentals, etc).

You can also find updated instructions in the "How to Begin" PDF guide.

For any issues, please open an issue on the GitHub repository.
